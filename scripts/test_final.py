#!/usr/bin/env python3
"""最终全站测试 - 使用统一采集器"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
from pathlib import Path
from unified_fetcher import UnifiedFetcher

CONFIG_DIR = Path(__file__).parent.parent / "configs" / "sites"

def test_all():
    fetcher = UnifiedFetcher()

    results = {'national': {}, 'provincial': {}}

    for level in ['national', 'provincial']:
        with open(CONFIG_DIR / f"{level}.json", encoding='utf-8') as f:
            sites = json.load(f)

        print(f"\n{'='*70}")
        print(f"{'国家部委' if level=='national' else '省级政府'}")
        print('='*70)

        for key in sorted(sites.keys()):
            try:
                # 强制使用JS渲染处理标记need_js的站点
                if sites[key].get('need_js', False):
                    items = fetcher.fetch_list(key, level)
                else:
                    items = fetcher.fetch_list(key, level)
                status = '✓' if items else '⚠'
                print(f"{status} {sites[key]['name']:20s} {len(items):4d} 条")
                results[level][key] = {'name': sites[key]['name'], 'count': len(items), 'status': 'OK' if items else 'EMPTY'}
            except Exception as e:
                print(f"✗ {sites[key]['name']:20s} 失败")
                results[level][key] = {'name': sites[key]['name'], 'count': 0, 'status': str(type(e).__name__)}

    fetcher.close()

    # 汇总
    national_ok = sum(1 for v in results['national'].values() if v['count'] > 0)
    provincial_ok = sum(1 for v in results['provincial'].values() if v['count'] > 0)
    total_ok = national_ok + provincial_ok
    total_sites = len(results['national']) + len(results['provincial'])
    total_records = sum(v['count'] for v in results['national'].values()) + sum(v['count'] for v in results['provincial'].values())

    print(f"\n{'='*70}")
    print("汇总")
    print('='*70)
    print(f"国家部委: {national_ok}/{len(results['national'])} 可用")
    print(f"省级政府: {provincial_ok}/{len(results['provincial'])} 可用")
    print(f"总计: {total_ok}/{total_sites} 可用 ({total_ok*100//total_sites}%)")
    print(f"总记录: {total_records} 条")

    return results

if __name__ == '__main__':
    test_all()
