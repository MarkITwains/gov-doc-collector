#!/usr/bin/env python3
"""测试所有国家部委网站"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def test_site(fetcher, site_key, level):
    """测试单个站点"""
    try:
        config = fetcher.load_config(site_key, level)
        items = fetcher.fetch_list(site_key, level)
        status = "✓" if len(items) > 0 else "⚠"
        print(f"{status} {config['name']:20s} | {len(items):3d} 条")
        return len(items) > 0, len(items)
    except Exception as e:
        print(f"✗ {site_key:20s} | 失败: {str(e)[:40]}")
        return False, 0

def main():
    fetcher = GovDocFetcher()

    print("\n" + "="*60)
    print("国家部委政府网站采集测试")
    print("="*60 + "\n")

    # 加载所有国家级站点
    config_file = fetcher.config_dir / "national.json"
    with open(config_file, 'r', encoding='utf-8') as f:
        all_sites = json.load(f)

    success = 0
    total = len(all_sites)
    total_items = 0

    for site_key in sorted(all_sites.keys()):
        ok, count = test_site(fetcher, site_key, 'national')
        if ok:
            success += 1
            total_items += count

    print("\n" + "="*60)
    print(f"测试完成: {success}/{total} 站点可用")
    print(f"总计获取: {total_items} 条记录")
    print("="*60)

if __name__ == "__main__":
    main()
