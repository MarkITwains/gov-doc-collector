#!/usr/bin/env python3
"""增强版采集器 - 处理反爬虫和常见问题"""
import time
import random
from fetcher import GovDocFetcher

class EnhancedFetcher(GovDocFetcher):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 增强请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

    def fetch_list(self, site_key: str, level: str = "national", **kwargs):
        """带重试和延迟的采集"""
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        url = config['base_url'] + config['search_path']

        # 添加Referer
        self.session.headers['Referer'] = config['base_url']

        # 重试3次
        for attempt in range(3):
            try:
                # 随机延迟，避免被识别
                if attempt > 0:
                    time.sleep(random.uniform(2, 5))

                resp = self.session.get(url, timeout=30, verify=False)
                resp.raise_for_status()
                resp.encoding = 'utf-8'

                content_type = resp.headers.get('Content-Type', '')

                if 'json' in content_type:
                    from parser import parse_json_api
                    return parse_json_api(resp.text, config.get('json_mappings', {}))
                elif 'xml' in content_type:
                    from parser import parse_xml_feed
                    return parse_xml_feed(resp.text)
                else:
                    from parser import extract_items
                    return extract_items(resp.text, config)

            except Exception as e:
                if attempt == 2:  # 最后一次重试
                    raise
                continue

        return []

if __name__ == "__main__":
    # 测试增强版
    fetcher = EnhancedFetcher()

    # 测试之前失败的站点
    test_sites = [
        ('miit', 'national'),
        ('mohrss', 'national'),
        ('tianjin', 'provincial')
    ]

    for site_key, level in test_sites:
        try:
            items = fetcher.fetch_list(site_key, level)
            print(f"✓ {site_key}: {len(items)} 条")
        except Exception as e:
            print(f"✗ {site_key}: {str(e)[:50]}")
