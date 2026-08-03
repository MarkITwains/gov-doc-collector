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
import logging
import re
from datetime import date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 兼容包内导入与直接脚本执行两种场景
try:
    from .policy_parser import parse_policy  # noqa: F401  (包内相对导入)
except ImportError:  # 直接以脚本方式运行时回退
    from policy_parser import parse_policy  # type: ignore  # noqa: F401

# 企业画像 schema(所有字段可选;字段越全,结论越确定)
PROFILE_SCHEMA = {
    'name': '企业名称 (str)',
    'region': '注册地,如 "深圳市南山区" (str)',
    'industry': '所属行业/主营业务,如 "工业软件研发" (str)',
    'company_age': '成立年限,年 (float);与 established 二选一',
    'established': '成立日期 "YYYY-MM-DD",有则自动算 company_age (str)',
    'as_of': '计算成立年限的基准日期 "YYYY-MM-DD",缺省取当天 (str)',
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

# ---------- 属地推断(省级/直辖市 + 主要地级市→省映射) ----------
PROVINCE_NAMES = [
    '北京', '天津', '上海', '重庆',
    '河北', '山西', '辽宁', '吉林', '黑龙江', '江苏', '浙江', '安徽',
    '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '海南',
    '四川', '贵州', '云南', '陕西', '甘肃', '青海',
    '内蒙古', '广西', '西藏', '宁夏', '新疆',
]
CITY_TO_PROVINCE = {
    '深圳': '广东', '广州': '广东', '珠海': '广东', '佛山': '广东', '东莞': '广东',
    '杭州': '浙江', '宁波': '浙江', '温州': '浙江',
    '南京': '江苏', '苏州': '江苏', '无锡': '江苏',
    '成都': '四川', '武汉': '湖北', '长沙': '湖南', '郑州': '河南',
    '济南': '山东', '青岛': '山东', '厦门': '福建', '福州': '福建',
    '合肥': '安徽', '南昌': '江西', '太原': '山西', '石家庄': '河北',
    '沈阳': '辽宁', '大连': '辽宁', '长春': '吉林', '哈尔滨': '黑龙江',
    '昆明': '云南', '贵阳': '贵州', '南宁': '广西', '海口': '海南',
    '兰州': '甘肃', '西宁': '青海', '西安': '陕西', '银川': '宁夏',
    '乌鲁木齐': '新疆', '拉萨': '西藏', '呼和浩特': '内蒙古',
}


def _policy_regions(text: str) -> set:
    """从政策发文机关/标题中识别辖区名(省份、直辖市、主要城市)。"""
    found = set()
    for n in PROVINCE_NAMES:
        if n in text:
            found.add(n)
    for c in CITY_TO_PROVINCE:
        if c in text:
            found.add(c)
    return found


def _profile_provinces(region: str) -> set:
    """从画像注册地解析所属省份集合(直接含省名,或经城市映射)。"""
    provs = {n for n in PROVINCE_NAMES if n in region}
    for city, prov in CITY_TO_PROVINCE.items():
        if city in region:
            provs.add(prov)
    return provs


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
    if not est:
        return None
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(est))
    if not m:
        logger.warning('established 格式无法解析: %s', est)
        return None
    try:
        born = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        logger.warning('established 非法日期: %s', est)
        return None
    # as_of 缺省取当天;支持 profile 显式传入基准日(便于复现/测试)
    as_of = profile.get('as_of')
    if as_of:
        m2 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(as_of))
        if m2:
            try:
                ref = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                ref = date.today()
        else:
            ref = date.today()
    else:
        ref = date.today()
    days = (ref - born).days
    if days < 0:
        logger.warning('established 晚于基准日期,company_age=0 (est=%s, as_of=%s)', est, ref)
        return 0.0
    return round(days / 365.0, 2)


def check_condition(cond: Dict, profile: Dict, context: Optional[Dict] = None) -> Dict:
    """单条结构化条件 vs 企业画像 → {status, reason}

    Args:
        context: 政策上下文 {'issuer', 'title'},用于属地条件推断辖区
    """
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
        # 条件含"或"→任一命中;否则全部命中。优先取 classify_condition 标注的 any_mode,
        # 兼容旧数据时回退到 value_text 中是否含"或"
        any_mode = cond.get('any_mode')
        if any_mode is None:
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
        # 用发文机关/标题推断政策辖区;能确定且画像可解析 → 给出确定结论,
        # 否则保持 review 交给上层复核
        ctx_text = ' '.join(filter(
            None, [(context or {}).get('issuer'), (context or {}).get('title')]))
        pol_regions = _policy_regions(ctx_text) if ctx_text else set()
        cond_desc = (cond.get('value_text') or cond.get('text', ''))[:30]
        if not pol_regions:
            return {'status': 'review',
                    'reason': f"属地条件「{cond_desc}」,发文机关未识别出辖区,"
                              f"需确认企业注册地({region})是否在辖区内"}

        pol_provs = {p for p in pol_regions if p in PROVINCE_NAMES}
        pol_cities = {c for c in pol_regions if c in CITY_TO_PROVINCE}
        prof_provs = _profile_provinces(region)
        prof_cities = {c for c in CITY_TO_PROVINCE if c in region}

        if pol_cities:
            # 市级政策:注册城市命中才算通过
            hit = pol_cities & prof_cities
            if hit:
                return {'status': 'pass',
                        'reason': f"属地匹配:政策限「{'、'.join(sorted(hit))}」,"
                                  f"企业注册地 {region}"}
            if prof_cities:
                return {'status': fail_status(),
                        'reason': f"属地不符:政策限「{'、'.join(sorted(pol_cities))}」,"
                                  f"企业注册地 {region}"}
            return {'status': 'review',
                    'reason': f"属地条件「{cond_desc}」(政策限{'、'.join(sorted(pol_cities))}),"
                              f"画像 region({region})未识别出城市,需人工确认"}
        # 省级政策:画像省份(直接含省名或城市→省映射)命中即通过
        hit = pol_provs & prof_provs
        if hit:
            return {'status': 'pass',
                    'reason': f"属地匹配:政策限「{'、'.join(sorted(hit))}」,"
                              f"企业注册地 {region}"}
        if prof_provs:
            return {'status': fail_status(),
                    'reason': f"属地不符:政策限「{'、'.join(sorted(pol_provs))}」,"
                              f"企业注册地 {region}"}
        return {'status': 'review',
                'reason': f"属地条件「{cond_desc}」(政策限{'、'.join(sorted(pol_provs))}),"
                          f"画像 region({region})未识别出省份,需人工确认"}

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
    同一条文本拆出的多条条件,若原文含"或/或者",其中一条 pass 即可整组 pass。
    原地把同组其余 fail → pass(标注 reason)。

    判定依据优先取 classify_condition 标注的 any_mode,兼容旧数据回退到 text 含"或"。
    """
    by_text: Dict[str, List[Dict]] = {}
    for c in checks:
        by_text.setdefault(c['condition']['text'], []).append(c)
    for text, group in by_text.items():
        if len(group) < 2:
            continue
        # 任一条件标注 any_mode 即视为"或"组
        is_or_group = any(g['condition'].get('any_mode') for g in group) or '或' in text
        if not is_or_group:
            continue
        if any(g['result']['status'] == 'pass' for g in group):
            rescued = 0
            for g in group:
                if g['result']['status'] in ('fail', 'soft_fail'):
                    g['result']['status'] = 'pass'
                    g['result']['reason'] += '(同条款"或"关系,另一分支已满足)'
                    rescued += 1
            if rescued:
                logger.debug('"或"关系救援: %d 条 fail→pass (text=%s)', rescued, text[:40])


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
    # 属地条件推断需要政策上下文(发文机关/标题)
    context = {'issuer': parsed_policy.get('issuer'),
               'title': parsed_policy.get('title')}
    checks = [{'condition': c, 'result': check_condition(c, profile, context)}
              for c in conditions]
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

    logger.debug('匹配完成: verdict=%s score=%s pass=%d fail=%d unknown=%d review=%d (title=%s)',
                 verdict, score, summary['pass'], summary['fail'],
                 summary['unknown'], summary['review'],
                 parsed_policy.get('title', '')[:40])

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
        # 截止时间感知:已过期明确提示,避免对着失效政策做申报准备
        m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(match['deadline']))
        if m:
            try:
                dl = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if dl < date.today():
                    lines.append(f"- ⚠️ **已过申报截止日 {(date.today() - dl).days} 天,本期不可申报**")
            except ValueError:
                pass
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
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    # parse_policy 已在模块顶部导入(兼容包内/脚本两种运行方式)

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
