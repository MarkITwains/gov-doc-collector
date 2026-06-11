#!/usr/bin/env python3
import json
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import urljoin

def parse_selector(element, selector: str) -> Optional[str]:
    """解析选择器，支持 @attr 语法获取属性"""
    if '@' in selector:
        path, attr = selector.rsplit('@', 1)
        elem = element.select_one(path) if path else element
        return elem.get(attr) if elem else None
    return element.select_one(selector).get_text(strip=True) if element.select_one(selector) else None

def extract_items(html_content: str, config: Dict) -> List[Dict]:
    """从 HTML 提取列表项"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')

    items = []
    selectors = config['selectors']
    base_url = config['base_url']

    for elem in soup.select(selectors['list']):
        item = {}
        for key in ['title', 'link', 'date']:
            if key in selectors:
                value = parse_selector(elem, selectors[key])
                if key == 'link' and value:
                    value = urljoin(base_url, value)
                item[key] = value

        # 过滤条件：必须有标题和链接，且标题长度>10
        if item.get('title') and item.get('link') and len(item['title']) > 10:
            items.append(item)

    return items

def parse_xml_feed(xml_content: str) -> List[Dict]:
    """解析 XML RSS/Atom feed"""
    root = ET.fromstring(xml_content)
    items = []

    for item in root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry'):
        entry = {
            'title': item.findtext('title') or item.findtext('{http://www.w3.org/2005/Atom}title'),
            'link': item.findtext('link') or item.find('{http://www.w3.org/2005/Atom}link').get('href') if item.find('{http://www.w3.org/2005/Atom}link') is not None else None,
            'date': item.findtext('pubDate') or item.findtext('{http://www.w3.org/2005/Atom}published')
        }
        if entry['title']:
            items.append(entry)

    return items

def parse_json_api(json_content: str, mappings: Dict) -> List[Dict]:
    """解析 JSON API 响应"""
    data = json.loads(json_content)

    items_path = mappings.get('items_path', 'data')
    items_data = data
    for key in items_path.split('.'):
        items_data = items_data.get(key, [])

    items = []
    for item in items_data:
        entry = {}
        for target, source in mappings.get('fields', {}).items():
            value = item
            for key in source.split('.'):
                value = value.get(key) if isinstance(value, dict) else None
            entry[target] = value
        items.append(entry)

    return items
