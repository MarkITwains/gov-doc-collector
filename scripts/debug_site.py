#!/usr/bin/env python3
"""调试单个站点"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def debug_site(site_key, level='national'):
    fetcher = GovDocFetcher()
    config = fetcher.load_config(site_key, level)

    if not config:
        print(f"错误: 站点 {site_key} 在 {level} 级别未找到")
        return

    print(f"站点: {config['name']}")
    print(f"URL: {config['base_url']}{config['search_path']}")
    print("\n正在获取...")

    try:
        url = config['base_url'] + config['search_path']
        resp = fetcher.session.get(url, timeout=30)
        print(f"状态码: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type')}")
        print(f"内容长度: {len(resp.text)}")

        # 保存HTML用于分析
        filename = f'debug_{level}_{site_key}.html'
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(resp.text)
        print(f"HTML已保存到 {filename}")

        # 尝试解析
        from parser import extract_items
        items = extract_items(resp.text, config)
        print(f"\n提取到 {len(items)} 条记录")

        if items:
            print("\n前3条:")
            for item in items[:3]:
                print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            # 显示选择器匹配情况
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            print(f"\n选择器 '{config['selectors']['list']}' 匹配: {len(soup.select(config['selectors']['list']))} 个元素")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python debug_site.py <site_key> [level]")
        print("  level: national (默认) 或 provincial")
        sys.exit(1)

    site_key = sys.argv[1]
    level = sys.argv[2] if len(sys.argv) > 2 else 'national'
    debug_site(site_key, level)
