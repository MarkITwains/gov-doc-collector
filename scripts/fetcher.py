#!/usr/bin/env python3
import json
import requests
import time
import random
import warnings
from pathlib import Path
from typing import Dict, List
from parser import extract_items, parse_xml_feed, parse_json_api

# 禁用SSL警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

class GovDocFetcher:
    def __init__(self, config_dir: str = None):
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "configs" / "sites"
        self.config_dir = Path(config_dir)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })

    def load_config(self, site_key: str, level: str = "national") -> Dict:
        """加载站点配置"""
        config_file = self.config_dir / f"{level}.json"
        with open(config_file, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        return configs.get(site_key)

    def fetch_list(self, site_key: str, level: str = "national", **kwargs) -> List[Dict]:
        """采集文档列表（带重试）"""
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        url = config['base_url'] + config['search_path']

        # 设置Referer
        self.session.headers['Referer'] = config['base_url']

        # 重试3次
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(random.uniform(1, 3))

                resp = self.session.get(url, timeout=40, verify=False)
                resp.raise_for_status()
                resp.encoding = 'utf-8'

                content_type = resp.headers.get('Content-Type', '')

                if 'json' in content_type:
                    return parse_json_api(resp.text, config.get('json_mappings', {}))
                elif 'xml' in content_type:
                    return parse_xml_feed(resp.text)
                else:
                    return extract_items(resp.text, config)

            except Exception as e:
                if attempt == 2:
                    raise
                continue

        return []

    def fetch_detail(self, url: str) -> Dict:
        """
        获取详情页内容并结构化提取。
        返回字典: {content_text, content_html, word_count, attachments, metadata, has_content, error?}
        """
        from detail_extractor import extract_detail
        try:
            resp = self.session.get(url, timeout=30, verify=False)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            return extract_detail(resp.text, url)
        except Exception as e:
            return {
                'url': url,
                'content_text': '',
                'content_html': '',
                'word_count': 0,
                'attachments': [],
                'metadata': {},
                'has_content': False,
                'error': str(e),
            }

if __name__ == "__main__":
    fetcher = GovDocFetcher()
    items = fetcher.fetch_list("gov_cn", "national")
    for item in items[:5]:
        print(json.dumps(item, ensure_ascii=False, indent=2))
