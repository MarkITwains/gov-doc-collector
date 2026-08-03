#!/usr/bin/env python3
"""列表解析器:HTML 选择器 / XML feed / JSON API 三种格式"""
import json
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import urljoin

ATOM = '{http://www.w3.org/2005/Atom}'

# 标题最短长度(过滤导航/栏目短链接);站点可用 "min_title_len" 覆盖
DEFAULT_MIN_TITLE_LEN = 6

_EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
     'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """日期归一化为 ISO YYYY-MM-DD;无法解析时原样返回(None 保持 None)。

    支持:2026-06-10 / 2026/6/10 / 2026.06.10 / 2026年6月10日 /
         2026-06-10T08:00:00Z / Mon, 01 Jun 2026 00:00:00 GMT / 2026年6月1日
    只有月日(缺年份)的不予臆造,原样返回。
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = re.search(r'(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # RFC822 / RSS pubDate: "Mon, 01 Jun 2026 ..."
    m = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\w*\.?,?\s+(\d{4})\b', s)
    if m and m.group(2).lower()[:3] in _EN_MONTHS:
        return f"{m.group(3)}-{_EN_MONTHS[m.group(2).lower()[:3]]:02d}-{int(m.group(1)):02d}"
    return s


def parse_selector(element, selector: str) -> Optional[str]:
    """解析选择器，支持 @attr 语法获取属性"""
    if '@' in selector:
        path, attr = selector.rsplit('@', 1)
        elem = element.select_one(path) if path else element
        return elem.get(attr) if elem else None
    elem = element.select_one(selector)
    return elem.get_text(strip=True) if elem else None


def extract_items(html_content: str, config: Dict) -> List[Dict]:
    """从 HTML 提取列表项(链接绝对化、日期归一化)"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')

    items = []
    selectors = config['selectors']
    base_url = config['base_url']
    min_title_len = int(config.get('min_title_len', DEFAULT_MIN_TITLE_LEN))

    for elem in soup.select(selectors['list']):
        item = {}
        for key in ['title', 'link', 'date']:
            if key in selectors:
                value = parse_selector(elem, selectors[key])
                if key == 'link' and value:
                    value = urljoin(base_url, value)
                if key == 'date' and value:
                    value = normalize_date(value)
                item[key] = value

        # 过滤条件：必须有标题和链接,标题长度达到阈值(默认 6,可配置)
        if item.get('title') and item.get('link') and len(item['title']) >= min_title_len:
            items.append(item)

    return items


def parse_xml_feed(xml_content: str) -> List[Dict]:
    """解析 XML RSS/Atom feed(兼容 RSS <link> 文本与 Atom <link href> 属性)"""
    root = ET.fromstring(xml_content)
    items = []

    nodes = root.findall('.//item') or root.findall(f'.//{ATOM}entry')
    for item in nodes:
        # link:RSS 是 <link> 元素文本;Atom 是 <link href="..."/> 属性。
        # 注意:不能写成三元一行式,`a or b.get() if cond else None` 的优先级
        # 会把条件套在整个 or 表达式外,导致非 Atom 条目 link 恒为 None。
        link = (item.findtext('link') or '').strip() or None
        if link is None:
            atom_link = item.find(f'{ATOM}link')
            if atom_link is not None:
                link = atom_link.get('href')
        entry = {
            'title': item.findtext('title') or item.findtext(f'{ATOM}title'),
            'link': link,
            'date': normalize_date(item.findtext('pubDate')
                                   or item.findtext(f'{ATOM}published')
                                   or item.findtext(f'{ATOM}updated'))
        }
        if entry['title']:
            items.append(entry)

    return items


def _walk_path(data, path: str):
    """按路径取数,支持 dict key 与数组下标混用。

    例:'data' / 'data.list' / 'result[0].items' / 'data.items[2].title'
    路径不存在或类型不匹配返回 None(而不是抛异常)。
    """
    cur = data
    for part in re.findall(r'[^.\[\]]+|\[\d+\]', path or ''):
        if cur is None:
            return None
        if part.startswith('['):
            idx = int(part[1:-1])
            if isinstance(cur, list) and idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
    return cur


def parse_json_api(json_content: str, mappings: Dict) -> List[Dict]:
    """解析 JSON API 响应。

    mappings 示例:
      {"items_path": "data.result[0].list",
       "fields": {"title": "name", "link": "url", "date": "pub_time"}}
    """
    data = json.loads(json_content)

    items_data = _walk_path(data, mappings.get('items_path', 'data'))
    if items_data is None:
        return []
    if isinstance(items_data, dict):
        items_data = [items_data]
    if not isinstance(items_data, list):
        return []

    items = []
    for item in items_data:
        entry = {}
        for target, source in mappings.get('fields', {}).items():
            entry[target] = _walk_path(item, source)
        items.append(entry)

    return items
