#!/usr/bin/env python3
"""示例：采集所有站点最新政策"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def main():
    fetcher = GovDocFetcher()

    print("="*70)
    print("政府文档采集器 - 最新政策汇总")
    print("="*70)

    # 国家级
    print("\n【国家级政策】\n")

    print("▸ 中国政府网")
    items = fetcher.fetch_list('gov_cn', 'national')
    for item in items[:3]:
        print(f"  [{item.get('date', 'N/A')}] {item['title']}")
    print()

    print("▸ 国家发展改革委")
    items = fetcher.fetch_list('ndrc', 'national')
    for item in items[:3]:
        print(f"  [{item.get('date', 'N/A')}] {item['title']}")
    print()

    # 省级
    print("\n【省级政策】\n")

    for province, key in [('北京', 'beijing'), ('上海', 'shanghai'), ('广东', 'guangdong')]:
        print(f"▸ {province}市/省")
        items = fetcher.fetch_list(key, 'provincial')
        for item in items[:2]:
            print(f"  [{item.get('date', 'N/A')}] {item['title'][:60]}")
        print()

    print("="*70)
    print("采集完成！")

if __name__ == "__main__":
    main()
