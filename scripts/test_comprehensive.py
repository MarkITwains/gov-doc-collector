#!/usr/bin/env python3
"""综合功能测试"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def test_basic_fetch():
    """测试基本采集功能"""
    print("\n" + "="*60)
    print("测试 1: 基本采集功能")
    print("="*60)

    fetcher = GovDocFetcher()

    # 测试中国政府网
    print("\n采集中国政府网...")
    items = fetcher.fetch_list('gov_cn', 'national')
    print(f"✓ 获取 {len(items)} 条记录")
    if items:
        print(f"  示例: {items[0]['title'][:40]}")

    # 测试发改委
    print("\n采集国家发改委...")
    items = fetcher.fetch_list('ndrc', 'national')
    print(f"✓ 获取 {len(items)} 条记录")
    if items:
        print(f"  示例: {items[0]['title'][:40]}")

def test_all_ministries():
    """测试所有部委"""
    print("\n" + "="*60)
    print("测试 2: 所有国家部委")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    config_file = fetcher.config_dir / "national.json"

    with open(config_file, 'r', encoding='utf-8') as f:
        all_sites = json.load(f)

    success = 0
    failed = []
    empty = []

    for site_key, config in all_sites.items():
        try:
            items = fetcher.fetch_list(site_key, 'national')
            if len(items) > 0:
                print(f"✓ {config['name']:20s} {len(items):3d} 条")
                success += 1
            else:
                print(f"⚠ {config['name']:20s}   0 条")
                empty.append(config['name'])
        except Exception as e:
            print(f"✗ {config['name']:20s} {str(e)[:30]}")
            failed.append(config['name'])

    print(f"\n成功: {success}/{len(all_sites)}")
    if empty:
        print(f"返回0条: {', '.join(empty)}")
    if failed:
        print(f"失败: {', '.join(failed)}")

def test_provincial():
    """测试省级站点"""
    print("\n" + "="*60)
    print("测试 3: 省级政府")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    config_file = fetcher.config_dir / "provincial.json"

    with open(config_file, 'r', encoding='utf-8') as f:
        all_sites = json.load(f)

    for site_key, config in all_sites.items():
        try:
            items = fetcher.fetch_list(site_key, 'provincial')
            status = "✓" if len(items) > 0 else "⚠"
            print(f"{status} {config['name']:20s} {len(items):3d} 条")
        except Exception as e:
            print(f"✗ {config['name']:20s} {str(e)[:30]}")

def test_data_quality():
    """测试数据质量"""
    print("\n" + "="*60)
    print("测试 4: 数据质量检查")
    print("="*60 + "\n")

    fetcher = GovDocFetcher()
    items = fetcher.fetch_list('ndrc', 'national')

    if not items:
        print("✗ 没有数据")
        return

    print(f"检查 {len(items)} 条记录...\n")

    # 检查必需字段
    has_title = sum(1 for item in items if item.get('title'))
    has_link = sum(1 for item in items if item.get('link'))
    has_date = sum(1 for item in items if item.get('date'))

    print(f"标题完整性: {has_title}/{len(items)} ({has_title*100//len(items)}%)")
    print(f"链接完整性: {has_link}/{len(items)} ({has_link*100//len(items)}%)")
    print(f"日期完整性: {has_date}/{len(items)} ({has_date*100//len(items)}%)")

    # 显示示例
    print("\n数据示例:")
    print(json.dumps(items[0], ensure_ascii=False, indent=2))

def main():
    print("\n" + "="*60)
    print("政府文档采集器 - 综合测试")
    print("="*60)

    test_basic_fetch()
    test_all_ministries()
    test_provincial()
    test_data_quality()

    print("\n" + "="*60)
    print("所有测试完成")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
