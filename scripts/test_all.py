#!/usr/bin/env python3
"""测试所有配置的政府网站"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def test_site(fetcher, site_key, level):
    """测试单个站点"""
    try:
        config = fetcher.load_config(site_key, level)
        print(f"\n{'='*60}")
        print(f"测试: {config['name']} ({site_key})")
        print(f"URL: {config['base_url']}{config['search_path']}")
        print(f"{'='*60}")

        items = fetcher.fetch_list(site_key, level)
        print(f"✓ 获取到 {len(items)} 条记录")

        if items:
            print("\n最新3条:")
            for item in items[:3]:
                print(f"  [{item.get('date', 'N/A')}] {item['title'][:50]}")
                print(f"    {item['link']}")
        return True
    except Exception as e:
        print(f"✗ 失败: {e}")
        return False

def main():
    fetcher = GovDocFetcher()

    print("\n" + "="*60)
    print("政府文档采集器 - 全站测试")
    print("="*60)

    # 国家级
    print("\n【国家级站点】")
    national_sites = ['ndrc', 'miit', 'gov_cn']
    national_ok = 0
    for site in national_sites:
        if test_site(fetcher, site, 'national'):
            national_ok += 1

    # 省级
    print("\n【省级站点】")
    provincial_sites = ['beijing', 'shanghai', 'guangdong', 'zhejiang']
    provincial_ok = 0
    for site in provincial_sites:
        if test_site(fetcher, site, 'provincial'):
            provincial_ok += 1

    # 汇总
    print("\n" + "="*60)
    print("测试汇总")
    print("="*60)
    print(f"国家级: {national_ok}/{len(national_sites)} 成功")
    print(f"省级: {provincial_ok}/{len(provincial_sites)} 成功")
    print(f"总计: {national_ok + provincial_ok}/{len(national_sites) + len(provincial_sites)} 成功")

if __name__ == "__main__":
    main()
