#!/usr/bin/env python3
"""整合curl_cffi的混合采集器"""
import json
import warnings
from pathlib import Path
from typing import Dict, List
from fetcher import GovDocFetcher
from parser import extract_items, parse_xml_fed, parse_json_api

warnings.filterwarnings('ignore')

class HybridFetcher(GovDocFetcher):
    """支持curl_cffi浏览器指纹的采集器"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_cffi = True
        try:
            from curl_cffi import requests as cffi_requests
            self.cffi_session = cffi_requests.Session(impersonate="chrome")
        except ImportError:
            self.use_cffi = False
            self.cffi_session = None

    def fetch_list(self, site_key: str, level: str = "national", **kwargs) -> List[Dict]:
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        url = config['base_url'] + config['search_path']

        # 优先尝试curl_cffi
        if self.use_cffi and config.get('use_cffi', False):
            try:
                resp = self.cffi_session.get(url, timeout=25, verify=False, headers={
                    'Referer': config['base_url'],
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                })
                resp.encoding = 'utf-8'
                ct = resp.headers.get('Content-Type', '')

                if 'json' in ct:
                    return parse_json_api(resp.text, config.get('json_mappings', {}))
                elif 'xml' in ct:
                    return parse_xml_feed(resp.text)
                else:
                    return extract_items(resp.text, config)
            except Exception as e:
                print(f"curl_cffi失败 {site_key}: {e}")

        return super().fetch_list(site_key, level, **kwargs)

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    fetcher = HybridFetcher()

    # 测试江西
    from configs.sites import provincial
    items = fetcher.fetch_list('jiangxi', 'provincial')
    print(f'江西: {len(items)} 条')
