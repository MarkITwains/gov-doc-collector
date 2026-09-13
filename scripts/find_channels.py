#!/usr/bin/env python3
"""栏目探测(P0-1 配套):找出站点里"值得接入采集"的栏目,尤其是申报/通知公告专区。

背景:实测 67 篇真实详情里申报类只有 1 篇 —— 因为站点配置大都指向
"政策法规/政务公开"栏目,而企业能申报的通知通常在"通知公告 / 申报专区 /
政务服务 / 项目申报"等栏目下。本工具把首页(或指定页)里的栏目链接按
"申报相关度"排序列出来,人工确认后写进配置。

用法:
    python scripts/find_channels.py beijing --level provincial
    python scripts/find_channels.py --level national --all --top 8
    python scripts/find_channels.py beijing --level provincial --json

输出末尾会给出可直接粘贴的 search_path / channel_type 配置片段。

导入方式兼容:python scripts/find_channels.py 或 python -m scripts.find_channels
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # bs4 已是项目依赖,缺失时给出明确提示
    BeautifulSoup = None

try:
    from .fetcher import GovDocFetcher
    from .policy_candidate import classify_channel_by_url
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from fetcher import GovDocFetcher
    from policy_candidate import classify_channel_by_url

# 栏目名权重(标题里出现即加分):申报相关最高,其次是通知公告与政策文件
CHANNEL_KEYWORDS = [
    (re.compile(r'申报|征集|遴选|奖补|资助|补贴|资金|项目库|入库|认定'), 6),
    (re.compile(r'通知公告|公告公示|公示公告|通知'), 4),
    (re.compile(r'政务服务|企业服务|办事|一网通办|服务专区|专题'), 3),
    (re.compile(r'政策|法规|文件|规章|规范性文件'), 2),
    (re.compile(r'新闻|动态|要闻|媒体|图片|视频|党建|人事|招聘'), -4),
]
# URL 栏目类型的基础分
CHANNEL_SCORE = {'apply': 8, 'notice': 5, 'law': 2, 'news': -3, 'unknown': 0}


def score_channel(text: str, url: str) -> Dict:
    """栏目名 + 链接结构 → 申报相关度得分与理由。"""
    channel = classify_channel_by_url(url)
    score = CHANNEL_SCORE.get(channel, 0)
    reasons = [f'URL类型={channel}'] if channel != 'unknown' else []
    for pat, weight in CHANNEL_KEYWORDS:
        m = pat.search(text or '')
        if m:
            score += weight
            reasons.append(f'名称含「{m.group(0)}」({weight:+d})')
    return {'score': score, 'channel': channel, 'reasons': reasons}


def extract_channels(html: str, base_url: str, same_host_only: bool = True,
                     max_items: int = 400) -> List[Dict]:
    """从页面里抽取站内栏目链接(按 URL 去重,保留出现次数)。"""
    if BeautifulSoup is None:
        raise RuntimeError('需要 beautifulsoup4(已在 requirements.txt)')
    soup = BeautifulSoup(html, 'html.parser')
    host = urlparse(base_url).netloc
    found: Dict[str, Dict] = {}

    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:')):
            continue
        full = urljoin(base_url, href)
        if not full.lower().startswith(('http://', 'https://')):
            continue
        p = urlparse(full)
        if same_host_only and host and p.netloc != host:
            continue
        text = re.sub(r'\s+', ' ', a.get_text(' ', strip=True))[:40]
        if not text:
            continue
        key = f'{p.scheme}://{p.netloc}{p.path}'
        item = found.setdefault(key, {'url': key, 'titles': [], 'count': 0})
        item['count'] += 1
        if text not in item['titles']:
            item['titles'].append(text)
        if len(found) >= max_items:
            break

    out = []
    for key, item in found.items():
        title = max(item['titles'], key=len)  # 取信息量最大的那个标题
        scored = score_channel(title, key)
        out.append({
            'url': key,
            'title': title,
            'path': urlparse(key).path,
            'hits': item['count'],
            'score': scored['score'] + min(item['count'], 3),  # 出现次数轻微加权
            'channel': scored['channel'],
            'reasons': scored['reasons'],
        })
    out.sort(key=lambda x: (x['score'], x['hits']), reverse=True)
    return out


def probe_site(fetcher: GovDocFetcher, site_key: str, level: str,
               top: int = 15, page: str = '/') -> Dict:
    """探测单个站点:抓首页 → 提取并排序栏目。"""
    try:
        config = fetcher.load_config(site_key, level)
    except FileNotFoundError:
        return {'site_key': site_key, 'error': f'配置目录不存在(level={level})'}
    if not config:
        return {'site_key': site_key, 'error': '站点未配置'}
    base_url = config['base_url']
    url = urljoin(base_url + '/', page.lstrip('/'))
    try:
        html = fetcher.fetch_html(url, config, timeout=30)
    except Exception as e:  # noqa: BLE001 探测失败只报告,不中断批量
        return {'site_key': site_key, 'url': url, 'error': f'抓取失败: {e}'}
    channels = extract_channels(html, url)
    return {
        'site_key': site_key,
        'name': config.get('name'),
        'url': url,
        'current_search_path': config.get('search_path'),
        'channel_count': len(channels),
        'top': channels[:top],
    }


def _print_site(result: Dict, top: int) -> None:
    print(f"\n=== {result['site_key']} {result.get('name') or ''} ===")
    if result.get('error'):
        print('  !', result['error'])
        return
    print(f"  当前 search_path: {result['current_search_path']}")
    print(f"  页面: {result['url']}  (共 {result['channel_count']} 个站内栏目)")
    for c in result['top'][:top]:
        print(f"  {c['score']:>4}  [{c['channel']:7s}] {c['path'][:44]:46s} {c['title'][:22]}")
        print(f"        {'; '.join(c['reasons'])}")
    best = result['top'][:top]
    apply_like = [c for c in best if c['score'] >= 8]
    if apply_like:
        snippet = {
            'search_path': [c['path'] for c in apply_like[:3]],
            'channel_type': 'apply' if apply_like[0]['channel'] == 'apply' else 'notice',
        }
        print('  建议写入配置(configs/sites/*.json):')
        print('    ' + json.dumps(snippet, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description='站点栏目探测:找出申报/通知公告栏目')
    ap.add_argument('site_key', nargs='?', help='站点标识符,如 beijing / ndrc')
    ap.add_argument('--level', default='provincial', choices=['national', 'provincial'])
    ap.add_argument('--all', action='store_true', help='探测该 level 下所有站点')
    ap.add_argument('--limit', type=int, default=5, help='--all 时最多探测几个站点')
    ap.add_argument('--top', type=int, default=8, help='每站点打印几个候选栏目')
    ap.add_argument('--page', default='/', help='探测入口页路径(默认首页)')
    ap.add_argument('--json', action='store_true', help='输出 JSON(便于回填配置)')
    args = ap.parse_args()

    fetcher = GovDocFetcher()
    if args.all:
        config_file = fetcher.config_dir / f'{args.level}.json'
        if not config_file.exists():
            print(f'配置文件不存在: {config_file}')
            return 2
        keys = list(json.loads(config_file.read_text(encoding='utf-8')))[:args.limit]
    elif args.site_key:
        keys = [args.site_key]
    else:
        ap.error('需要 site_key 或 --all')

    results = [probe_site(fetcher, k, args.level, top=args.top, page=args.page)
               for k in keys]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    for r in results:
        _print_site(r, args.top)
    print('\n提示:把候选栏目路径填进 search_path 数组,并声明 channel_type;'
          '\n      "apply" 表示该栏目整体是申报专区(整栏目条目都会当候选)。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
