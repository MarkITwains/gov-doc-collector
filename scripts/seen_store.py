#!/usr/bin/env python3
"""增量采集缓存 — 记录已采集链接,支持"只报新政策"的监控场景。

JSON 持久化,按 site_key 隔离,链接按首次出现顺序保存(超限时淘汰最旧):
    { "ndrc": ["http://...", ...], "mof": [...] }

用法:
    store = SeenStore()                     # 缺省存到 .cache/seen_links.json
    items = fetcher.fetch_list('ndrc', 'national')
    new_items = store.filter_new('ndrc', items)   # 只留没见过链接的条目
    store.save()

    # 或者直接用 UnifiedFetcher.fetch_list_new()(内部自动调用本模块)
"""
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = Path(__file__).resolve().parent.parent / '.cache' / 'seen_links.json'
DEFAULT_MAX_PER_SITE = 20000


class SeenStore:
    def __init__(self, path=None, max_per_site: int = DEFAULT_MAX_PER_SITE):
        self.path = Path(path) if path else DEFAULT_STORE_PATH
        self.max_per_site = max_per_site
        self._lock = threading.Lock()
        self._sets: Dict[str, set] = {}
        self._order: Dict[str, List[str]] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            for site, links in data.items():
                self._order[site] = list(links)
                self._sets[site] = set(links)
        except Exception as e:  # noqa: BLE001 缓存损坏不应阻断采集
            logger.warning('SeenStore 加载失败,从空白开始: %s (%s)', self.path, e)

    def save(self):
        """原子写盘(临时文件 + rename)"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            with self._lock:
                data = {site: order for site, order in self._order.items()}
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.path)
        except OSError as e:
            logger.warning('SeenStore 保存失败: %s (%s)', self.path, e)

    def is_seen(self, site_key: str, link: str) -> bool:
        if not link:
            return True  # 无链接的条目不做增量判断,视为已见
        with self._lock:
            return link in self._sets.get(site_key, set())

    def mark_seen(self, site_key: str, links: Iterable[str]):
        with self._lock:
            s = self._sets.setdefault(site_key, set())
            order = self._order.setdefault(site_key, [])
            for link in links:
                if not link or link in s:
                    continue
                s.add(link)
                order.append(link)
            # 超限淘汰最旧
            overflow = len(order) - self.max_per_site
            if overflow > 0:
                for old in order[:overflow]:
                    s.discard(old)
                del order[:overflow]

    def filter_new(self, site_key: str, items: List[Dict],
                   mark: bool = True) -> List[Dict]:
        """返回未采集过的条目(同批次内重复链接也去重);
        mark=True 时顺带把新链接记入缓存(需自行 save())"""
        new_items: List[Dict] = []
        batch_seen = set()
        for it in items:
            link = it.get('link')
            if not link or link in batch_seen or self.is_seen(site_key, link):
                continue
            batch_seen.add(link)
            new_items.append(it)
        if mark:
            self.mark_seen(site_key, batch_seen)
        logger.info('站点 %s: %d 条中 %d 条为新政策',
                    site_key, len(items), len(new_items))
        return new_items

    def reset(self, site_key: Optional[str] = None):
        """清空缓存(site_key 缺省清空全部)"""
        with self._lock:
            if site_key is None:
                self._sets.clear()
                self._order.clear()
            else:
                self._sets.pop(site_key, None)
                self._order.pop(site_key, None)
