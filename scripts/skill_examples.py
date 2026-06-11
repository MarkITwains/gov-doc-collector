#!/usr/bin/env python3
"""Skill使用示例 - 展示如何在应用中集成gov-doc-collector"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json
from datetime import datetime

def example_1_basic():
    """示例1: 基本使用"""
    print("\n" + "="*60)
    print("示例 1: 基本使用 - 获取发改委最新政策")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    items = fetcher.fetch_list('ndrc', 'national')

    print(f"获取到 {len(items)} 条记录\n")
    for i, item in enumerate(items[:5], 1):
        print(f"{i}. [{item.get('date', 'N/A')}] {item['title']}")
        print(f"   {item['link']}\n")

def example_2_multiple_sources():
    """示例2: 多源采集"""
    print("\n" + "="*60)
    print("示例 2: 多源采集 - 汇总多个部委政策")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    sources = {
        'ndrc': '发改委',
        'mof': '财政部',
        'mee': '生态环境部'
    }

    all_items = []

    for site_key, name in sources.items():
        items = fetcher.fetch_list(site_key, 'national')
        print(f"{name}: {len(items)} 条")
        for item in items:
            item['source'] = name
            all_items.append(item)

    print(f"\n总计: {len(all_items)} 条政策\n")

    # 显示最新3条
    print("最新政策:")
    for item in all_items[:3]:
        print(f"[{item['source']}] {item['title'][:40]}")

def example_3_keyword_filter():
    """示例3: 关键词过滤"""
    print("\n" + "="*60)
    print("示例 3: 关键词过滤 - 查找包含'数字'的政策")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    items = fetcher.fetch_list('ndrc', 'national')

    # 过滤包含关键词的政策
    keyword = '数字'
    filtered = [item for item in items if keyword in item['title']]

    print(f"找到 {len(filtered)} 条包含'{keyword}'的政策:\n")
    for item in filtered:
        print(f"- {item['title']}")
        print(f"  {item['link']}\n")

def example_4_export_json():
    """示例4: 导出数据"""
    print("\n" + "="*60)
    print("示例 4: 导出数据 - 保存为JSON")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    items = fetcher.fetch_list('gov_cn', 'national')

    output = {
        'source': '中国政府网',
        'fetch_time': datetime.now().isoformat(),
        'count': len(items),
        'items': items[:10]  # 只保存前10条
    }

    filename = 'gov_policies.json'
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"已导出 {len(output['items'])} 条记录到 {filename}")

def example_5_provincial():
    """示例5: 省级政府"""
    print("\n" + "="*60)
    print("示例 5: 省级政府 - 获取北京市政策")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    items = fetcher.fetch_list('beijing', 'provincial')

    print(f"获取到 {len(items)} 条记录\n")
    print("最新5条:")
    for i, item in enumerate(items[:5], 1):
        print(f"{i}. {item['title'][:50]}")

def example_6_list_all_sites():
    """示例6: 列出所有可用站点"""
    print("\n" + "="*60)
    print("示例 6: 列出所有可用站点")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    config_file = fetcher.config_dir / "national.json"

    with open(config_file, 'r', encoding='utf-8') as f:
        configs = json.load(f)

    print("国家级站点:\n")
    for i, (key, config) in enumerate(sorted(configs.items()), 1):
        print(f"{i:2d}. {config['name']:20s} ({key})")

def main():
    print("\n" + "="*60)
    print("gov-doc-collector Skill 使用示例")
    print("="*60)

    example_1_basic()
    example_2_multiple_sources()
    example_3_keyword_filter()
    example_4_export_json()
    example_5_provincial()
    example_6_list_all_sites()

    print("\n" + "="*60)
    print("所有示例运行完成")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
