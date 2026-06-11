#!/usr/bin/env python3
"""快速测试修改的站点"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from fetcher import GovDocFetcher

fetcher = GovDocFetcher()

# 测试修改的国家级站点
test_sites = ['miit', 'mohrss', 'mps', 'nhc', 'nra']
print('国家级站点测试:')
for key in test_sites:
    try:
        items = fetcher.fetch_list(key, 'national')
        print(f'✓ {key}: {len(items)} 条')
    except Exception as e:
        print(f'✗ {key}: {str(e)[:50]}')

# 测试修改的省级站点
test_sites_p = ['tianjin', 'chongqing', 'hebei', 'shanxi', 'jilin', 'zhejiang', 'hubei', 'hunan']
print('\n省级站点测试:')
for key in test_sites_p:
    try:
        items = fetcher.fetch_list(key, 'provincial')
        print(f'✓ {key}: {len(items)} 条')
    except Exception as e:
        print(f'✗ {key}: {str(e)[:50]}')
