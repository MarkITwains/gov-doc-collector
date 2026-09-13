#!/usr/bin/env python3
"""LLM 条件抽取补漏(P1-2)—— 正则的"高召回"补充,而不是替代品。

分工:
- 正则(`policy_parser.classify_condition`)是**高精度、低召回**:命中就准,但覆盖面受版式限制
- LLM 抽取是**高召回**:能读懂非典型表述,但会幻觉
所以这里做三件事保证可信:
1. **原文回验**(必须):每条抽取结果都要给 `quote`,我们回正文做子串校验,对不上直接丢弃
   —— 模型编不出来原文里没有的片段
2. **白名单校验**:field/op 必须在集合内,类型必须自洽
3. **磁盘缓存**:按 (prompt 版本 + 模型 + 正文哈希) 缓存**校验后**的结果,重复解析不重复付费

合并策略由 `policy_parser.merge_llm_conditions` 负责:正则优先,LLM 只补没有的
(field, op, value) 组合,冲突一律保留正则结果。

配置沿用 llm_triage 的环境变量(POLICY_LLM_API_KEY / BASE_URL / MODEL / TIMEOUT),
另加:
    POLICY_LLM_CACHE_DIR   缓存目录(默认 <repo>/.cache/policy_extract)
    POLICY_LLM_CACHE=0     关闭缓存
"""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .llm_triage import chat_json, is_llm_available, resolve_model
except ImportError:  # 直接以脚本方式运行
    from llm_triage import chat_json, is_llm_available, resolve_model

logger = logging.getLogger(__name__)

PROMPT_VERSION = 'v1'          # 改 prompt/校验逻辑时递增,自动让旧缓存失效
TEXT_SNIPPET_MAX = 3000        # 送入 LLM 的正文片段长度
MAX_LLM_CONDITIONS = 15        # 单次最多接受多少条(防模型刷条目)
EXTRACT_MAX_TOKENS = 900

# 字段 → 基准单位(prompt 要求模型按基准单位给数,便于与画像直接比较)
FIELD_UNITS = {
    'company_age': '年',
    'revenue': '万元',
    'registered_capital': '万元',
    'headcount': '人',
    'patents': '件',
    'rd_ratio': '%',
    'rd_staff_ratio': '%',
}
NUMERIC_FIELDS = set(FIELD_UNITS)
NUMERIC_OPS = ('>=', '>', '<=', '<')
SPECIAL_OPS = {'qualification': 'has', 'credit': 'clean', 'region': 'in'}
ALLOWED_FIELDS = tuple(FIELD_UNITS) + ('qualification', 'credit', 'region')

SYSTEM_PROMPT = """你是政府政策文档的结构化抽取助手。只抽取"企业申报条件",不要推断、不要补充常识、不要输出正文里没有的内容。

可用字段(必须从这些里选):
  company_age 成立年限(单位:年)      revenue 营业收入(单位:万元)
  registered_capital 注册资本(单位:万元) headcount 从业人员(单位:人)
  patents 有效发明专利(单位:件)       rd_ratio 研发费用占营收(单位:%)
  rd_staff_ratio 研发人员占比(单位:%)  qualification 资质/认定(值给字符串数组)
  credit 信用要求                      region 属地要求
运算符:
  数值类用 >= > <= < ;qualification 用 has ;credit 用 clean ;region 用 in

硬性要求:
1. 每条必须带 quote:正文中**原样连续出现**的片段(不改字、不合并、不省略)。系统会回原文校验,校验失败会被丢弃。
2. 数值换算到上面标注的单位(如"2亿元"→ 20000 万元;"24个月"→ 2 年)。
3. hard:硬性条件 true;含"优先/鼓励/原则上/视情"的软性表述 false。
4. 只输出 JSON:{"conditions":[{"field","op","value","hard","quote"}]},不要任何额外文字。
没有可抽取的条件时输出 {"conditions":[]}。"""


# ---------- 缓存 ----------

def _cache_dir() -> Path:
    env = os.environ.get('POLICY_LLM_CACHE_DIR')
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / '.cache' / 'policy_extract'


def _cache_enabled() -> bool:
    return os.environ.get('POLICY_LLM_CACHE', '1').strip().lower() not in ('0', 'false', 'no')


def _cache_key(text: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode('utf-8'))
    h.update(b'\x00')
    h.update((model or 'default').encode('utf-8'))
    h.update(b'\x00')
    h.update((text or '').encode('utf-8'))
    return h.hexdigest()


def load_cached(key: str) -> Optional[Dict]:
    if not _cache_enabled():
        return None
    path = _cache_dir() / f'{key}.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        logger.debug('缓存损坏,忽略: %s', path)
        return None


def save_cached(key: str, value: Dict) -> None:
    if not _cache_enabled():
        return
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{key}.json').write_text(
            json.dumps(value, ensure_ascii=False), encoding='utf-8')
    except OSError as e:  # 缓存失败不影响主流程
        logger.debug('缓存写入失败: %s', e)


def clear_cache() -> int:
    """清空抽取缓存,返回删除的文件数。"""
    d = _cache_dir()
    if not d.exists():
        return 0
    n = 0
    for f in d.glob('*.json'):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# ---------- 校验 ----------

_PUNCT_CHARS = (' \t\r\n,。;；、:,：()（）[]【】'
                '\u201c\u201d\u2018\u2019「」『』')
_PUNCT_RE = re.compile('[' + re.escape(_PUNCT_CHARS) + ']')


def _normalize(s: str) -> str:
    """去空白与常见标点,用于 quote 回验(容忍模型补标点/空格)。"""
    return _PUNCT_RE.sub('', s or '')


def verify_quote(quote: str, text: str) -> bool:
    """quote 是否**原样**出现在正文中(先做宽松归一化再比对)。"""
    q = _normalize(quote)
    if len(q) < 4:
        return False
    return q in _normalize(text)


def _to_number(value) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def normalize_item(item: Dict, text: str) -> Optional[Dict]:
    """把模型返回的一条转成 policy_parser 的条件结构;不合法返回 None。"""
    if not isinstance(item, dict):
        return None
    field = str(item.get('field', '')).strip()
    if field not in ALLOWED_FIELDS:
        return None
    quote = str(item.get('quote', '')).strip()
    if not verify_quote(quote, text):
        return None
    hard = bool(item.get('hard', True))
    op = str(item.get('op', '')).strip()
    base = {'text': quote[:200], 'field': field, 'unit': None, 'value_text': quote[:100],
            'hard': hard, 'needs_llm_review': False,
            'any_mode': bool(re.search(r'或者|或(?!者)', quote)),
            'source': 'llm'}

    if field in SPECIAL_OPS:
        want_op = SPECIAL_OPS[field]
        if op and op != want_op:
            return None
        if field == 'qualification':
            v = item.get('value')
            vals = [str(x).strip() for x in v] if isinstance(v, list) else [str(v).strip()]
            vals = [x for x in vals if x]
            if not vals:
                return None
            base.update(op='has', value=vals)
            return base
        base.update(op=want_op, value=None)
        return base

    # 数值类
    if op not in NUMERIC_OPS:
        return None
    num = _to_number(item.get('value'))
    if num is None or num < 0:
        return None
    base.update(op=op, value=num, unit=FIELD_UNITS[field])
    return base


def verify_items(raw_items: List[Dict], text: str) -> List[Dict]:
    """批量校验 + 去重(同 field/op/value 只留一条),上限 MAX_LLM_CONDITIONS。"""
    out, seen = [], set()
    for item in raw_items or []:
        cond = normalize_item(item, text)
        if not cond:
            continue
        v = cond.get('value')
        key = (cond['field'], cond['op'],
               tuple(sorted(v)) if isinstance(v, list) else v)
        if key in seen:
            continue
        seen.add(key)
        out.append(cond)
        if len(out) >= MAX_LLM_CONDITIONS:
            break
    return out


# ---------- 主入口 ----------

def build_messages(title: str, text: str, doc_type: str,
                   existing: Optional[List[Dict]] = None) -> List[Dict]:
    snippet = (text or '')[:TEXT_SNIPPET_MAX]
    known = ''
    if existing:
        lines = [f"  - {c.get('field')} {c.get('op')} {c.get('value')}"
                 for c in existing[:10]]
        known = ('\n正则已抽到的条件(不要重复输出这些):\n' + '\n'.join(lines))
    user = (f"标题: {title or '(无)'}\n文种: {doc_type or '(未识别)'}\n"
            f"正文:\n{snippet}{known}")
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user}]


def extract_conditions_with_llm(title: str, text: str, doc_type: str = '',
                                existing: Optional[List[Dict]] = None,
                                model: Optional[str] = None,
                                **config_overrides) -> Optional[List[Dict]]:
    """用 LLM 抽取申报条件;返回**已校验**的条件列表。

    不可用/解析失败 → None(调用方保持正则结果);没有新条件 → []。
    结果按 (prompt 版本 + 模型 + 正文) 缓存,重复解析不再请求。
    """
    text = text or ''
    if not text.strip() or not is_llm_available(**config_overrides):
        return None
    if model is None:
        model = resolve_model(**config_overrides)

    key = _cache_key(text, model)
    cached = load_cached(key)
    if cached is not None:
        logger.debug('LLM 抽取命中缓存: %s', key[:12])
        return verify_items(cached.get('conditions') or [], text)

    parsed = chat_json(build_messages(title, text, doc_type, existing),
                       max_tokens=EXTRACT_MAX_TOKENS, temperature=0.0,
                       log_tag='LLM extract', **config_overrides)
    if not parsed:
        return None
    raw = parsed.get('conditions')
    if not isinstance(raw, list):
        logger.warning('LLM 抽取返回结构异常(无 conditions 数组)')
        return None
    verified = verify_items(raw, text)
    dropped = len(raw) - len(verified)
    if dropped:
        logger.warning('LLM 抽取 %d 条未通过校验(quote 对不上/字段非法),已丢弃', dropped)
    save_cached(key, {'conditions': [
        {'field': c['field'], 'op': c['op'], 'value': c['value'],
         'hard': c['hard'], 'quote': c['text']} for c in verified]})
    return verified


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s: %(message)s')
    if not is_llm_available():
        print('POLICY_LLM_API_KEY 未配置,跳过。')
        print('校验逻辑自测(不联网):')
        sample = '申报企业须在本市注册成立满2年,上年度营业收入不低于1000万元。'
        demo = [
            {'field': 'company_age', 'op': '>=', 'value': 2, 'hard': True,
             'quote': '成立满2年'},
            {'field': 'revenue', 'op': '>=', 'value': 1000, 'hard': True,
             'quote': '上年度营业收入不低于1000万元'},
            {'field': 'headcount', 'op': '<=', 'value': 500, 'hard': True,
             'quote': '从业人员不超过500人'},   # 正文没有 → 应被丢弃
        ]
        for c in verify_items(demo, sample):
            print('  OK  ', c['field'], c['op'], c['value'])
        sys.exit(0)
