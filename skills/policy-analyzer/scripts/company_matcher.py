#!/usr/bin/env python3
"""
企业适配性匹配器 (policy-analyzer)
将企业画像(profile)与 policy_parser 解析出的结构化条件逐条比对,
输出 eligible / likely / uncertain / ineligible 四档结论 + 逐条核对明细。

匹配语义:
- pass    画像字段满足条件
- fail    画像字段不满足硬性条件
- soft_fail 不满足软性条件(优先/鼓励类) → 降分不否决
- unknown 画像缺少该字段 → 计入 missing_fields
- review  语义条件(行业契合等) → 交给上层 Agent 复核
"""
import json
import re
from typing import Dict, List, Optional

# 企业画像 schema(所有字段可选;字段越全,结论越确定)
PROFILE_SCHEMA = {
    'name': '企业名称 (str)',
    'region': '注册地,如 "深圳市南山区" (str)',
    'industry': '所属行业/主营业务,如 "工业软件研发" (str)',
    'company_age': '成立年限,年 (float)',
    'established': '成立日期 "YYYY-MM-DD",有则自动算 company_age (str)',
    'revenue': '上年度营业收入,万元 (float)',
    'headcount': '从业人员数 (int)',
    'registered_capital': '注册资本,万元 (float)',
    'rd_ratio': '研发费用占营收比例,% (float)',
    'rd_staff_ratio': '研发人员占比,% (float)',
    'patents': '有效发明专利数 (int)',
    'qualifications': '已有资质列表,如 ["高新技术企业"] (list)',
    'credit_clean': '信用记录是否干净(无失信/重大处罚) (bool)',
}

# 单位换算到画像基准单位
_UNIT_TO_WAN = {'万元': 1.0, '亿元': 10000.0, '元': 0.0001}

# 资质别名(政策提法 → 画像可能的写法)
QUAL_ALIASES = {
    '专精特新“小巨人”': ['小巨人', '专精特新小巨人'],
    '专精特新小巨人': ['小巨人', '专精特新“小巨人”'],
    '小巨人': ['专精特新小巨人', '专精特新“小巨人”'],
    '高新技术企业': ['国家高新技术企业', '国高新', '高企'],
    '中小企业': ['小微企业', '中小微企业', '中小型企业', '小中企业', '微型企业', '小型企业', '中型企业'],
    '小微企业': ['中小企业', '中小微企业', '小微'],
    '中小微企业': ['中小企业', '小微企业', '中小型企业'],
    '微型企业': ['中小企业', '小微企业', '小型企业'],
    '小型企业': ['中小企业', '微型企业', '小微企业'],
    '中型企业': ['中小企业', '中型', '中企业'],
    '科技型中小企业': ['科技型小微企业', '科小企业', '科技企业'],
}


def _has_qual(profile_quals: List[str], wanted: str) -> bool:
    cands = [wanted] + QUAL_ALIASES.get(wanted, [])
    for q in profile_quals:
        for c in cands:
            if c in q or q in c:
                return True
    return False


def _compare(op: str, actual: float, expected: float) -> bool:
    return {'>=': actual >= expected, '>': actual > expected,
            '<=': actual <= expected, '<': actual < expected}.get(op, False)


def _to_profile_unit(field: str, value: float, unit: Optional[str]) -> Optional[float]:
    """把条件值换算到画像基准单位(金额→万元,时间→年)。"""
    if value is None:
        return None
    if field in ('revenue', 'registered_capital') and unit in _UNIT_TO_WAN:
        return value * _UNIT_TO_WAN[unit]
    if field == 'company_age' and unit == '个月':
        return value / 12.0
    return value


def _company_age(profile: Dict) -> Optional[float]:
    if profile.get('company_age') is not None:
        return float(profile['company_age'])
    est = profile.get('established')
    if est:
        m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(est))
        if m:
            # 以解析时传入的 as_of 为准;缺省按 profile['as_of'] 或不算
            as_of = profile.get('as_of')
            if as_of:
                m2 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(as_of))
                if m2:
                    days = (int(m2.group(1)) - int(m.group(1))) * 365 + \
                           (int(m2.group(2)) - int(m.group(2))) * 30 + \
                           (int(m2.group(3)) - int(m.group(3)))
                    return round(days / 365.0, 2)
    return None


def check_condition(cond: Dict, profile: Dict) -> Dict:
    """单条结构化条件 vs 企业画像 → {status, reason}"""
    field = cond.get('field')
    op = cond.get('op')
    value = cond.get('value')
    unit = cond.get('unit')
    hard = cond.get('hard', True)

    def fail_status():
        return 'fail' if hard else 'soft_fail'

    # 语义类 → Agent 复核
    if cond.get('needs_llm_review') or field in ('industry', 'other'):
        return {'status': 'review',
                'reason': '语义条件,需结合企业主营业务人工/LLM 判断'}

    if field == 'qualification':
        quals = profile.get('qualifications')
        if quals is None:
            return {'status': 'unknown', 'reason': '画像缺少 qualifications'}
        wanted = value if isinstance(value, list) else [value]
        # 条件文本含"或"→任一命中;否则全部命中
        any_mode = '或' in (cond.get('value_text') or '')
        hits = [w for w in wanted if _has_qual(quals, w)]
        ok = bool(hits) if any_mode else len(hits) == len(wanted)
        if ok:
            return {'status': 'pass', 'reason': f"已有资质: {','.join(hits)}"}
        # 资质条件常与专利等并列为"或"关系,留给汇总层判断;单条先报 fail
        return {'status': fail_status(),
                'reason': f"缺少资质: {','.join(w for w in wanted if w not in hits)}"}

    if field == 'credit':
        cc = profile.get('credit_clean')
        if cc is None:
            return {'status': 'unknown', 'reason': '画像缺少 credit_clean'}
        return ({'status': 'pass', 'reason': '信用记录干净'} if cc
                else {'status': fail_status(), 'reason': '存在失信/处罚记录'})

    if field == 'region':
        region = profile.get('region')
        if not region:
            return {'status': 'unknown', 'reason': '画像缺少 region'}
        # "本市/本省"无法仅凭文本确定是哪个市 → 需结合政策发文机关
        return {'status': 'review',
                'reason': f"属地条件「{cond.get('value_text') or cond.get('text', '')[:30]}」,"
                          f"需确认企业注册地({region})是否属于发文机关辖区"}

    if field == 'company_age':
        age = _company_age(profile)
        if age is None:
            return {'status': 'unknown', 'reason': '画像缺少 company_age/established'}
        expected = _to_profile_unit(field, value, unit)
        if expected is None or op not in ('>=', '>', '<=', '<'):
            return {'status': 'review', 'reason': '条件数值未解析,需人工核对'}
        ok = _compare(op, age, expected)
        return {'status': 'pass' if ok else fail_status(),
                'reason': f"成立年限 {age} 年,要求 {op} {expected} 年"}

    if field in ('revenue', 'headcount', 'registered_capital', 'rd_ratio',
                 'rd_staff_ratio', 'patents'):
        actual = profile.get(field)
        if actual is None:
            return {'status': 'unknown', 'reason': f'画像缺少 {field}'}
        expected = _to_profile_unit(field, value, unit)
        if expected is None or op not in ('>=', '>', '<=', '<'):
            return {'status': 'review', 'reason': '条件数值未解析,需人工核对'}
        ok = _compare(op, float(actual), expected)
        unit_label = {'revenue': '万元', 'registered_capital': '万元', 'headcount': '人',
                      'rd_ratio': '%', 'rd_staff_ratio': '%', 'patents': '件'}.get(field, unit or '')
        return {'status': 'pass' if ok else fail_status(),
                'reason': f"{field}={actual}{unit_label},要求 {op} {expected}{unit_label}"}

    return {'status': 'review', 'reason': f'未知字段 {field},需人工核对'}


def _or_group_rescue(checks: List[Dict]) -> None:
    """
    同一条文本拆出的多条条件,若原文含"或",其中一条 pass 即可整组 pass。
    原地把同组其余 fail → pass(标注 reason)。
    """
    by_text: Dict[str, List[Dict]] = {}
    for c in checks:
        by_text.setdefault(c['condition']['text'], []).append(c)
    for text, group in by_text.items():
        if len(group) < 2 or '或' not in text:
            continue
        if any(g['result']['status'] == 'pass' for g in group):
            for g in group:
                if g['result']['status'] in ('fail', 'soft_fail'):
                    g['result']['status'] = 'pass'
                    g['result']['reason'] += '(同条款"或"关系,另一分支已满足)'


# 适用于"申报类"政策的文种(其他文种是法/规划/招聘/吹风会等,不做 triage)
TRIAGE_DOC_TYPES = {'通知', '公告', '办法', '细则', '意见', '方案', '规定', '决定',
                    '指南', '目录', '措施', '规则', '管理办法', '实施细则', '行动方案',
                    '实施方案', '指导意见', '实施意见', '若干措施', '若干政策', '行动计划',
                    '工作要点', '规划', '法', '条例'}  # 法/条例常含具体条款,也可 triage

# 不适合做"申报分流"的文种
NON_TRIAGE_DOC_TYPES = set()  # 由 caller 判定


def match_policy(parsed_policy: Dict, profile: Dict) -> Dict:
    """
    企业画像 vs 解析后的政策 → 适配性报告。

    Returns:
      {
        'policy_title', 'verdict', 'score',
        'checks': [{condition, result:{status, reason}}, ...],
        'summary': {pass/fail/soft_fail/unknown/review 计数},
        'missing_fields': [...],   # 补全这些字段可消除 unknown
        'review_items': [...],     # 需上层 Agent 语义复核的条目
        'funding': [...],          # 透传政策资金条款,报告用
      }
    """
    conditions = parsed_policy.get('conditions', [])
    checks = [{'condition': c, 'result': check_condition(c, profile)} for c in conditions]
    _or_group_rescue(checks)

    summary = {'pass': 0, 'fail': 0, 'soft_fail': 0, 'unknown': 0, 'review': 0}
    missing, reviews = [], []
    for c in checks:
        st = c['result']['status']
        summary[st] += 1
        if st == 'unknown':
            m = re.search(r'画像缺少 ([\w/]+)', c['result']['reason'])
            if m and m.group(1) not in missing:
                missing.append(m.group(1))
        if st == 'review':
            reviews.append({'text': c['condition']['text'],
                            'reason': c['result']['reason']})

    decisive = summary['pass'] + summary['fail']
    if summary['fail'] > 0:
        verdict = 'ineligible'           # 任一硬性条件不满足
    elif not conditions:
        verdict = 'uncertain'            # 没解析出条件,无从判断
    elif summary['unknown'] == 0 and summary['review'] == 0:
        verdict = 'eligible'             # 全部硬性条件确定通过
    elif summary['pass'] > 0 and summary['pass'] >= summary['unknown'] + summary['review']:
        verdict = 'likely'               # 多数已核通过,少量待补/待审
    elif summary['pass'] == 0 and summary['review'] > 0 and summary['review'] == len(checks):
        # 全部都是语义类条件(industry/other)→ 给'likely'让上层 Agent 复核
        verdict = 'likely'
    else:
        verdict = 'uncertain'

    total = max(1, len(checks))
    score = round(100.0 * (summary['pass'] + 0.5 * summary['soft_fail']) / total, 1)

    return {
        'policy_title': parsed_policy.get('title'),
        'doc_number': parsed_policy.get('doc_number'),
        'deadline': (parsed_policy.get('validity') or {}).get('deadline'),
        'verdict': verdict,
        'score': score,
        'summary': summary,
        'checks': checks,
        'missing_fields': missing,
        'review_items': reviews,
        'funding': parsed_policy.get('funding', []),
        'support_measures': parsed_policy.get('support_measures', []),
    }


def match_with_triage(parsed_policy: Dict, profile: Dict) -> Dict:
    """
    分流前置: 若不是'申报类'政策, 直接给 not_applicable; 否则走 match_policy。
    """
    tc = parsed_policy.get('triage_category')
    if tc and tc in ('news', 'regulate', 'other'):
        return {
            'policy_title': parsed_policy.get('title'),
            'doc_number': parsed_policy.get('doc_number'),
            'deadline': (parsed_policy.get('validity') or {}).get('deadline'),
            'triage_category': tc,
            'verdict': 'not_applicable',
            'score': 0.0,
            'summary': {'pass': 0, 'fail': 0, 'soft_fail': 0, 'unknown': 0, 'review': 0},
            'checks': [],
            'missing_fields': [],
            'review_items': [],
            'funding': parsed_policy.get('funding', []),
            'support_measures': parsed_policy.get('support_measures', []),
            'skip_reason': f'triage_category={tc}, 非申报类政策, 跳过匹配',
        }
    return match_policy(parsed_policy, profile)


VERDICT_LABELS = {'eligible': '✅ 符合', 'likely': '🟡 大概率符合',
                  'uncertain': '❓ 信息不足', 'ineligible': '❌ 不符合',
                  'not_applicable': '⏭  不适申报'}
STATUS_MARKS = {'pass': '✓', 'fail': '✗', 'soft_fail': '△', 'unknown': '?', 'review': '⊙'}


def format_report(match: Dict) -> str:
    """匹配结果 → 可读 Markdown 报告。"""
    lines = [f"# 政策适配报告: {match['policy_title']}", '']
    if match.get('doc_number'):
        lines.append(f"- 发文字号: {match['doc_number']}")
    if match.get('deadline'):
        lines.append(f"- 申报截止: **{match['deadline']}**")
    if match.get('triage_category'):
        tc = match['triage_category']
        cat_label = {'apply': '申报/资助类', 'regulate': '规范类(法/条例)',
                     'news': '新闻/会议/招聘', 'other': '其它'}.get(tc, tc)
        lines.append(f"- 文档类别: **{cat_label}**")
    lines.append(f"- 结论: **{VERDICT_LABELS[match['verdict']]}** (符合度 {match['score']}%)")
    s = match['summary']
    lines.append(f"- 条件核对: {s['pass']} 过 / {s['fail']} 不过 / "
                 f"{s['unknown']} 缺信息 / {s['review']} 待复核")
    lines.append('')
    lines.append('## 逐条核对')
    lines.append('')
    lines.append('| | 条件 | 结果 |')
    lines.append('|---|---|---|')
    for c in match['checks']:
        st = c['result']['status']
        lines.append(f"| {STATUS_MARKS[st]} | {c['condition']['text'][:60]} | {c['result']['reason']} |")
    if match['funding']:
        lines.append('')
        lines.append('## 可获支持')
        for f in match['funding']:
            if f['kind'] == 'ratio':
                lines.append(f"- 按比例 {f['ratio_pct']}%: {f['text']}")
            else:
                cap = '(上限)' if f['kind'] == 'cap' else ''
                lines.append(f"- {f['value_wan']:.0f} 万元{cap}: {f['text']}")
    if match['missing_fields']:
        lines.append('')
        lines.append(f"## 待补企业信息\n\n补全后可确定结论: `{'`, `'.join(match['missing_fields'])}`")
    if match['review_items']:
        lines.append('')
        lines.append('## 需人工/LLM 复核')
        for r in match['review_items']:
            lines.append(f"- {r['text'][:60]} — {r['reason']}")
    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    from pathlib import Path
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, str(Path(__file__).parent))
    from policy_parser import parse_policy

    sample_policy = """市工信局关于组织申报2026年度专精特新中小企业培育资助的通知

一、支持对象
在本市行政区域内注册登记、具有独立法人资格的中小企业。

二、申报条件
申报企业须同时满足以下条件:
(一)在本市注册成立满2年,且上年度营业收入不低于1000万元;
(二)从业人员不超过500人,研发费用占营业收入比例不低于4%;
(三)拥有有效发明专利2件以上,或获得高新技术企业、科技型中小企业认定;
(四)未被列入严重违法失信名单,近三年无重大安全生产事故。

三、支持标准
经认定的企业,给予一次性奖励50万元;对首次获评国家级专精特新"小巨人"的,
按其上年度研发投入的30%给予补助,最高不超过500万元。

五、其他
申报截止时间为2026年7月31日。
"""
    profile = {
        'name': '深圳市某某智能科技有限公司',
        'region': '深圳市南山区',
        'industry': '工业软件研发',
        'company_age': 4.5,
        'revenue': 3200,        # 万元
        'headcount': 120,
        'rd_ratio': 8.5,        # %
        'patents': 1,           # 发明专利仅 1 件
        'qualifications': ['国家高新技术企业'],
        'credit_clean': True,
    }
    parsed = parse_policy(sample_policy)
    m = match_policy(parsed, profile)
    print(format_report(m))
    print()
    print('JSON verdict:', m['verdict'], m['score'])
