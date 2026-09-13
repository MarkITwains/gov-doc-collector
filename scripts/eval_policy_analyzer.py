#!/usr/bin/env python3
"""policy-analyzer 评测/回归脚本(P0-3)。

没有评测就没有"命中率"可言 —— 之前只有一次性人工统计。本脚本提供两种模式:

1) **语料模式**(不需要人工标注):跑真实采集语料,输出
   漏斗(triage 分布)→ 字段覆盖率 → 申报类内部覆盖率 → 候选筛选校准,
   并可与基线对比(`eval/baseline.json`),防止改动把指标悄悄打下去。

       python scripts/eval_policy_analyzer.py
       python scripts/eval_policy_analyzer.py --corpus scripts/detail_full_results.json
       python scripts/eval_policy_analyzer.py --update-baseline     # 刷新基线
       python scripts/eval_policy_analyzer.py --fail-on-drop 0.05  # 掉超 5% 就失败(CI)

2) **标注集模式**(有 golden 才有 P/R/F1):逐字段 precision/recall/F1 + 逐条 diff,
   用于把"抽取能力"变成可优化目标。

       python scripts/eval_policy_analyzer.py --golden eval/golden.example.jsonl
       python scripts/eval_policy_analyzer.py --golden eval/golden.example.jsonl --min-f1 0.7

golden JSONL 每行一条:
    {"id": "...", "source": "seed-synthetic|real-labeled",
     "title": "...", "text": "政策正文",
     "use_llm_triage": false,
     "expect": {"triage_category": "apply",
                "conditions": [["revenue", ">=", 1000], ["company_age", ">=", 2]],
                "funding": [["fixed", 50], ["cap", 500]],
                "deadline": "2026-07-31"},
     "profile": {...},            # 可选:给了就算 verdict
     "expect_verdict": "likely"}

condition 值会先归一化再比较(金额→万元、月→年、% 原样),所以 1 亿元与 10000 万元等价。
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(REPO_ROOT / 'skills' / 'policy-analyzer' / 'scripts'))

from policy_parser import parse_policy                      # noqa: E402
from company_matcher import match_policy, match_with_triage  # noqa: E402
from policy_candidate import score_candidate                 # noqa: E402

try:  # P1-1 之后才有
    from detail_extractor import assess_content_quality
except ImportError:  # pragma: no cover
    assess_content_quality = None

DEFAULT_CORPUS = REPO_ROOT / 'scripts' / 'detail_full_results.json'
DEFAULT_BASELINE = REPO_ROOT / 'eval' / 'baseline.json'
DEFAULT_GOLDEN = REPO_ROOT / 'eval' / 'golden.example.jsonl'

FUNNEL_FIELDS = ['doc_number', 'conditions', 'funding', 'support_targets',
                 'support_measures', 'validity']
_MONEY_FIELDS = ('revenue', 'registered_capital')


# ---------- 归一化 ----------

def norm_condition(cond: Dict) -> Optional[Tuple]:
    """条件 → 可比较的 (field, op, value) 归一化键。

    金额统一到万元、时间统一到年、比例原样;资质按集合(顺序无关);
    credit/region 这类无值条件用 (field, op, '') 表示;field=other 跳过。
    """
    field = cond.get('field')
    op = cond.get('op')
    value = cond.get('value')
    unit = cond.get('unit')
    if not field or field == 'other':
        return None
    if field == 'qualification':
        vals = value if isinstance(value, list) else ([value] if value else [])
        if not vals:
            return None
        return ('qualification', op or 'has', '|'.join(sorted(str(v) for v in vals)))
    if field == 'credit':
        return ('credit', op or 'clean', '')
    if field == 'region':
        return ('region', op or 'in', '')
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if field in _MONEY_FIELDS:
        if unit == '亿元':
            v *= 10000
        elif unit == '元':
            v /= 10000
    elif field == 'company_age' and unit == '个月':
        v /= 12
    return (field, op or '', round(v, 2))


def norm_funding(item: Dict) -> Optional[Tuple]:
    if item.get('kind') == 'ratio':
        if item.get('ratio_pct') is None:
            return None
        return ('ratio', round(float(item['ratio_pct']), 2))
    if item.get('value_wan') is None:
        return None
    return (item.get('kind'), round(float(item['value_wan']), 2))


# ---------- 标注集评测 ----------

def _prf(tp: int, fp: int, fn: int) -> Dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {'tp': tp, 'fp': fp, 'fn': fn, 'p': round(p, 3), 'r': round(r, 3),
            'f1': round(f1, 3)}


def evaluate_golden(records: List[Dict], ignore_fields: Tuple[str, ...] = ('other',)
                    ) -> Dict:
    """逐字段 P/R/F1 + 分类准确率 + 逐条 diff。"""
    stats: Dict[str, List[int]] = {}
    triage_ok = triage_total = 0
    verdict_ok = verdict_total = 0
    deadline_ok = deadline_total = 0
    diffs: List[Dict] = []

    def bump(field: str, tp=0, fp=0, fn=0):
        s = stats.setdefault(field, [0, 0, 0])
        s[0] += tp
        s[1] += fp
        s[2] += fn

    for rec in records:
        parsed = parse_policy(rec.get('text', ''), title=rec.get('title', ''),
                              metadata=rec.get('metadata') or {},
                              use_llm_triage=rec.get('use_llm_triage', False))
        expect = rec.get('expect') or {}
        row = {'id': rec.get('id'), 'source': rec.get('source'), 'issues': []}

        # triage
        if 'triage_category' in expect:
            triage_total += 1
            if parsed['triage_category'] == expect['triage_category']:
                triage_ok += 1
            else:
                row['issues'].append(
                    f"triage: 期望 {expect['triage_category']} 实际 {parsed['triage_category']}")

        # conditions(集合比较)
        if 'conditions' in expect:
            got = {norm_condition(c) for c in parsed['conditions']}
            got.discard(None)
            if 'other' in ignore_fields:
                got = {g for g in got if g[0] != 'other'}
            want = {tuple(x) for x in expect['conditions']}
            if 'other' in ignore_fields:
                want = {w for w in want if w[0] != 'other'}
            bump('conditions', tp=len(got & want), fp=len(got - want), fn=len(want - got))
            if got != want:
                row['issues'].append(f"conditions: 缺 {sorted(want - got)} 多 {sorted(got - want)}")

        # funding(集合比较)
        if 'funding' in expect:
            got = {norm_funding(f) for f in parsed['funding']}
            got.discard(None)
            want = {tuple(x) for x in expect['funding']}
            bump('funding', tp=len(got & want), fp=len(got - want), fn=len(want - got))
            if got != want:
                row['issues'].append(f"funding: 缺 {sorted(want - got)} 多 {sorted(got - want)}")

        # 关键单值字段
        if 'deadline' in expect:
            deadline_total += 1
            if parsed['validity'].get('deadline') == expect['deadline']:
                deadline_ok += 1
            else:
                row['issues'].append(
                    f"deadline: 期望 {expect['deadline']} 实际 {parsed['validity'].get('deadline')}")
        if 'doc_number' in expect and parsed['doc_number'] != expect['doc_number']:
            row['issues'].append(f"doc_number: 期望 {expect['doc_number']} 实际 {parsed['doc_number']}")

        # verdict(需 profile)
        if rec.get('profile') and rec.get('expect_verdict'):
            verdict_total += 1
            m = match_with_triage(parsed, rec['profile'])
            if m['verdict'] == rec['expect_verdict']:
                verdict_ok += 1
            else:
                row['issues'].append(f"verdict: 期望 {rec['expect_verdict']} 实际 {m['verdict']}")

        if row['issues']:
            diffs.append(row)

    metrics = {f: _prf(*v) for f, v in stats.items()}
    micro = _prf(sum(v[0] for v in stats.values()),
                 sum(v[1] for v in stats.values()),
                 sum(v[2] for v in stats.values())) if stats else _prf(0, 0, 0)
    return {
        'total': len(records),
        'fields': metrics,
        'micro': micro,
        'triage_accuracy': round(triage_ok / triage_total, 3) if triage_total else None,
        'deadline_accuracy': round(deadline_ok / deadline_total, 3) if deadline_total else None,
        'verdict_accuracy': round(verdict_ok / verdict_total, 3) if verdict_total else None,
        'diffs': diffs,
    }


# ---------- 语料模式 ----------

def corpus_metrics(path: Path, use_llm_triage: bool = False) -> Dict:
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    items = []
    for site in raw.get('results', []):
        for it in site.get('items', []):
            it.setdefault('site_key', site.get('site_key'))
            items.append(it)

    docs = [it for it in items if len(it.get('content_text') or '') >= 300]
    n = len(docs)
    triage: Dict[str, int] = {}
    coverage: Dict[str, int] = {}
    quality: Dict[str, int] = {}
    low_quality_docs: List[Dict] = []
    prefilter_pass = 0
    prefilter_pass_apply = 0   # 候选里真正是申报类的(校准用)
    apply_total = 0
    apply_with_cond = 0
    apply_with_funding = 0

    for it in docs:
        parsed = parse_policy(it['content_text'], title=it.get('title', ''),
                              metadata=it.get('metadata') or {},
                              use_llm_triage=use_llm_triage)
        tc = parsed['triage_category']
        triage[tc] = triage.get(tc, 0) + 1
        if tc == 'apply':
            apply_total += 1
            if parsed['conditions']:
                apply_with_cond += 1
            if parsed['funding']:
                apply_with_funding += 1
        for f in FUNNEL_FIELDS:
            hit = (bool(parsed['conditions']) if f == 'conditions'
                   else bool(parsed['funding']) if f == 'funding'
                   else bool(parsed['support_targets']) if f == 'support_targets'
                   else bool(parsed['support_measures']) if f == 'support_measures'
                   else bool(parsed['doc_number']) if f == 'doc_number'
                   else any(v for v in parsed['validity'].values()))
            if hit:
                coverage[f] = coverage.get(f, 0) + 1
        cand = score_candidate(it.get('title') or '', it.get('url') or '')
        if cand['decision'] == 'apply':
            prefilter_pass += 1
            if tc == 'apply':
                prefilter_pass_apply += 1
        if assess_content_quality is not None:
            q = assess_content_quality(it['content_text'])
            quality[q['level']] = quality.get(q['level'], 0) + 1
            if q['level'] == 'low' and len(low_quality_docs) < 20:
                low_quality_docs.append({
                    'site_key': it.get('site_key'),
                    'title': (it.get('title') or '')[:50],
                    'url': it.get('url'),
                    'flags': q['flags'],
                })

    def pct(v: int) -> float:
        return round(100.0 * v / n, 1) if n else 0.0

    return {
        'corpus': str(path),
        'docs': n,
        'triage': {k: {'count': v, 'pct': pct(v)} for k, v in sorted(
            triage.items(), key=lambda kv: -kv[1])},
        'coverage': {f: {'count': coverage.get(f, 0), 'pct': pct(coverage.get(f, 0))}
                     for f in FUNNEL_FIELDS},
        'apply_share_pct': pct(apply_total),
        'apply_with_conditions_pct': round(100.0 * apply_with_cond / apply_total, 1)
        if apply_total else 0.0,
        'apply_with_funding_pct': round(100.0 * apply_with_funding / apply_total, 1)
        if apply_total else 0.0,
        'candidate_prefilter': {
            'pass': prefilter_pass,
            'pass_pct': pct(prefilter_pass),
            'pass_is_apply': prefilter_pass_apply,
            'precision_vs_triage': round(prefilter_pass_apply / prefilter_pass, 3)
            if prefilter_pass else None,
            'recall_vs_triage': round(prefilter_pass_apply / apply_total, 3)
            if apply_total else None,
        },
        'content_quality': quality,
        'low_quality_docs': low_quality_docs,
        'generated_at': date.today().isoformat(),
    }


# ---------- 输出 ----------

def print_corpus(m: Dict, baseline: Optional[Dict] = None,
                 fail_on_drop: float = 0.0, show_low_quality: bool = False) -> int:
    print(f"== 语料评测: {m['corpus']}")
    print(f"   文档数(正文≥300 字): {m['docs']}   生成于 {m['generated_at']}")
    print('\n-- 漏斗(triage) --')
    for k, v in m['triage'].items():
        print(f"   {k:9s} {v['count']:4d}  ({v['pct']}%)")
    print(f"   申报类占比: {m['apply_share_pct']}%")
    print('\n-- 字段覆盖(全样本) --')
    for f, v in m['coverage'].items():
        b = ((baseline or {}).get('coverage') or {}).get(f, {}).get('pct')
        delta = f"   (基线 {b}%, {v['pct'] - b:+.1f})" if b is not None else ''
        print(f"   {f:16s} {v['count']:4d}  ({v['pct']}%){delta}")
    print('\n-- 申报类内部覆盖 --')
    print(f"   有 conditions: {m['apply_with_conditions_pct']}%"
          f"   有 funding: {m['apply_with_funding_pct']}%")
    cp = m['candidate_prefilter']
    print('\n-- 候选预筛校准(P0-2) --')
    print(f"   通过候选筛: {cp['pass']} ({cp['pass_pct']}%)"
          f"   其中真是申报类: {cp['pass_is_apply']}"
          f"   精确率 {cp['precision_vs_triage']}  召回率 {cp['recall_vs_triage']}")
    if m.get('content_quality'):
        print('\n-- 正文质量(P1-1) --')
        for k, v in m['content_quality'].items():
            print(f"   {k:8s} {v:4d}")
    if show_low_quality and m.get('low_quality_docs'):
        print('\n-- 低质量正文(疑似错采,建议用 find_channels 核对栏目)--')
        for d in m['low_quality_docs']:
            print(f"   [{d['site_key']}] {d['title']}")
            print(f"      {d['url']}")
            print(f"      {'; '.join(d['flags'])}")

    rc = 0
    if baseline and fail_on_drop:
        for f, v in m['coverage'].items():
            b = ((baseline.get('coverage') or {}).get(f) or {}).get('pct')
            if b is not None and b - v['pct'] > fail_on_drop * 100:
                print(f"\n!! 覆盖率回退: {f} {b}% → {v['pct']}%")
                rc = 1
        b_apply = baseline.get('apply_share_pct')
        if b_apply is not None and b_apply - m['apply_share_pct'] > fail_on_drop * 100:
            print(f"\n!! 申报类占比回退: {b_apply}% → {m['apply_share_pct']}%")
            rc = 1
    return rc


def print_golden(m: Dict) -> int:
    print(f"== 标注集评测: {m['total']} 条")
    print(f"   triage 准确率 {m['triage_accuracy']}   "
          f"deadline 准确率 {m['deadline_accuracy']}   verdict 准确率 {m['verdict_accuracy']}")
    print('\n-- 逐字段 P/R/F1 --')
    print(f"   {'field':16s} {'P':>6s} {'R':>6s} {'F1':>6s}  TP/FP/FN")
    for f, v in m['fields'].items():
        print(f"   {f:16s} {v['p']:6.3f} {v['r']:6.3f} {v['f1']:6.3f}  "
              f"{v['tp']}/{v['fp']}/{v['fn']}")
    mic = m['micro']
    print(f"   {'(micro)':16s} {mic['p']:6.3f} {mic['r']:6.3f} {mic['f1']:6.3f}")
    if m['diffs']:
        print(f"\n-- 未完全命中的 {len(m['diffs'])} 条 --")
        for d in m['diffs']:
            print(f"   [{d['id']}] {d['source']}")
            for issue in d['issues']:
                print(f"      - {issue}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='policy-analyzer 评测/回归')
    ap.add_argument('--corpus', default=str(DEFAULT_CORPUS))
    ap.add_argument('--baseline', default=str(DEFAULT_BASELINE))
    ap.add_argument('--update-baseline', action='store_true', help='把当前语料指标写为基线')
    ap.add_argument('--golden', nargs='?', const=str(DEFAULT_GOLDEN), default=None,
                    help='标注集 JSONL(缺省位置 eval/golden.example.jsonl)')
    ap.add_argument('--min-f1', type=float, default=None, help='micro-F1 低于该值则失败')
    ap.add_argument('--fail-on-drop', type=float, default=0.0,
                    help='语料模式:覆盖率/申报占比相对基线掉超过该比例(0.05 即 5 个百分点)则失败')
    ap.add_argument('--use-llm-triage', action='store_true', help='语料模式走 LLM 分流')
    ap.add_argument('--show-low-quality', action='store_true',
                    help='列出低质量正文(疑似错采页面)')
    ap.add_argument('--json', action='store_true', help='输出 JSON(便于 CI 解析)')
    args = ap.parse_args()

    rc = 0
    if args.golden:
        records = [json.loads(line) for line in
                   Path(args.golden).read_text(encoding='utf-8').splitlines()
                   if line.strip() and not line.strip().startswith('//')]
        gm = evaluate_golden(records)
        if args.json:
            print(json.dumps(gm, ensure_ascii=False, indent=2))
        else:
            rc |= print_golden(gm)
        if args.min_f1 is not None and gm['micro']['f1'] < args.min_f1:
            print(f"\n!! micro-F1 {gm['micro']['f1']} < {args.min_f1}")
            rc = 1
        return rc

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f'语料文件不存在: {corpus_path}')
        print('  说明:真实采集快照是本地文件(.gitignore 忽略),不随仓库分发。')
        print('  生成方式(任选):')
        print('    python scripts/test_all_sites.py     # 采集 + 落盘 detail_*.json')
        print('    python scripts/diagnose_sites.py     # 站点诊断也产出快照')
        print('  或跳过语料模式,直接跑标注集:')
        print('    python scripts/eval_policy_analyzer.py --golden')
        return 2

    baseline = None
    bpath = Path(args.baseline)
    if bpath.exists():
        baseline = json.loads(bpath.read_text(encoding='utf-8'))
    cur = corpus_metrics(corpus_path, use_llm_triage=args.use_llm_triage)
    if args.json:
        print(json.dumps(cur, ensure_ascii=False, indent=2))
    else:
        rc |= print_corpus(cur, baseline, args.fail_on_drop, args.show_low_quality)
    if args.update_baseline:
        bpath.parent.mkdir(parents=True, exist_ok=True)
        bpath.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"\n基线已更新: {bpath}")
    return rc


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
