#!/usr/bin/env python3
"""
政策条文解析器 (policy-analyzer)
将政策正文(纯文本)解析为结构化数据:
政策类型、发文字号、章节大纲、支持对象、支持方式、资金额度、
申报条件(结构化)、申报材料、有效期/申报截止等。

设计原则: 只做"确定性"提取(正则+规则),语义模糊的判断(行业契合等)
标记 needs_llm_review 交给上层 Agent。
可直接消费 gov-doc-collector 的 detail 输出(content_text + metadata)。
"""
import re
from typing import Dict, List, Optional

# ---------- 中文数字 ----------
CN_DIGITS = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_num(s: str) -> Optional[float]:
    """中文数字转数值(支持 一~九十九);阿拉伯数字直接转。"""
    s = (s or '').strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    if '十' in s:
        left, _, right = s.partition('十')
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        if (left and left not in CN_DIGITS) or (right and right not in CN_DIGITS):
            return None
        return float(tens * 10 + ones)
    val = 0
    for ch in s:
        if ch not in CN_DIGITS:
            return None
        val = val * 10 + CN_DIGITS[ch]
    return float(val)


# ---------- 文种 ----------
# 排序原则: 越长越具体的放前面(否则"通知"会优先于"通知公告"匹配)
DOC_TYPES = ['管理办法', '实施细则', '行动方案', '实施方案', '指导意见', '实施意见',
             '若干措施', '若干政策', '行动计划', '工作要点', '规划',
             '管理办法', '实施细则',  # 保底冗余
             '法', '条例',  # 单字'法'必须靠后,避免误匹配"办法"
             '通知', '公告', '办法', '细则', '意见', '方案', '规定', '决定',
             '指南', '目录', '措施', '规则']

DOC_TYPE_RULES = [
    # 带书名号《》的文种: "《XX法》" / "《XX条例》" / "《XX办法》"
    (re.compile(r'《[^》]{0,80}法》'), '法'),
    (re.compile(r'《[^》]{0,80}条例》'), '条例'),
    (re.compile(r'《[^》]{0,80}规定》'), '规定'),
    (re.compile(r'《[^》]{0,80}办法》'), '办法'),
    (re.compile(r'《[^》]{0,80}细则》'), '细则'),
    (re.compile(r'《[^》]{0,80}方案》'), '方案'),
    # 标题末尾"XX法"且非"办法"
    (re.compile(r'[一-龥]{2,30}法$'), '法'),
    # 法治/依法治国 等是政治文,不是"法"
]


def detect_doc_type(title: str) -> str:
    title = title or ''
    # 排除: 法治/依法治国 类政治文, 不是法律
    if re.search(r'法治(道路|思想|国家|建设|社会|体系)?|依法治国|法制(建设|宣传)|法治宣讲', title):
        return '其他'
    # 书名号内的内容, 取最后一个书名号组
    inner = re.findall(r'《([^》]+)》', title)
    core = inner[-1] if inner else title
    # 先查书名号/末尾规则(规则按从具体到通用排序)
    for chunk in (core, title):
        if not chunk:
            continue
        # 先用通用关键词(更具体的优先) → 然后才用规则
        # 关键词按长度从长到短排序(已在 DOC_TYPES 里设置)
        # 但是为了"办法/法"能区分,得"管理办法"先于"办法"先于"法"
        for t in DOC_TYPES:
            if not t or t in ('法',):  # 单字'法'留到规则层判定
                continue
            if t in chunk:
                return t
        for pat, name in DOC_TYPE_RULES:
            if pat.search(chunk):
                # 避免"管理办法/实施细则"被单字'法'抢匹配
                if name == '法' and re.search(r'(管理|实施|暂行|试行|工作|考核)办法', chunk):
                    return '办法'
                if name == '法' and re.search(r'(实施|暂行|试行)细则', chunk):
                    return '细则'
                if name == '条例' and re.search(r'(管理|实施|暂行|试行)规定', chunk):
                    return '规定'
                # 避免 "X法" 抢匹配 "X方案" / "X法(2024修订)" 之类
                if name == '方案' and re.search(r'[一-龥]{2,30}法$', chunk):
                    continue
                # 避免"办法/条例"等被单字'法'抢匹配(如"XXX保护办法" → 应该是'办法')
                if name == '法' and re.search(r'[一-龥]{2,30}(保护|管理|监督|处罚|征收|补偿|评估|检查|责任|追究)办法', chunk):
                    return '办法'
                if name == '法' and re.search(r'[一-龥]{2,30}(保护|管理|监督|处罚|征收|补偿)条例', chunk):
                    return '条例'
                # "关于印发《XX计划》的通知" 这类核心是"通知", 不是"法/办法"
                if name in ('法', '条例', '办法', '细则', '规定', '方案', '规划', '意见', '工作要点', '行动计划', '行动方案', '实施方案', '若干措施', '若干政策', '指导意见', '实施意见', '实施细则', '管理办法') and re.search(r'关于.{0,40}的(通知|通报|公告)', chunk) and not re.search(r'^[《\"]?(中华|中华人民共和国|全国|国务院)', chunk):
                    return '通知'
                # "XXX计划" (行动计划/工作要点/规划 等) 应优先于"法/办法"
                if name in ('法', '条例', '办法', '细则', '规定') and re.search(r'(工作要点|立法计划|行动计划|行动方案|实施方案)', chunk):
                    # 保留"立法计划"等,不算"法"
                    return '其他'
                return name
    # 通用关键词(已按长度排序, 优先匹配具体词);"办法" → 单字'法' 这种情形已被前面规则处理过
    for t in DOC_TYPES:
        if not t:
            continue
        if t in title:
            return t
    # 兜底: 含"……办法""……条例"等中文文种词
    if re.search(r'[一-龥]{2,30}(管理办法|实施细则|工作办法|工作细则|实施办法)', title):
        return '办法'
    if re.search(r'[一-龥]{2,30}(保护|管理|监督|处罚|征收|补偿|评估|检查)办法', title):
        return '办法'
    if re.search(r'[一-龥]{2,30}法$', title):
        return '法'
    if re.search(r'[一-龥]{2,30}条例$', title):
        return '条例'
    return '其他'


# ---------- 章节大纲 ----------
SECTION_PATTERNS = [
    (1, re.compile(r'^第([一二三四五六七八九十百\d]{1,4})章\s*(.*)$')),
    (1, re.compile(r'^第([一二三四五六七八九十百\d]{1,4})条\s*(.*)$')),
    (1, re.compile(r'^([一二三四五六七八九十]{1,3})、\s*(.*)$')),
    (2, re.compile(r'^[(（]([一二三四五六七八九十]{1,3})[)）]\s*(.*)$')),
    (3, re.compile(r'^(\d{1,2})[.、]\s*(.*)$')),
    (4, re.compile(r'^[(（](\d{1,2})[)）]\s*(.*)$')),
]


def split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r'\n+', text or '') if p.strip()]


def extract_outline(paragraphs: List[str], max_items: int = 60) -> List[Dict]:
    """提取章节大纲(只保留标题行,不含正文)。"""
    outline = []
    for p in paragraphs:
        head = p[:80]
        for level, pat in SECTION_PATTERNS:
            m = pat.match(head)
            if m:
                title = (m.group(2) or '').strip()
                # 标题应当较短;太长说明该段是"编号+整段正文"
                title = title.split('。')[0][:60]
                outline.append({'level': level, 'marker': m.group(1), 'title': title})
                break
        if len(outline) >= max_items:
            break
    return outline


# ---------- 元数据 ----------
DOC_NUMBER_RE = re.compile(r'[一-龥]{1,8}[〔【\[](\d{4})[〕】\]]\s*第?\s*\d+\s*号')
DATE_RE = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日')


def _fmt_date(m) -> str:
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def extract_doc_number(text: str) -> Optional[str]:
    m = DOC_NUMBER_RE.search(text)
    if m:
        return m.group(0).strip()
    m = re.search(r'(国务院|主席|部)令\s*第\s*(\d+)\s*号', text)
    if m:
        return m.group(0).strip()
    return None


# ---------- 有效期 / 截止 ----------
def extract_validity(text: str) -> Dict:
    v = {'effective_from': None, 'valid_until': None, 'valid_years': None, 'deadline': None}

    m = re.search(r'自' + DATE_RE.pattern + r'起?(?:施行|实施|执行|生效)', text)
    if m:
        v['effective_from'] = _fmt_date(m)
    elif re.search(r'自(印发|发布|公布)之日起(施行|实施|执行|生效)', text):
        v['effective_from'] = '发布之日'

    m = re.search(r'有效期[至截至]\s*' + DATE_RE.pattern, text)
    if m:
        v['valid_until'] = _fmt_date(m)
    if v['valid_until'] is None:
        m = re.search(r'有效期[至截至][^。]{0,15}?为止', text)
        if m:
            inner = DATE_RE.search(m.group(0))
            if inner:
                v['valid_until'] = _fmt_date(inner)

    m = re.search(r'有效期为?\s*([一二三四五六七八九十\d]{1,3})\s*年', text)
    if m:
        n = cn_num(m.group(1))
        if n:
            v['valid_years'] = int(n)

    # 申报截止: "申报截止时间为...日" / "请于...日前报送/提交"
    m = re.search(r'(?:申报|申请|受理|报名)[^。]{0,12}?(?:截止|截至)[^。]{0,8}?' + DATE_RE.pattern, text)
    if not m:
        m = re.search(DATE_RE.pattern + r'(?:前|之前)[^。]{0,15}?(?:报送|提交|申报|报名|上报)', text)
    if m:
        v['deadline'] = _fmt_date(m)
    return v


# ---------- 支持方式 ----------
MEASURE_TAGS = {
    '资金补贴': r'补贴|补助|资助|奖励|奖补|专项资金|扶持资金|拨付',
    '税收优惠': r'税收优惠|减免税|免征|所得税.{0,8}(减免|优惠|扣除)|增值税.{0,8}(减免|优惠)|税前(加计)?扣除',
    '融资支持': r'贷款|贴息|融资|担保|风险补偿|投资基金|股权投资',
    '租金减免': r'租金.{0,6}(减免|补贴|优惠)|免收?租金|免租',
    '人才支持': r'人才(引进|补贴|奖励|公寓|住房)|落户|职称|子女入学',
    '评定授牌': r'认定为?|授牌|评定|示范(企业|项目|基地)|试点(企业|单位)|称号',
    '用地支持': r'用地(保障|支持|指标)|优先供地|土地出让',
    '审批便利': r'绿色通道|优先(办理|审批|受理)|容缺(受理|办理)|一站式',
    '贷款贴息': r'贴息',
    '贷款额度': r'贷款额度.{0,12}(最高|不超过)',
}


def detect_measures(text: str) -> List[str]:
    """识别支持方式(资金/税收/融资/人才/用地等)。"""
    return [tag for tag, pat in MEASURE_TAGS.items() if re.search(pat, text)]


# ---------- 资金额度 ----------
AMOUNT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(亿元|万元)')
# 金额前若是这些词,说明是阈值/规模而非补贴
THRESHOLD_PRE_RE = re.compile(
    r'(营业收入|销售收入|主营业务收入|注册资本|营收|总资产|产值|缴纳|罚款|成本|合同(金)?额)[^。]{0,12}$')
# 贷款/贴息额度类(给贴息时的"贷款额度"是 funding 而非 threshold)
LOAN_QUOTA_RE = re.compile(r'(贷款额度|单户[^。]{0,4}额度|贴息[^。]{0,4}额度|补助[^。]{0,4}额度|奖励[^。]{0,4}金额|补贴[^。]{0,4}金额|资助[^。]{0,4}金额)')
FUNDING_VERB_RE = re.compile(r'补贴|补助|资助|奖励|奖补|支持|给予|资金|贴息|减免|拨付|资助额')


def _norm_amount(value: float, unit: str) -> Optional[float]:
    """统一为万元"""
    if unit == '亿元':
        return value * 10000
    if unit == '万元':
        return value
    return None


def extract_funding(text: str, max_items: int = 20) -> List[Dict]:
    """提取补贴/奖励金额(过滤掉营收/注册资本等阈值类金额)。"""
    out, seen = [], set()
    for m in AMOUNT_RE.finditer(text):
        s, e = m.start(), m.end()
        pre = text[max(0, s - 35):s]
        post = text[e:e + 25]
        if THRESHOLD_PRE_RE.search(pre):
            continue
        # 贷款额度/单户额度 紧邻的金额也算 funding
        loan_match = LOAN_QUOTA_RE.search(pre)
        if not FUNDING_VERB_RE.search(pre + post) and not loan_match:
            continue
        kind = 'cap' if re.search(r'最高|不超过|上限|封顶|不高于', pre[-14:] + post[:8]) else 'fixed'
        ctx = re.sub(r'\s+', '', pre[-25:] + m.group(0) + post[:18])
        key = (kind, m.group(0))
        if key in seen:
            continue
        seen.add(key)
        out.append({'text': ctx, 'kind': kind,
                    'value_wan': _norm_amount(float(m.group(1)), m.group(2))})
        if len(out) >= max_items:
            break

    # 比例类: "按...30%给予补助"
    for m in re.finditer(r'按(?:照)?[^。;;]{0,25}?(\d+(?:\.\d+)?)\s*%', text):
        post = text[m.end():m.end() + 25]
        if not FUNDING_VERB_RE.search(post) and not FUNDING_VERB_RE.search(m.group(0)):
            continue
        ctx = re.sub(r'\s+', '', text[max(0, m.start() - 10):m.end()] + post[:18])
        key = ('ratio', m.group(1))
        if key in seen:
            continue
        seen.add(key)
        out.append({'text': ctx, 'kind': 'ratio', 'ratio_pct': float(m.group(1))})
        if len(out) >= max_items:
            break
    return out


# ---------- 支持对象 ----------
TARGET_HEADER_RE = re.compile(
    r'(支持对象|申报对象|补助对象|资助对象|奖励对象|适用范围|适用对象|申报主体|认定范围|征集对象|扶持对象)')


def extract_support_targets(paragraphs: List[str]) -> List[str]:
    targets = []
    for i, p in enumerate(paragraphs):
        m = TARGET_HEADER_RE.search(p[:40])
        if not m:
            continue
        # 标题段("一、支持对象") → 取下一段
        head_only = re.match(r'^[一二三四五六七八九十]{1,3}、\s*' + TARGET_HEADER_RE.pattern + r'\s*$', p.strip())
        if head_only:
            if i + 1 < len(paragraphs):
                # 下一段可能含子条目 → 整体保留前 300 字
                targets.append(paragraphs[i + 1][:300])
                # 如果下一段还含子条目(2. 3. ...), 也保留
                if i + 2 < len(paragraphs) and re.match(r'^\d{1,2}[.、]', paragraphs[i + 2]):
                    targets[-1] = (targets[-1] + '\n' + paragraphs[i + 2])[:300]
            continue
        tail = p[m.end():].lstrip(':: 。')
        if len(tail) >= 8:
            targets.append(tail[:300])
        elif i + 1 < len(paragraphs):
            targets.append(paragraphs[i + 1][:300])
    # 句级兜底: "本办法适用于..."
    if not targets:
        for p in paragraphs:
            m = re.search(r'本(办法|细则|政策|措施|意见|通知)适用于([^。]{6,200})', p)
            if m:
                targets.append(m.group(2).strip())
                break
    return targets[:5]


# ---------- 申报条件 ----------
COND_HEADER_RE = re.compile(
    r'(申报条件|申请条件|认定条件|支持条件|基本条件|入选条件|奖励条件|资助条件|扶持条件|申报要求'
    r'|支持对象|申报对象|扶持对象|补助对象|资助对象|奖励对象|适用范围|适用对象|申报主体|认定范围|征集对象|支持范围|支持内容|支持方式|支持标准'
    r'|(?:应当?|须|需)同时?(?:具备|符合|满足)|符合(?:下列|以下)[^。]{0,8}条件'
    r'|具备(?:下列|以下)条件|满足(?:下列|以下)[^。]{0,6}条件)')
# 仅括号包裹的中文序号或数字序号算"子条目";裸"一、"视为顶级标题
ENUM_ITEM_RE = re.compile(r'^(?:[(（][一二三四五六七八九十\d]{1,3}[)）]|\d{1,2}[.、](?!\d))')
TOP_HEADING_RE = re.compile(r'^(?:[一二三四五六七八九十]{1,3}、|第[一二三四五六七八九十百\d]{1,4}[章条])')
# 阻断头: 进入条件块时遇到这些就停止; 但"支持内容/支持方式/支持标准" 既是支持块头也是阻断头, 在判定时要小心
STOP_HEADER_RE = re.compile(r'(申报程序|申报流程|申报材料|申报方式|资金拨付|监督管理|附则|实施流程|组织实施|附\s*则)')

# 软性词 → 非硬性条件
SOFT_RE = re.compile(r'优先|鼓励|原则上|酌情|视情|适当')

# 数值比较
NUM_TOKEN = r'(\d+(?:\.\d+)?|[一二三四五六七八九十两]{1,4})'
UNIT_TOKEN = r'(亿元|万元|元|人|名|年|周年|个月|%|件|项|家)'
OP_PRE = {'不低于': '>=', '不少于': '>=', '不小于': '>=', '不得低于': '>=', '达到': '>=',
          '超过': '>', '高于': '>', '不超过': '<=', '不高于': '<=', '不多于': '<=',
          '不得超过': '<=', '低于': '<', '少于': '<'}
OP_POST = {'以上': '>=', '及以上': '>=', '以下': '<=', '及以下': '<=', '以内': '<='}
_OP_PRE_RE = re.compile('(' + '|'.join(OP_PRE) + r')\s*' + NUM_TOKEN + r'\s*' + UNIT_TOKEN + '?')
_OP_POST_RE = re.compile(NUM_TOKEN + r'\s*' + UNIT_TOKEN + r'?\s*(及?以[上下内])')
_NUM_UNIT_RE = re.compile(NUM_TOKEN + r'\s*' + UNIT_TOKEN)


def extract_numeric(text: str):
    """从条件文本提取 (op, value, unit);找不到返回 (None, None, None)。"""
    m = _OP_PRE_RE.search(text)
    if m:
        return OP_PRE[m.group(1)], cn_num(m.group(2)), m.group(3)
    m = _OP_POST_RE.search(text)
    if m and m.group(3) in OP_POST:
        return OP_POST[m.group(3)], cn_num(m.group(1)), m.group(2)
    m = _NUM_UNIT_RE.search(text)
    if m:
        return '>=', cn_num(m.group(1)), m.group(2)
    return None, None, None


# 资质关键词(可被企业画像 qualifications 命中)
QUALIFICATIONS = [
    '高新技术企业', '专精特新“小巨人”', '专精特新小巨人', '小巨人', '专精特新',
    '科技型中小企业', '瞪羚企业', '瞪羚', '独角兽', '制造业单项冠军', '单项冠军',
    '技术先进型服务企业', '企业技术中心', '工程技术研究中心', '工程研究中心',
    '重点实验室', '博士后(?:科研)?工作站', '院士工作站', '众创空间', '科技企业孵化器',
    '规模以上', '上市公司', '两化融合', '知识产权管理体系', '质量管理体系',
    'ISO9001', 'ISO14001', 'ISO27001', 'ISO45001', 'CMMI', 'ITSS', 'DCMM',
    # 企业划型
    '中型企业', '小型企业', '微型企业', '中小微企业', '中小型企业', '中小企业', '小微企业',
]
QUAL_RE = re.compile('(' + '|'.join(QUALIFICATIONS) + ')')

# 字段检测器: (field, 关键词正则) — 顺序即优先级,一个条件可命中多个字段
FIELD_DETECTORS = [
    ('company_age', re.compile(r'(?:成立|注册|设立)(?:时间|日期)?(?:满|不少于|达到|超过)?\s*' + NUM_TOKEN + r'\s*(年|周年|个月)')),
    ('rd_staff_ratio', re.compile(r'(?:研发|科技|技术)人员[^。]{0,10}占')),
    ('rd_ratio', re.compile(r'(?:研发|研究开发)(?:费用|经费|投入)[^。]{0,15}占')),
    ('revenue', re.compile(r'(?<!占)(?:营业收入|销售收入|主营业务收入|年销售额|年营收)')),
    ('headcount', re.compile(r'从业人员|职工人数|员工人数|在职员工|职工总数|用工人数')),
    ('registered_capital', re.compile(r'注册资本')),
    ('patents', re.compile(r'发明专利|实用新型|软件著作权|授权专利')),
    ('credit', re.compile(r'失信|严重违法|无重大[^。]{0,8}(事故|处罚|违法)|未发生重大|信用(记录|状况|等级)|纳税信用|违法违规')),
    ('region', re.compile(
        r'(?:在|于)?(?:本|我)(?:市|省|区|县|行政区域)[^。]{0,12}?(?:注册|登记|设立)'
        r'|注册(?:地|登记)(?:在|位于)'
        r'|行政区域内(?:依法)?(?:注册|登记|设立)'
        r'|在本区经营'
    )),
    ('industry', re.compile(r'行业|领域|产业(发展|方向|导向|结构)|主营业务|经营范围|从事.{0,12}行业')),
]


def classify_condition(text: str) -> List[Dict]:
    """
    把一条条件文本拆解成结构化条件(可能拆出多条,如"在本市注册且成立满3年")。
    返回 [{text, field, op, value, unit, hard, needs_llm_review}, ...]
    """
    text = text.strip().rstrip(';;。')
    if not text:
        return []
    hard = not SOFT_RE.search(text)
    conds: List[Dict] = []

    def add(field, op=None, value=None, unit=None, review=False, value_text=None):
        conds.append({'text': text[:200], 'field': field, 'op': op, 'value': value,
                      'unit': unit, 'value_text': value_text,
                      'hard': hard, 'needs_llm_review': review})

    # 资质类(可多个)
    quals = QUAL_RE.findall(text)
    if quals:
        uniq = list(dict.fromkeys(quals))
        add('qualification', op='has', value=uniq,
            value_text='或'.join(uniq) if '或' in text else '且'.join(uniq))

    matched_fields = set()
    for field, pat in FIELD_DETECTORS:
        m = pat.search(text)
        if not m or field in matched_fields:
            continue
        matched_fields.add(field)
        if field == 'company_age':
            n = cn_num(m.group(1))
            unit = m.group(2)
            if n is not None:
                val = n / 12.0 if unit == '个月' else n
                add('company_age', op='>=', value=val, unit='年')
            continue
        if field in ('revenue', 'headcount', 'registered_capital', 'rd_ratio',
                     'rd_staff_ratio', 'patents'):
            # 数值通常紧跟在关键词后(向后取 60 字窗口)
            window = text[m.start(): m.end() + 60]
            op, value, unit = extract_numeric(window)
            if value is not None:
                add(field, op=op, value=value, unit=unit)
            else:
                add(field, review=True)
            continue
        if field == 'credit':
            add('credit', op='clean')
            continue
        if field == 'region':
            add('region', op='in', value_text=m.group(0))
            continue
        if field == 'industry':
            # 行业/领域契合是语义判断 → 交给 Agent
            if not quals:  # 资质条件里常带"企业"字样,避免重复
                add('industry', review=True)
            continue

    if not conds:
        add('other', review=True)
    return conds


def split_enum_items(text: str) -> List[str]:
    """把同段内联的 (一)...(二)... / 1....2.... 拆成多条。"""
    marks = [m.start() for m in re.finditer(
        r'[(（][一二三四五六七八九十\d]{1,3}[)）]|(?:(?<=[。;;])|^)\d{1,2}[.、](?!\d)', text)]
    if len(marks) <= 1:
        return [text] if text.strip() else []
    marks.append(len(text))
    items = []
    for a, b in zip(marks, marks[1:]):
        seg = text[a:b].strip(' ;;。')
        if len(seg) >= 6:
            items.append(seg)
    return items


def extract_conditions(paragraphs: List[str], max_items: int = 30) -> List[Dict]:
    """定位"申报条件"块,逐条结构化。找不到时句级兜底。"""
    conditions: List[Dict] = []
    raw_items: List[str] = []

    for i, p in enumerate(paragraphs):
        m = COND_HEADER_RE.search(p)
        if not m:
            continue
        # 标题段("一、支持对象")只取头,不消费
        if TOP_HEADING_RE.match(p) and m.start() < 8:
            # 但若是"一、支持对象"后无内容,直接看下一段
            tail = p[m.end():].lstrip(':: 。')
            if not tail:
                # 把下一段作为内容;但 ENUM 段不再累加
                j = i + 1
                while j < len(paragraphs) and len(raw_items) < max_items:
                    q = paragraphs[j]
                    if TOP_HEADING_RE.match(q) and not ENUM_ITEM_RE.match(q):
                        if not COND_HEADER_RE.search(q[:30]):
                            break
                    if STOP_HEADER_RE.search(q[:30]):
                        break
                    if ENUM_ITEM_RE.match(q):
                        raw_items.extend(split_enum_items(q) or [q])
                    elif COND_HEADER_RE.search(q):
                        break
                    else:
                        if not raw_items and 10 <= len(q) <= 500:
                            raw_items.append(q)
                        elif raw_items and 10 <= len(q) <= 500:
                            raw_items[-1] = (raw_items[-1] + ' ' + q).strip()
                        else:
                            break
                    j += 1
                continue

        # 同段冒号后可能就带条目
        tail = p[m.end():].lstrip(':: 。')
        if tail and len(tail) >= 8:
            raw_items.extend(split_enum_items(tail))
        # 收集后续条目段
        j = i + 1
        while j < len(paragraphs) and len(raw_items) < max_items:
            q = paragraphs[j]
            if TOP_HEADING_RE.match(q) and not ENUM_ITEM_RE.match(q):
                break
            if STOP_HEADER_RE.search(q[:30]):
                break
            if ENUM_ITEM_RE.match(q):
                raw_items.extend(split_enum_items(q) or [q])
            elif COND_HEADER_RE.search(q):
                pass  # 下一个条件块,继续外层循环处理
            else:
                # 非条目段:若紧跟头部且还没条目,可能整段就是条件描述
                if not raw_items and 10 <= len(q) <= 500:
                    raw_items.append(q)
                elif raw_items and 10 <= len(q) <= 500:
                    # 拼接到最后一条(描述性补充)
                    raw_items[-1] = (raw_items[-1] + ' ' + q).strip()
                else:
                    break
            j += 1
        if len(raw_items) >= max_items:
            break

    # 兜底: 全文找"须/应当具备/满足"句
    if not raw_items:
        for p in paragraphs:
            for sent in re.split(r'[。;;]', p):
                if re.search(r'(应当?|必须|须|需)(同时)?(具备|满足|符合)', sent) and len(sent) >= 10:
                    raw_items.append(sent.strip())
                if len(raw_items) >= 8:
                    break
            if len(raw_items) >= 8:
                break

    seen = set()
    for item in raw_items[:max_items]:
        item = re.sub(r'^[(（]?[一二三四五六七八九十\d]{1,3}[)）.、]\s*', '', item).strip()
        # 过滤"以下条件:"之类的头部残句
        if len(item) < 6 or re.match(r'^(?:以下|下列)?(?:全部)?条件[::]?$', item):
            continue
        # 过滤章节标题/发问句/记者提问等
        if re.match(r'^第[一二三四五六七八九十百\d]{1,4}[章节条]', item):
            continue
        if item.endswith('?'):
            continue
        if re.match(r'^[一二三四五六七八九十]{1,3}、', item) and len(item) < 12:
            continue
        # 过滤'招聘/录用'类(属'资格要求'而非'申报条件')
        if re.search(r'中华人民共和国国籍|拥护.{0,8}宪法|四个意识|四个自信|两个维护|具备.{0,8}政治素质|德才兼备|遵纪守法|品行.{0,4}良|无.{0,4}犯罪记录|婚育状况|户口.{0,4}所在地|政治面貌为中共党员|中共正式党员|中共党员(含预备)', item):
            continue
        # 过滤"分配方式/资金管理/法律责任"等流程性条款(不属'申报条件')
        if re.search(r'权重为|主要采取.{0,8}法分配|不得.{0,4}(挤占|挪用|用于)|项目法.{0,8}相结合|原则上.{0,4}通过|结合实际情况|具体分配事宜|按规定程序|按规定使用|根据预算管理', item):
            continue
        if item[:60] in seen:
            continue
        seen.add(item[:60])
        conditions.extend(classify_condition(item))
    return conditions


# ---------- 申报材料 / 程序 ----------
APPLICATION_HEADER_RE = re.compile(r'(申报材料|申请材料|申报程序|申报流程|申报方式|办理流程|申报及审核)')


def extract_application(paragraphs: List[str]) -> Dict:
    app = {'materials': [], 'process_text': None}
    for i, p in enumerate(paragraphs):
        m = APPLICATION_HEADER_RE.search(p[:40])
        if not m:
            continue
        is_material = '材料' in m.group(0)
        # 收集本段尾部 + 后续条目
        chunks = []
        tail = p[m.end():].lstrip(':: ')
        if tail:
            chunks.extend(split_enum_items(tail) or ([tail] if len(tail) > 8 else []))
        j = i + 1
        while j < len(paragraphs) and len(chunks) < 15:
            q = paragraphs[j]
            if TOP_HEADING_RE.match(q) and not ENUM_ITEM_RE.match(q):
                break
            if ENUM_ITEM_RE.match(q):
                chunks.extend(split_enum_items(q) or [q])
                j += 1
            else:
                break
        if is_material:
            for c in chunks:
                c = re.sub(r'^[(（]?[一二三四五六七八九十\d]{1,3}[)）.、]\s*', '', c).strip(' ;;。')
                if 2 <= len(c) <= 120:
                    app['materials'].append(c)
        elif not app['process_text'] and chunks:
            app['process_text'] = ' / '.join(c[:80] for c in chunks[:6])
    app['materials'] = app['materials'][:15]
    return app


# ---------- 主入口 ----------
def parse_policy(text: str, title: str = '', metadata: Optional[Dict] = None) -> Dict:
    """
    解析政策正文。

    Args:
        text: 政策正文(纯文本,推荐用 gov-doc-collector 的 content_text)
        title: 政策标题(可选,不传则取正文首行)
        metadata: gov-doc-collector detail['metadata'](可选,doc_number 等优先采用)

    Returns: 结构化政策 dict(见 skill.md「解析输出」)
    """
    text = (text or '').strip()
    metadata = metadata or {}
    paragraphs = split_paragraphs(text)
    if not title and paragraphs:
        title = paragraphs[0][:80]

    conditions = extract_conditions(paragraphs)
    parsed = {
        'title': title,
        'doc_type': detect_doc_type(title or text[:200]),
        'doc_number': metadata.get('doc_number') or extract_doc_number(text[:3000]),
        'issuer': metadata.get('issuer'),
        'issue_date': metadata.get('issue_date'),
        'validity': extract_validity(text),
        'support_targets': extract_support_targets(paragraphs),
        'support_measures': detect_measures(text),
        'funding': extract_funding(text),
        'conditions': conditions,
        'application': extract_application(paragraphs),
        'outline': extract_outline(paragraphs),
        'stats': {
            'chars': len(text),
            'paragraphs': len(paragraphs),
            'conditions': len(conditions),
        },
    }
    parsed['triage_category'] = _classify_triage(
        parsed['title'], text, conditions, parsed['doc_type'])
    return parsed


# 标题/正文中"非政策"信号(招聘/会议/吹风会/记者会/调研/报道 等)
_NEWS_TITLE_RE = re.compile(
    r'会议|报道|启动|调研|讲话|吹风|访谈|答记者|记者会|发布会|在京举行|会见|会谈'
    r'|签约|欢送|致辞|致辞|致辞|部署|检查|考察|出席|举行|召开|赴.{0,4}调研|开展工作'
    r'|在.{0,6}调研|在.{0,6}考察'
)
# 标题/正文中"招聘/考试/录用"信号
_RECRUIT_TITLE_RE = re.compile(
    r'公开招聘|招聘工作人员|考试录用|公务员|事业单位招聘|招录|校园招聘|联合招聘'
)
# 政策类信号
_POLICY_HINTS_RE = re.compile(
    r'申报|资助|补助|贴息|补贴|奖励|认定|扶持|培育|征集|招标|采购|计划|规划|方案'
)


def _classify_triage(title: str, text: str,
                     conditions: list, doc_type: str) -> str:
    """
    把政策分为四档(用于'政策分流'前置):
    - apply:     申报/资助/奖励类(企业可申报)
    - regulate:  规范类(法/条例/办法,约束企业行为, 不属申报)
    - news:      新闻/会议/招聘/吹风会/调研
    - other:     其它(无法判定)
    """
    if _RECRUIT_TITLE_RE.search(title):
        return 'news'
    if _NEWS_TITLE_RE.search(title):
        return 'news'
    if doc_type in ('法', '条例', '规定', '管理办法', '实施细则'):
        # 法/条例是规范类, 除非有具体的申报条款, 否则不算"申报类"
        if any(re.search(r'申报|资助|补助|奖励|认定', c.get('text','')) for c in conditions):
            return 'apply'
        return 'regulate'
    # 通知/公告/办法/细则 等: 需要双重判断
    if doc_type in ('办法', '细则', '意见', '方案', '指南', '目录',
                    '措施', '决定', '规则', '行动方案', '实施方案', '若干措施',
                    '若干政策', '行动计划', '工作要点', '规划', '指导意见', '实施意见'):
        # "办法"中很多是规范类(如"古树名木保护办法"), 通过标题二次过滤
        if re.search(r'保护办法|管理办法|监督办法|处罚办法|评估办法|检查办法|责任追究办法', title):
            return 'regulate'
        if re.search(r'关于.{0,40}的(立法计划|工作要点)', title):
            return 'regulate'
        # 规划/意见/方案 等需要进一步判断: 有 conditions OR 含申报关键词 → apply
        if conditions:
            return 'apply'
        if re.search(r'申报|资助|补助|贴息|补贴|奖励|认定|扶持|培育|征集|招标|采购', title):
            return 'apply'
        # 国务院级规划/意见/方案 → 规范类
        if re.search(r'国务院|规划$|工作要点$|实施方案$|若干措施$', title):
            return 'regulate'
        return 'other'
    if doc_type in ('通知', '公告'):
        if re.search(r'申报|资助|补助|贴息|补贴|奖励|认定|扶持|培育|征集|招标|采购', title):
            return 'apply'
        if re.search(r'立法工作计划|工作计划|车用.{0,8}价格|价格.{0,4}通知', title):
            return 'other'
        if conditions:
            return 'apply'
        return 'other'
    if _POLICY_HINTS_RE.search(title):
        return 'apply'
    return 'other'


def parse_from_detail(detail: Dict, title: str = '') -> Dict:
    """直接消费 gov-doc-collector 的 fetch_detail() 输出。"""
    return parse_policy(detail.get('content_text', ''), title=title,
                        metadata=detail.get('metadata') or {})


if __name__ == '__main__':
    import json
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sample = """市工信局关于组织申报2026年度专精特新中小企业培育资助的通知

各有关单位:
为加快培育专精特新中小企业,现组织开展2026年度培育资助申报工作。

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

四、申报材料
(一)申报书;
(二)营业执照复印件;
(三)上年度审计报告及纳税证明。

五、其他
申报截止时间为2026年7月31日。本通知自发布之日起施行,有效期3年。
"""
    r = parse_policy(sample)
    print(json.dumps(r, ensure_ascii=False, indent=2))
