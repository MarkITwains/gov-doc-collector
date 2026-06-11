#!/usr/bin/env python3
"""最终优化测试 - 整合所有改进"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
from pathlib import Path

# 先尝试使用JS渲染版本，如果失败则降级到普通版本
try:
    from js_fetcher import JSFetcher
    use_js = True
    print("✓ 使用JS渲染增强版")
except ImportError:
    from fetcher import GovDocFetcher as JSFetcher
    use_js = False
    print("⚠ Playwright未安装，使用普通版本")
    print("  安装JS支持: pip install playwright && playwright install chromium\n")

def test_all_sites():
    if use_js:
        fetcher = JSFetcher(use_js=True)
    else:
        fetcher = JSFetcher()

    print("="*70)
    print("政府文档采集器 - 优化版全站测试")
    print("="*70)

    total_success = 0
    total_sites = 0
    total_records = 0
    improvements = []

    # 测试国家级
    print("\n【国家部委】\n")
    config_file = Path(__file__).parent.parent / "configs" / "sites" / "national.json"
    with open(config_file, 'r', encoding='utf-8') as f:
        national_sites = json.load(f)

    national_success = 0
    national_records = 0

    for key in sorted(national_sites.keys()):
        try:
            config = national_sites[key]
            need_js = config.get('need_js', False)

            if use_js and need_js:
                items = fetcher.fetch_list(key, 'national', force_js=True)
            else:
                items = fetcher.fetch_list(key, 'national')

            if len(items) > 0:
                status = "✓"
                if need_js:
                    status += " [JS]"
                national_success += 1
                national_records += len(items)
                if need_js and len(items) > 0:
                    improvements.append(f"{config['name']} (JS渲染修复)")
            else:
                status = "⚠"
            print(f"{status:8s} {config['name']:20s} {len(items):4d} 条")
        except Exception as e:
            print(f"✗       {national_sites[key]['name']:20s} 失败")

    # 测试省级
    print("\n【省级政府】\n")
    config_file = Path(__file__).parent.parent / "configs" / "sites" / "provincial.json"
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
            print(f"{status:8s} {config['name']:20s} {len(items):4d} 条")
        except Exception as e:
            print(f"✗       {provincial_sites[key]['name']:20s} 失败")

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

    if improvements:
        print(f"\n✓ 本次优化修复: {len(improvements)} 个站点")
        for imp in improvements:
            print(f"  - {imp}")

    print("="*70)

if __name__ == "__main__":
    test_all_sites()
