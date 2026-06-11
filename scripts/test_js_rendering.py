#!/usr/bin/env python3
"""测试JS渲染功能"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from js_fetcher import JSFetcher

# JS渲染站点
JS_SITES = [
    ('miit', 'national', '工业和信息化部'),
    ('mohrss', 'national', '人力资源社会保障部'),
    ('mwr', 'national', '水利部'),
    ('hunan', 'provincial', '湖南省'),
]

def test_js_rendering():
    fetcher = JSFetcher(use_js=True)

    print("="*70)
    print("JS渲染测试")
    print("="*70)

    results = []
    for site_key, level, name in JS_SITES:
        try:
            print(f"\n测试 {name} ({site_key})...")
            items = fetcher.fetch_list(site_key, level, force_js=True)
            status = "✓" if items else "⚠"
            print(f"{status} {name:20s} {len(items):4d} 条")
            results.append((name, len(items), True))

            if items:
                for item in items[:3]:
                    print(f"  - [{item.get('date', 'N/A')}] {item['title'][:50]}")
        except Exception as e:
            print(f"✗ {name:20s} 失败: {str(e)[:60]}")
            results.append((name, 0, False))

    fetcher.close()

    print("\n" + "="*70)
    print("测试汇总")
    print("="*70)
    success = sum(1 for _, count, _ in results if count > 0)
    total_items = sum(count for _, count, _ in results)
    print(f"成功: {success}/{len(JS_SITES)}")
    print(f"总记录: {total_items} 条")

    return success, total_items

if __name__ == '__main__':
    test_js_rendering()
