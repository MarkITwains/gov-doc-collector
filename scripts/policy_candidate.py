#!/usr/bin/env python3
"""列表级政策候选筛选(P0-2):在**抓详情之前**判断"这份值不值得解析"。

详情抓取是整条链路最贵的一步(每站点还要 curl_cffi/Playwright/requests 三级降级),
而真实语料里绝大多数条目是新闻/会议/招聘/法规(实测 67 篇详情中申报类仅 1 篇)。
先用列表页就有的信息(标题 / 链接 / 栏目)把非申报内容挡在外面。

设计原则:
- **高精度优先**:宁可漏(只影响召回),不要错(浪费抓取 + 污染下游解析)
- 栏目类型(`channel_type`,来自站点配置)比标题更可靠,优先采信
- 纯标准库,可离线单测

用法:
    from policy_candidate import score_candidate, is_apply_candidate
    score_candidate('关于组织申报2026年度专精特新企业的通知', 'https://x.gov.cn/tzgg/a.html')
    # → {'decision': 'apply', 'score': 85, 'reasons': ['标题含申报信号: 申报', ...]}
"""
import re
from typing import Dict, List, Optional

# 栏目类型:apply=申报/资助专区,notice=通知公告,law=法规/政策文件,news=新闻/招聘/活动
CHANNEL_TYPES = ('apply', 'notice', 'law', 'news', 'unknown')

# URL 路径特征 → 栏目类型(顺序敏感:更具体的在前)
URL_CHANNEL_RULES = [
    (re.compile(r'/tzgg/|tongzhigonggao|/notice|/gsgg/|/gonggao|/tzgs/|/gg/'), 'notice'),
    (re.compile(r'shenbao|/zcsb/|/sbzt/|/xmgl/|/xiangmu|/zizhu|/butie|'
                r'/jiangbu|/xmzt/|/qyfw/|/zxfw/'), 'apply'),
    (re.compile(r'/flfg/|fagui|zhengcefagui|/zcfg/|/guizhang|/tiaoli|'
                r'/zhengce/|/zcfb/|/gfxwj/'), 'law'),
    (re.compile(r'/xwzx/|/news|/xwdt/|/gzdt/|/mtbd/|/sph/|/video|/ztbd/'), 'news'),
]

# 标题信号
# 申报/资助类(强信号:出现即大概率是申报入口)
TITLE_APPLY_RE = re.compile(
    r'申报|征集|遴选|奖补|入库|项目储备|推荐申报|组织认定|开展认定|认定工作'
    r'|资助|补助|补贴|奖励|扶持|专项资金|以奖代补|试点(项目|企业|单位)?(申报|征集|遴选)')
# 结果/名单公示类(是申报的**结果**,不是入口)
TITLE_RESULT_RE = re.compile(r'公布|公示|名单|结果|表彰|评选|拟支持|拟认定|获奖')
# 法规/标准类(文种收尾);'法$' 覆盖"XX法",但要放过"办法"(已由 办法$ 处理)
TITLE_LAW_RE = re.compile(r'(法规|条例|办法|规定|细则|规则|标准|指南|规程|法)$'
                          r'|《[^》]{2,40}(条例|办法|规定|细则|标准|规划|法)》')
# 规范/监管类文种("XX保护办法""XX处罚条例"):约束行为,不是申报入口
TITLE_RULE_DOC_RE = re.compile(
    r'(保护|处罚|监管|监督管理|责任追究|禁止|许可|征收|检查|执法|应急预案)'
    r'[^》]{0,6}(办法|条例|规定|细则|规则)')
# 支持类文种("贷款贴息支持办法""产业发展专项资金管理办法"):**就是**申报入口,
# 企业按办法申报(虽属"办法"文种,不能一刀切当规范类)
TITLE_SUPPORT_DOC_RE = re.compile(
    r'(贴息|补贴|补助|奖励|资助|扶持|奖补|资金|专项|培育|认定|试点|示范|纾困|减免)'
    r'[^》]{0,8}(办法|细则|规定|指南|方案|措施)')
# 新闻/会议/招聘/活动类
TITLE_NEWS_RE = re.compile(
    r'会议|召开|举行|调研|考察|讲话|致辞|出席|会见|签约|签署|论坛|峰会'
    r'|发布会|吹风会|记者|座谈|宣讲|培训|活动|仪式|启动|开幕|闭幕'
    r'|招聘|招录|考试录用|录用公示|公务员|事业单位招聘'
    r'|贯彻|学习|传达|解读|综述|评论|访谈')

# 标题过短或纯栏目名(如"通知公告"、"工作职责")不是候选
MIN_TITLE_LEN = 8


def classify_channel_by_url(url: str) -> str:
    """按 URL 路径特征猜栏目类型(免费且比标题稳定)。"""
    u = (url or '').lower()
    if not u:
        return 'unknown'
    for pat, ch in URL_CHANNEL_RULES:
        if pat.search(u):
            return ch
    return 'unknown'


def resolve_channel(url: str, channel_type: Optional[str] = None) -> str:
    """栏目类型优先取站点配置声明,其次按 URL 推断。"""
    ch = (channel_type or '').strip().lower()
    if ch in CHANNEL_TYPES:
        return ch
    return classify_channel_by_url(url)


def score_candidate(title: str, url: str = '',
                    channel_type: Optional[str] = None) -> Dict:
    """给列表条目打分 → {'decision': 'apply'|'reject', 'score': int, 'reasons': [...]}。

    判定顺序(从强到弱):
      1. 栏目声明为申报专区             → apply
      2. 规范/监管类文种(保护办法/处罚条例) → reject
      3. 支持类文种(贴息/资金…办法)      → apply(企业按办法申报)
      4. 标题含申报信号                 → apply
      5. 法规/新闻栏目 + 无申报信号      → reject
      6. 结果公示 / 新闻招聘 / 法规文种   → reject
      7. 其余(无信号)                  → reject(高精度,宁可漏)
    """
    title = (title or '').strip()
    reasons: List[str] = []
    channel = resolve_channel(url, channel_type)

    if channel == 'apply':
        return {'decision': 'apply', 'score': 100, 'channel': channel,
                'reasons': ['栏目声明为申报/资助专区']}

    if len(title) < MIN_TITLE_LEN:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': [f'标题过短({len(title)} 字),疑似栏目名']}

    is_law_doc = bool(TITLE_LAW_RE.search(title))
    apply_hit = TITLE_APPLY_RE.search(title)

    # 规范/监管类文种先毙掉("XX保护办法""XX处罚条例"里没有可申报的钱)
    rule_hit = TITLE_RULE_DOC_RE.search(title)
    if rule_hit:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': [f'规范/监管类文种: {rule_hit.group(0)}']}

    # 支持类文种是申报入口("贷款贴息支持办法""专项资金管理办法")
    support_hit = TITLE_SUPPORT_DOC_RE.search(title)
    if support_hit:
        return {'decision': 'apply', 'score': 80, 'channel': channel,
                'reasons': [f'支持类文种(按办法申报): {support_hit.group(0)}']}

    if channel in ('law', 'news') and not apply_hit:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': [f'栏目类型={channel},标题无申报信号']}

    if apply_hit:
        score = 85 if channel in ('notice', 'apply') else 70
        reasons.append(f'标题含申报信号: {apply_hit.group(0)}')
        if channel == 'notice':
            reasons.append('通知公告栏目,信号增强')
        return {'decision': 'apply', 'score': score, 'channel': channel,
                'reasons': reasons}

    result_hit = TITLE_RESULT_RE.search(title)
    if result_hit:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': [f'标题为结果/名单公示: {result_hit.group(0)}']}

    news_hit = TITLE_NEWS_RE.search(title)
    if news_hit:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': [f'标题为新闻/会议/招聘类: {news_hit.group(0)}']}

    if is_law_doc:
        return {'decision': 'reject', 'score': 0, 'channel': channel,
                'reasons': ['文种为法规/标准,非申报入口']}

    return {'decision': 'reject', 'score': 0, 'channel': channel,
            'reasons': ['标题无申报信号(高精度策略下默认不抓详情)']}


def is_apply_candidate(title: str, url: str = '',
                       channel_type: Optional[str] = None) -> bool:
    """便捷判断:是否值得抓详情解析(仅申报/资助候选)。"""
    return score_candidate(title, url, channel_type)['decision'] == 'apply'


def split_candidates(items: List[Dict], channel_type: Optional[str] = None
                     ) -> tuple:
    """把列表条目拆成 (候选, 跳过) 两批,并给每条打上 `candidate` 标记。

    条目的 link/title 缺失或非 http(s) 链接直接跳过。
    """
    candidates, skipped = [], []
    for it in items or []:
        link = it.get('link') or ''
        if not link.lower().startswith(('http://', 'https://')):
            it['candidate'] = {'decision': 'reject', 'score': 0,
                               'reasons': ['无有效链接']}
            skipped.append(it)
            continue
        info = score_candidate(it.get('title') or '', link, channel_type)
        it['candidate'] = info
        (candidates if info['decision'] == 'apply' else skipped).append(it)
    return candidates, skipped


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    samples = [
        ('关于组织申报2026年度专精特新中小企业培育资助的通知', 'https://x.gov.cn/tzgg/a.html'),
        ('关于开展2026年工业领域数据安全检查工作的通知', 'https://x.gov.cn/tzgg/b.html'),
        ('2026年度重点企业名单公示', 'https://x.gov.cn/tzgg/c.html'),
        ('XX市所属事业单位2026年公开招聘工作人员公告', 'https://x.gov.cn/tzgg/d.html'),
        ('关于印发《XX市产业发展专项资金管理办法》的通知', 'https://x.gov.cn/zcfg/e.html'),
        ('中共中央、国务院印发《教育强国建设规划纲要》', 'https://x.gov.cn/xwzx/f.html'),
        ('关于征集2026年数字化转型试点项目的通知', 'https://x.gov.cn/zwgk/g.html'),
        ('工作职责', 'https://x.gov.cn/gywm/h.html'),
    ]
    for t, u in samples:
        r = score_candidate(t, u)
        print(f"  [{r['decision']:6s}] {r['score']:3d} | {t[:34]:36s} | {'; '.join(r['reasons'])}")
