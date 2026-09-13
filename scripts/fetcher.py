#!/usr/bin/env python3
"""基础采集器 - 普通 HTTP 请求 + 重试 + 编码自动检测 + 分页框架"""
import json
import logging
import re
import time
import random
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import requests

# 兼容包内导入(from scripts.fetcher import ...)与直接脚本执行(python scripts/fetcher.py)
try:
    from .parser import extract_items, parse_xml_feed, parse_json_api
except ImportError:
    from parser import extract_items, parse_xml_feed, parse_json_api

logger = logging.getLogger(__name__)

# 仅静默"未验证 HTTPS"告警(降级路径会打 logger.warning,不再盲目全局静音)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')


def detect_encoding(resp) -> str:
    """推断响应编码:header charset → <meta charset> → utf-8 严格解码探测 → gb18030 兜底。

    政府网站大量使用 GBK/GB2312(尤其老 TRS 系统),硬编码 utf-8 会产出乱码标题。
    gb18030 是 GBK/GB2312 的超集,作为中文兜底最安全。
    """
    try:
        enc = (getattr(resp, 'encoding', '') or '').lower()
        if enc and enc != 'iso-8859-1':
            return resp.encoding
        head = resp.content[:4096]
        m = re.search(rb'charset\s*=\s*["\']?\s*([\w-]+)', head, re.I)
        if m:
            return m.group(1).decode('ascii', 'ignore') or 'utf-8'
        try:
            resp.content.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError:
            return 'gb18030'
    except Exception:  # noqa: BLE001 编码探测失败不应阻断采集
        return 'utf-8'


def _same_dir(column: str, tpl: str) -> bool:
    """分页 URL 模板是否属于该栏目(同目录)。

    兼容两种旧写法:栏目写目录(`/list/` 或 `/list`)或写列表页文件
    (`/list/index.html`);模板取其所在目录比较。
    """
    col = column or ''
    if not col or not tpl:
        return False
    col_dir = col if col.endswith('/') else col.rsplit('/', 1)[0] + '/'
    tpl_dir = (tpl.rsplit('/', 1)[0] + '/') if '/' in tpl else '/'
    if col_dir == tpl_dir:
        return True
    # 栏目写成目录但没带尾斜杠(/list vs /list/index_{page}.html)
    return col.rstrip('/') == tpl_dir.rstrip('/')


class GovDocFetcher:
    def __init__(self, config_dir: str = None, verify_ssl: bool = True):
        """
        Args:
            config_dir: 站点配置目录,缺省 configs/sites/
            verify_ssl: TLS 证书校验策略。True=优先校验,证书链损坏时自动降级
                        为不校验并打 warning;False=始终不校验(旧行为)。
                        单站点可用配置项 "verify_ssl": false 强制关闭。
        """
        if config_dir is None:
            config_dir = Path(__file__).parent.parent / "configs" / "sites"
        self.config_dir = Path(config_dir)
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })

    def load_config(self, site_key: str, level: str = "national") -> Dict:
        """加载站点配置"""
        config_file = self.config_dir / f"{level}.json"
        with open(config_file, 'r', encoding='utf-8') as f:
            configs = json.load(f)
        return configs.get(site_key)

    # ---------- 栏目 / 分页 ----------

    def _columns(self, config: Dict) -> List[str]:
        """站点配置里的栏目路径列表。

        `search_path` 既可以是字符串(单栏目,兼容旧配置),也可以是数组
        —— 申报公告往往分散在"通知公告 / 申报专区 / 政策法规"多个栏目,
        只配一个栏目是踩不到申报通知的主因。另可用 `extra_paths` 追加栏目。
        """
        raw = config.get('search_path', '/')
        cols = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        extra = config.get('extra_paths') or []
        cols.extend(list(extra) if isinstance(extra, (list, tuple)) else [extra])
        out, seen = [], set()
        for c in cols:
            c = (c or '').strip()
            if not c:
                continue
            if not c.startswith('/'):
                c = '/' + c
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out or ['/']

    def _column_page_urls(self, config: Dict, column: str,
                          max_pages: Optional[int] = None) -> List[str]:
        """单个栏目的分页 URL 列表(第 1 页恒为 base+column)。

        支持两种配置:
          {"pagination": {"type": "template", "url_template": "/list/index_{page}.html",
                          "start": 1, "max_pages": 3}}
            → 第 2 页起按模板替换 {page}(TRS 系常见:index.html / index_1.html / ...)
          {"pagination": {"type": "query", "param": "page", "start": 2, "max_pages": 3}}
            → 第 2 页起追加 ?page=N(或 &page=N)
        start = 第 2 个 URL 里代入的页码,缺省 1。

        多栏目时模板型分页只对"模板所属栏目"生效(模板以该栏目路径开头,
        或使用 {column} 占位);不匹配的栏目只采第 1 页,避免翻到别的栏目上。
        """
        base = config['base_url'] + column
        pag = config.get('pagination') or {}
        total = pag.get('max_pages', 1) if max_pages is None else max_pages
        if total <= 1 or not pag:
            return [base]

        start = pag.get('start', 1)
        urls = [base]
        tpl = pag.get('url_template', '')
        if pag.get('type') != 'query':
            if '{column}' in tpl:
                tpl = tpl.replace('{column}', column)
            elif tpl and not _same_dir(column, tpl):
                # 模板只对"它所属栏目"生效,否则多栏目配置会把别的栏目翻到
                # 同一批 URL 上(重复请求 + 采错栏目)
                logger.debug('栏目 %s 与 url_template(%s) 不在同一目录,该栏目只采第 1 页',
                             column, tpl)
                return [base]
        for i in range(1, total):
            page_no = start + i - 1
            if pag.get('type') == 'query':
                sep = '&' if '?' in base else '?'
                urls.append(f"{base}{sep}{pag.get('param', 'page')}={page_no}")
            else:
                if not tpl:
                    break
                path = tpl.replace('{page}', str(page_no))
                urls.append(config['base_url'] + path)
        return urls

    def _page_urls(self, config: Dict, max_pages: Optional[int] = None,
                   column: Optional[str] = None) -> List[str]:
        """待采集页面 URL 列表。column 指定单个栏目;缺省拼接**所有**栏目。"""
        cols = [column] if column else self._columns(config)
        urls: List[str] = []
        for col in cols:
            urls.extend(self._column_page_urls(config, col, max_pages))
        return urls

    # ---------- TLS ----------

    def _resolve_verify(self, config: Dict) -> bool:
        return bool(config.get('verify_ssl', self.verify_ssl))

    def _get_with_verify(self, url: str, config: Dict, timeout: int = 40,
                         **kwargs) -> requests.Response:
        """优先验证 TLS 证书;证书链损坏的站点自动降级为不验证并告警。"""
        verify = self._resolve_verify(config)
        if not verify:
            return self.session.get(url, timeout=timeout, verify=False, **kwargs)
        try:
            return self.session.get(url, timeout=timeout, verify=True, **kwargs)
        except requests.exceptions.SSLError as e:
            logger.warning('TLS 证书校验失败,降级 verify=False: %s (%s)', url, e)
            return self.session.get(url, timeout=timeout, verify=False, **kwargs)

    # ---------- 采集 ----------

    def _fetch_page(self, url: str, config: Dict) -> List[Dict]:
        """抓取单页并解析为条目(带重试)。子类覆写此方法以接入 cffi/JS 策略。"""
        # 设置Referer
        self.session.headers['Referer'] = config['base_url']

        # 重试3次
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(random.uniform(1, 3))

                resp = self._get_with_verify(url, config, timeout=40)
                resp.raise_for_status()
                resp.encoding = detect_encoding(resp)

                content_type = resp.headers.get('Content-Type', '')

                if 'json' in content_type:
                    return parse_json_api(resp.text, config.get('json_mappings', {}))
                elif 'xml' in content_type:
                    return parse_xml_feed(resp.text)
                else:
                    return extract_items(resp.text, config)

            except Exception as e:
                logger.debug('抓取失败(第 %d 次): %s (%s)', attempt + 1, url, e)
                if attempt == 2:
                    raise
                continue

        return []

    def fetch_list(self, site_key: str, level: str = "national",
                   max_pages: Optional[int] = None, **kwargs) -> List[Dict]:
        """采集文档列表(多栏目 + 分页,按 link 去重;单栏目空页即停)。

        Args:
            max_pages: 覆盖配置里的 pagination.max_pages;缺省用配置值(无配置=1 页)
        """
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        items: List[Dict] = []
        seen = set()
        for col in self._columns(config):
            for url in self._column_page_urls(config, col, max_pages):
                page_items = self._fetch_page(url, config) or []
                for it in page_items:
                    key = it.get('link') or it.get('title')
                    if key and key not in seen:
                        seen.add(key)
                        # 记下来源栏目:下游(候选筛选/解析)可用栏目先验
                        it.setdefault('source_path', col)
                        items.append(it)
                logger.debug('站点 %s 栏目 %s 分页 %s: %d 条',
                             site_key, col, url, len(page_items))
                if not page_items:
                    break  # 本栏目空页 → 不再翻它后面的页(其它栏目继续)
        return items

    def fetch_html(self, url: str, config: Optional[Dict] = None,
                   timeout: int = 40) -> str:
        """抓取任意页面 HTML(带编码检测/TLS 降级),供栏目探测等分析工具复用。

        注意:不同于 _fetch_page,这里不解析列表、不做三级策略降级。
        """
        config = config or {}
        resp = self._get_with_verify(url, config, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = detect_encoding(resp)
        return resp.text

    def fetch_detail(self, url: str) -> Dict:
        """
        获取详情页内容并结构化提取。
        返回字典: {content_text, content_html, word_count, attachments, metadata, has_content, error?}
        """
        try:
            from .detail_extractor import extract_detail
        except ImportError:
            from detail_extractor import extract_detail
        try:
            resp = self._get_with_verify(url, {}, timeout=30)
            resp.raise_for_status()
            resp.encoding = detect_encoding(resp)
            return extract_detail(resp.text, url)
        except Exception as e:
            logger.warning('详情页抓取失败: %s (%s)', url, e)
            return {
                'url': url,
                'content_text': '',
                'content_html': '',
                'word_count': 0,
                'attachments': [],
                'metadata': {},
                'has_content': False,
                'content_quality': {'score': 0, 'level': 'low', 'flags': ['fetch_error']},
                'content_usable': False,
                'error': str(e),
            }


if __name__ == "__main__":
    fetcher = GovDocFetcher()
    items = fetcher.fetch_list("gov_cn", "national")
    for item in items[:5]:
        print(json.dumps(item, ensure_ascii=False, indent=2))
