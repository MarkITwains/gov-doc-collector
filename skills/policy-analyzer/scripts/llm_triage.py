#!/usr/bin/env python3
"""
LLM 政策分流分类器 (policy-analyzer)

用大模型对政策内容做四档分流(apply/regulate/news/other),
替代纯关键词规则的歧义问题。调用 OpenAI 兼容 Chat Completions API。

设计:
- 仅依赖 requests(已在 requirements.txt),不引入 openai SDK
- 配置走环境变量,符合 12-factor:
    POLICY_LLM_BASE_URL  API 基址(默认 https://api.openai.com/v1)
    POLICY_LLM_API_KEY    API Key(未设置则跳过 LLM,由调用方回退规则)
    POLICY_LLM_MODEL      模型名(默认 gpt-4o-mini)
    POLICY_LLM_TIMEOUT    超时秒数(默认 15)
- LLM 不可用/超时/返回异常 → 返回 None,调用方回退到规则分类
- 正文截断到前 1500 字(够判断类别,省 token)
- 要求模型返回严格 JSON,含 category/reason/confidence

可独立测试:
    python llm_triage.py
"""
import json
import logging
import os
import re
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------- 默认配置(可被环境变量覆盖) ----------
DEFAULT_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_MODEL = 'gpt-4o-mini'
DEFAULT_TIMEOUT = 15
TEXT_SNIPPET_MAX = 1500  # 送入 LLM 的正文片段最大字符数

VALID_CATEGORIES = ('apply', 'regulate', 'news', 'other')

# 四档定义(写入 prompt)
CATEGORY_DEFINITIONS = """\
- apply:     申报/资助/奖励类。企业可据此申报获取资金补贴、资质认定、项目扶持等。
             常见:组织申报、开展认定、征集试点示范、专项资金、评价考核(可申报类)。
- regulate:  规范类。法律/条例/管理办法/监管检查/专项整治,约束企业行为,不属申报。
             常见:XX法、XX条例、管理办法、开展检查/核查/整治、贯彻落实法律。
- news:      新闻/会议/招聘/调研/活动报道。非政策性内容,企业无法据此申报。
             常见:工作会议、发布会、记者会、宣讲会、调研考察、签署协议、放假通知、招聘公告。
- other:     无法归入以上三类的其它内容。"""


def _get_config(**overrides) -> Dict:
    """合并配置:参数 > 环境变量 > 默认值。"""
    cfg = {
        'base_url': os.environ.get('POLICY_LLM_BASE_URL', DEFAULT_BASE_URL).rstrip('/'),
        'api_key': os.environ.get('POLICY_LLM_API_KEY', ''),
        'model': os.environ.get('POLICY_LLM_MODEL', DEFAULT_MODEL),
        'timeout': int(os.environ.get('POLICY_LLM_TIMEOUT', str(DEFAULT_TIMEOUT))),
    }
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


def build_prompt(title: str, text: str, doc_type: str,
                 conditions: List[Dict]) -> List[Dict]:
    """构建 chat messages。返回 OpenAI 兼容的 messages 列表。"""
    snippet = (text or '')[:TEXT_SNIPPET_MAX]
    cond_summary = ''
    if conditions:
        # 只给字段+文本摘要,避免 prompt 过长
        cond_lines = [f"  - {c.get('field', '?')}: {c.get('text', '')[:40]}"
                      for c in conditions[:8]]
        cond_summary = '\n已识别条件(参考):\n' + '\n'.join(cond_lines)

    system = (
        "你是政府政策文档分类助手。请把给定政府内容归入四档之一:\n"
        f"{CATEGORY_DEFINITIONS}\n\n"
        "判断依据:标题(最重要)+ 正文片段 + 已识别文种。\n"
        "只返回一个 JSON 对象,不要任何额外文字、不要 markdown 代码块:\n"
        '{"category":"apply|regulate|news|other","reason":"简短理由(不超过30字)","confidence":0.0-1.0}'
    )
    user = (
        f"标题: {title or '(无)'}\n"
        f"文种: {doc_type or '(未识别)'}\n"
        f"正文片段:\n{snippet}{cond_summary}"
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def _extract_json(content: str) -> Optional[Dict]:
    """从模型返回中提取 JSON(兼容 ```json 包裹和多余文字)。"""
    if not content:
        return None
    content = content.strip()
    # 直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 提取 ```json ... ``` 或 ``` ... ``` 包裹
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 提取第一个 {...} 块
    m = re.search(r'\{[^{}]*"category"[^{}]*\}', content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def classify_triage_with_llm(title: str, text: str, doc_type: str,
                             conditions: List[Dict], **config_overrides) -> Optional[Dict]:
    """
    用 LLM 对政策做四档分流。

    Args:
        title: 政策标题
        text: 政策正文
        doc_type: 已识别的文种
        conditions: 已结构化的条件列表
        **config_overrides: base_url/api_key/model/timeout 覆盖

    Returns:
        {'category': str, 'reason': str, 'confidence': float, 'model': str}
        LLM 不可用/出错时返回 None(由调用方回退规则)。
    """
    cfg = _get_config(**config_overrides)
    if not cfg['api_key']:
        logger.debug('POLICY_LLM_API_KEY 未配置,跳过 LLM 分类')
        return None

    messages = build_prompt(title, text, doc_type, conditions)
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }
    payload = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': 0.0,  # 分类任务用确定性输出
        'max_tokens': 200,
        'response_format': {'type': 'json_object'},  # 强制 JSON(支持的模型生效)
    }

    try:
        logger.debug('LLM triage 请求: model=%s, title=%s', cfg['model'], (title or '')[:40])
        resp = requests.post(url, headers=headers, json=payload,
                             timeout=cfg['timeout'])
        resp.raise_for_status()
        data = resp.json()
        content = data['choices'][0]['message']['content']
        parsed = _extract_json(content)
        if not parsed:
            logger.warning('LLM 返回无法解析为 JSON: %s', content[:200])
            return None
        category = str(parsed.get('category', '')).strip().lower()
        if category not in VALID_CATEGORIES:
            logger.warning('LLM 返回未知 category=%s,内容: %s', category, content[:200])
            return None
        return {
            'category': category,
            'reason': str(parsed.get('reason', ''))[:100],
            'confidence': float(parsed.get('confidence', 0.0)),
            'model': cfg['model'],
        }
    except requests.exceptions.Timeout:
        logger.warning('LLM triage 请求超时(%ss),回退规则', cfg['timeout'])
        return None
    except requests.exceptions.RequestException as e:
        logger.warning('LLM triage 请求失败: %s,回退规则', e)
        return None
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning('LLM triage 响应解析失败: %s,回退规则', e)
        return None


def is_llm_available(**config_overrides) -> bool:
    """快速判断 LLM 是否可用(API Key 是否配置)。"""
    cfg = _get_config(**config_overrides)
    return bool(cfg['api_key'])


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s: %(message)s')

    if not is_llm_available():
        print('POLICY_LLM_API_KEY 未配置,无法测试 LLM 调用。')
        print('设置环境变量后重试:')
        print('  $env:POLICY_LLM_API_KEY="sk-..."')
        print('  $env:POLICY_LLM_BASE_URL="https://api.openai.com/v1"')
        print('  $env:POLICY_LLM_MODEL="gpt-4o-mini"')
        sys.exit(0)

    # 用几个样例测试
    samples = [
        ('关于组织申报2026年度专精特新中小企业培育资助的通知',
         '申报条件:在本市注册成立满2年,营收不低于1000万', '通知', [], 'apply'),
        ('我部赴广东省开展调研工作', '赴广东调研企业', '其他', [], 'news'),
        ('关于开展2026年工业领域数据安全检查工作的通知', '开展数据安全检查', '通知', [], 'regulate'),
        ('中华人民共和国数据安全法', '数据安全法条文', '法', [], 'regulate'),
    ]
    for title, text, doc_type, conds, expected in samples:
        result = classify_triage_with_llm(title, text, doc_type, conds)
        if result:
            ok = 'OK' if result['category'] == expected else 'XX'
            print(f"{ok} 期望={expected} 实际={result['category']} "
                  f"(conf={result['confidence']:.2f}) {result['reason']} | {title[:30]}")
        else:
            print(f"-- LLM 返回 None | {title[:30]}")
