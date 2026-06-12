#!/usr/bin/env python3
"""
用 gov-doc-collector 真实采集的政策详情(detail_full_results.json)
批量验证 policy_parser 的提取效果。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'skills' / 'policy-analyzer' / 'scripts'))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from policy_parser import parse_policy

DATA = Path(__file__).parent / 'detail_full_results.json'


def main():
    with open(DATA, encoding='utf-8') as f:
        data = json.load(f)

    total = 0
    stats = {'doc_type': 0, 'doc_number': 0, 'outline': 0, 'measures': 0,
             'funding': 0, 'conditions': 0, 'targets': 0, 'validity': 0}
    samples = []

    for site in data['results']:
        for it in site.get('items', []):
            text = it.get('content_text') or ''
            if len(text) < 300:
                continue
            total += 1
            parsed = parse_policy(text, title=it.get('title', ''),
                                  metadata=it.get('metadata') or {})
            if parsed['doc_type'] != '其他':
                stats['doc_type'] += 1
            if parsed['doc_number']:
                stats['doc_number'] += 1
            if len(parsed['outline']) >= 3:
                stats['outline'] += 1
            if parsed['support_measures']:
                stats['measures'] += 1
            if parsed['funding']:
                stats['funding'] += 1
            if parsed['conditions']:
                stats['conditions'] += 1
            if parsed['support_targets']:
                stats['targets'] += 1
            v = parsed['validity']
            if any(v.values()):
                stats['validity'] += 1
            samples.append((site['site_key'], parsed))

    print(f"== 真实政策解析覆盖率 (n={total}) ==")
    for k, v in stats.items():
        print(f"  {k:12s}: {v}/{total} ({100*v/total:.0f}%)")

    # 展示几条解析最丰富的
    rich = sorted(samples, key=lambda s: (
        len(s[1]['conditions']) + len(s[1]['funding']) + len(s[1]['support_measures'])),
        reverse=True)[:3]
    for site, p in rich:
        print(f"\n--- [{site}] {p['title'][:50]}")
        print(f"  文种={p['doc_type']} 字号={p['doc_number']} 大纲={len(p['outline'])}节")
        print(f"  支持方式={p['support_measures']}")
        print(f"  资金条款={len(p['funding'])}条 申报条件={len(p['conditions'])}条")
        for c in p['conditions'][:5]:
            print(f"    [{c['field']}] {c['op'] or ''} {c['value'] if c['value'] is not None else ''}"
                  f"{c['unit'] or ''}  ← {c['text'][:45]}")
        for f in p['funding'][:4]:
            label = f.get('value_wan') or f.get('ratio_pct')
            print(f"    [{f['kind']}] {label}  ← {f['text'][:45]}")


if __name__ == '__main__':
    main()
