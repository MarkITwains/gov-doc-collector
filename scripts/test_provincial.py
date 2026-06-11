#!/usr/bin/env python3
"""测试所有省级政府网站"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def main():
    fetcher = GovDocFetcher()
    config_file = fetcher.config_dir / "provincial.json"

    with open(config_file, 'r', encoding='utf-8') as f:
        all_sites = json.load(f)

    print("\n" + "="*60)
    print("省级政府网站采集测试")
    print("="*60 + "\n")

    success = 0
    total_items = 0

    # 直辖市
    print("【直辖市】\n")
    for key in ['beijing', 'tianjin', 'shanghai', 'chongqing']:
        try:
            config = all_sites[key]
            items = fetcher.fetch_list(key, 'provincial')
            status = "✓" if len(items) > 0 else "⚠"
            print(f"{status} {config['name']:15s} | {len(items):3d} 条")
            if len(items) > 0:
                success += 1
                total_items += len(items)
        except Exception as e:
            print(f"✗ {all_sites[key]['name']:15s} | 失败: {str(e)[:30]}")

    # 省份
    print("\n【省份】\n")
    provinces = [k for k in all_sites.keys() if k not in ['beijing', 'tianjin', 'shanghai', 'chongqing']]
    for key in sorted(provinces):
        try:
            config = all_sites[key]
            items = fetcher.fetch_list(key, 'provincial')
            status = "✓" if len(items) > 0 else "⚠"
            print(f"{status} {config['name']:20s} | {len(items):3d} 条")
            if len(items) > 0:
                success += 1
                total_items += len(items)
        except Exception as e:
            print(f"✗ {all_sites[key]['name']:20s} | 失败: {str(e)[:30]}")

    print("\n" + "="*60)
    print(f"测试完成: {success}/{len(all_sites)} 站点可用")
    print(f"总计获取: {total_items} 条记录")
    print("="*60)

if __name__ == "__main__":
    main()
