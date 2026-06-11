#!/usr/bin/env python3
"""测试所有政府网站（国家级+省级）"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher
import json

def main():
    fetcher = GovDocFetcher()

    print("\n" + "="*70)
    print("政府文档采集器 - 全站点测试")
    print("="*70)

    total_success = 0
    total_sites = 0
    total_records = 0

    # 测试国家级
    print("\n【国家部委】\n")
    config_file = fetcher.config_dir / "national.json"
    with open(config_file, 'r', encoding='utf-8') as f:
        national_sites = json.load(f)

    national_success = 0
    national_records = 0

    for key in sorted(national_sites.keys()):
        try:
            config = national_sites[key]
            items = fetcher.fetch_list(key, 'national')
            if len(items) > 0:
                status = "✓"
                national_success += 1
                national_records += len(items)
            else:
                status = "⚠"
            print(f"{status} {config['name']:20s} {len(items):4d} 条")
        except Exception as e:
            print(f"✗ {national_sites[key]['name']:20s} 失败")

    # 测试省级
    print("\n【省级政府】\n")
    config_file = fetcher.config_dir / "provincial.json"
    with open(config_file, 'r', encoding='utf-8') as f:
        provincial_sites = json.load(f)

    provincial_success = 0
    provincial_records = 0

    for key in sorted(provincial_sites.keys()):
        try:
            config = provincial_sites[key]
            items = fetcher.fetch_list(key, 'provincial')
            if len(items) > 0:
                status = "✓"
                provincial_success += 1
                provincial_records += len(items)
            else:
                status = "⚠"
            print(f"{status} {config['name']:20s} {len(items):4d} 条")
        except Exception as e:
            print(f"✗ {provincial_sites[key]['name']:20s} 失败")

    # 汇总
    total_success = national_success + provincial_success
    total_sites = len(national_sites) + len(provincial_sites)
    total_records = national_records + provincial_records

    print("\n" + "="*70)
    print("测试汇总")
    print("="*70)
    print(f"国家部委: {national_success}/{len(national_sites)} 可用, {national_records} 条记录")
    print(f"省级政府: {provincial_success}/{len(provincial_sites)} 可用, {provincial_records} 条记录")
    print(f"总计: {total_success}/{total_sites} 可用 ({total_success*100//total_sites}%)")
    print(f"总记录数: {total_records} 条")
    print("="*70)

if __name__ == "__main__":
    main()
