#!/usr/bin/env python3
"""全站诊断 - 精确分类每个站点的失败原因"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json
import concurrent.futures
from pathlib import Path

import requests
import warnings
warnings.filterwarnings('ignore')

from parser import extract_items, parse_xml_feed, parse_json_api

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

CONFIG_DIR = Path(__file__).parent.parent / "configs" / "sites"

# JS渲染特征:返回的HTML极小、或含有典型的JS challenge标记
JS_MARKERS = ['<noscript>', 'document.cookie', 'window.location', 'eval(', 'YunSuoAutoJump',
              'security verify', 'waf', 'jschl', '_$', 'arg1=', 'acw_sc']


def diagnose_one(key, config, level):
    """诊断单个站点,返回 (key, level, name, status, detail, count)"""
    url = config['base_url'] + config['search_path']
    sess = requests.Session()
    sess.headers.update(HEADERS)
    sess.headers['Referer'] = config['base_url']
    try:
        resp = sess.get(url, timeout=30, verify=False)
    except requests.exceptions.ConnectionError as e:
        s = str(e)
        if 'Connection aborted' in s or 'reset' in s.lower():
            return (key, level, config['name'], 'CONN_RESET', s[:80], 0)
        return (key, level, config['name'], 'CONN_ERROR', s[:80], 0)
    except requests.exceptions.Timeout:
        return (key, level, config['name'], 'TIMEOUT', '20s timeout', 0)
    except Exception as e:
        return (key, level, config['name'], 'ERROR', str(e)[:80], 0)

    if resp.status_code != 200:
        return (key, level, config['name'], f'HTTP_{resp.status_code}', resp.reason, 0)

    resp.encoding = 'utf-8'
    text = resp.text
    ct = resp.headers.get('Content-Type', '')

    # 解析
    try:
        if 'json' in ct:
            items = parse_json_api(text, config.get('json_mappings', {}))
        elif 'xml' in ct:
            items = parse_xml_feed(text)
        else:
            items = extract_items(text, config)
    except Exception as e:
        return (key, level, config['name'], 'PARSE_ERROR', str(e)[:80], 0)

    if items:
        return (key, level, config['name'], 'OK', f'{len(items)} items', len(items))

    # 0条 - 区分是JS渲染还是选择器问题
    body_len = len(text)
    js_hint = any(m in text for m in JS_MARKERS)
    if body_len < 3000 and js_hint:
        return (key, level, config['name'], 'NEED_JS', f'body={body_len}B, js markers', 0)
    if body_len < 1500:
        return (key, level, config['name'], 'NEED_JS?', f'tiny body={body_len}B', 0)
    return (key, level, config['name'], 'SELECTOR_0', f'body={body_len}B, selectors matched 0', 0)


def main():
    tasks = []
    for level in ['national', 'provincial']:
        with open(CONFIG_DIR / f"{level}.json", 'r', encoding='utf-8') as f:
            sites = json.load(f)
        for key, config in sites.items():
            tasks.append((key, config, level))

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(diagnose_one, k, c, l) for k, c, l in tasks]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    # 按状态分组输出
    by_status = {}
    for r in results:
        by_status.setdefault(r[3], []).append(r)

    print(f"{'='*78}")
    print(f"诊断结果 ({len(results)} 个站点)")
    print(f"{'='*78}\n")

    order = ['OK', 'NEED_JS', 'NEED_JS?', 'SELECTOR_0', 'PARSE_ERROR']
    keys_sorted = order + sorted(k for k in by_status if k not in order)
    seen = set()
    for status in keys_sorted:
        if status not in by_status or status in seen:
            continue
        seen.add(status)
        rows = sorted(by_status[status], key=lambda x: (x[1], x[0]))
        print(f"--- {status} ({len(rows)}) ---")
        for key, level, name, _, detail, count in rows:
            print(f"  {key:16s} {level:11s} {name:16s} {detail}")
        print()

    ok = len(by_status.get('OK', []))
    total_items = sum(r[5] for r in results)
    print(f"可用: {ok}/{len(results)} ({ok*100//len(results)}%), 共 {total_items} 条记录")

    # 保存JSON结果供后续使用
    out = {r[0] + ':' + r[1]: {'name': r[2], 'status': r[3], 'detail': r[4], 'count': r[5]} for r in results}
    with open(Path(__file__).parent / 'diagnose_results.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
