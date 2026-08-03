#!/usr/bin/env python3
"""统一采集器 - 整合JS渲染+curl_cffi+普通请求,支持分页与并发详情抓取"""
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

try:
    from .fetcher import GovDocFetcher, detect_encoding
    from .parser import extract_items
    from .detail_extractor import extract_detail
except ImportError:
    from fetcher import GovDocFetcher, detect_encoding
    from parser import extract_items
    from detail_extractor import extract_detail

logger = logging.getLogger(__name__)

warnings.filterwarnings('ignore', message='Unverified HTTPS request')

DETAIL_MAX_WORKERS = 4   # 详情页并发度(need_js 站点自动降为串行)
DETAIL_TIMEOUT = 30


class UnifiedFetcher(GovDocFetcher):
    """统一采集器:支持JS渲染、curl_cffi浏览器指纹、普通请求、分页、并发详情"""

    def __init__(self, *args, enable_js=True, enable_cffi=True, **kwargs):
        super().__init__(*args, **kwargs)

        # curl_cffi初始化
        self.use_cffi = enable_cffi
        self.cffi_session = None
        if enable_cffi:
            try:
                from curl_cffi import requests as cffi_requests
                self.cffi_session = cffi_requests.Session(impersonate="chrome120")
            except ImportError:
                logger.info('curl_cffi 未安装,禁用 cffi 策略')
                self.use_cffi = False

        # Playwright初始化(懒加载)
        self.use_js = enable_js
        self.browser = None
        self.playwright = None
        self.context = None

    def _init_browser(self):
        """初始化浏览器(懒加载)"""
        if self.browser is None and self.use_js:
            try:
                from playwright.sync_api import sync_playwright
                self.playwright = sync_playwright().start()
                self.browser = self.playwright.chromium.launch(headless=True)
                self.context = self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/120.0.0.0 Safari/537.36'),
                    locale='zh-CN'
                )
            except ImportError:
                logger.info('playwright 未安装,禁用 JS 渲染策略')
                self.use_js = False

    def fetch_with_js(self, url: str) -> str:
        """使用Playwright获取JS渲染后的内容"""
        self._init_browser()
        if not self.context:
            return ""
        page = self.context.new_page()
        try:
            page.goto(url, timeout=60000, wait_until='networkidle')
            # 额外等待动态内容加载
            page.wait_for_timeout(3000)
            return page.content()
        finally:
            page.close()

    # ---------- 单页三级策略 ----------

    def _cffi_get(self, url: str, config: Dict):
        """curl_cffi 请求(带 TLS 降级)。失败抛异常由调用方处理。"""
        verify = self._resolve_verify(config)
        try:
            return self.cffi_session.get(url, timeout=DETAIL_TIMEOUT, verify=verify, headers={
                'Referer': config.get('base_url', url),
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            })
        except Exception as e:
            if verify and ('ssl' in str(e).lower() or 'certificate' in str(e).lower()):
                logger.warning('TLS 证书校验失败,降级 verify=False: %s (%s)', url, e)
                return self.cffi_session.get(url, timeout=DETAIL_TIMEOUT, verify=False, headers={
                    'Referer': config.get('base_url', url),
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                })
            raise

    def _fetch_page(self, url: str, config: Dict) -> List[Dict]:
        """单页三级降级:curl_cffi → Playwright → 普通请求。

        分页由基类 fetch_list 驱动(循环调用本方法),此处只负责单页。
        """
        last_err: Optional[Exception] = None

        # 策略1: curl_cffi浏览器指纹 (解决 TLS指纹检测的 WAF)
        if config.get('use_cffi', False) and self.cffi_session:
            try:
                resp = self._cffi_get(url, config)
                try:
                    resp.encoding = detect_encoding(resp)
                except Exception:  # noqa: BLE001 cffi 编码探测失败回退 utf-8
                    resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    items = extract_items(resp.text, config)
                    if items:
                        return items
            except Exception as e:
                logger.debug('cffi 策略失败: %s (%s)', url, e)
                last_err = e

        # 策略2: Playwright JS渲染 (动态加载 + iframe 嵌套)
        if config.get('need_js', False) and self.use_js:
            try:
                html = self.fetch_with_js(url)
                if html:
                    items = extract_items(html, config)
                    if items:
                        return items
            except Exception as e:
                logger.debug('Playwright 策略失败: %s (%s)', url, e)
                last_err = e

        # 策略3: 普通请求(兜底,含重试)
        try:
            return super()._fetch_page(url, config)
        except Exception as e:
            logger.debug('普通请求失败: %s (%s)', url, e)
            last_err = e
            raise

    # ---------- 详情页 ----------

    def fetch_detail(self, url: str, base_url: str = '', use_cffi: bool = False,
                     need_js: bool = False) -> Dict:
        """
        抓取详情页正文。支持三级降级:
          1) curl_cffi (use_cffi=True)
          2) Playwright (need_js=True)
          3) plain requests

        返回 detail_extractor.extract_detail() 的结构化结果
        """
        last_html = ''
        last_err: Optional[Exception] = None
        config_stub = {'base_url': base_url}

        # 策略1: curl_cffi
        if use_cffi and self.cffi_session:
            try:
                resp = self._cffi_get(url, config_stub)
                try:
                    resp.encoding = detect_encoding(resp)
                except Exception:  # noqa: BLE001
                    resp.encoding = 'utf-8'
                if resp.status_code == 200 and len(resp.text) > 500:
                    last_html = resp.text
                    result = extract_detail(resp.text, url, base_url)
                    if result['has_content']:
                        return result
            except Exception as e:
                logger.debug('详情 cffi 策略失败: %s (%s)', url, e)
                last_err = e

        # 策略2: Playwright
        if need_js and self.use_js:
            try:
                html = self.fetch_with_js(url)
                if html:
                    last_html = html
                    result = extract_detail(html, url, base_url)
                    if result['has_content']:
                        return result
            except Exception as e:
                logger.debug('详情 Playwright 策略失败: %s (%s)', url, e)
                last_err = e

        # 策略3: plain requests(带 TLS 降级与编码检测)
        try:
            resp = self._get_with_verify(url, config_stub, timeout=DETAIL_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = detect_encoding(resp)
            last_html = resp.text
            return extract_detail(resp.text, url, base_url)
        except Exception as e:
            if last_html:
                return extract_detail(last_html, url, base_url)
            logger.warning('详情页全部策略失败: %s (%s)', url, last_err or e)
            return {
                'url': url,
                'content_text': '',
                'content_html': '',
                'word_count': 0,
                'attachments': [],
                'metadata': {},
                'has_content': False,
                'error': str(e),
            }

    def fetch_list_with_details(self, site_key: str, level: str = "national",
                                limit: int = 5, include_detail: bool = True,
                                max_workers: int = DETAIL_MAX_WORKERS) -> List[Dict]:
        """
        一步到位: 列表 + 详情正文

        Args:
            limit: 限制详情抓取条数(详情页抓取慢,默认 5)
            max_workers: 详情并发度;need_js 站点自动降为串行
                         (Playwright sync API 非线程安全)
        """
        items = self.fetch_list(site_key, level)
        if not include_detail or not items:
            return items

        config = self.load_config(site_key, level)
        use_cffi = config.get('use_cffi', False)
        need_js = config.get('need_js', False)
        base_url = config.get('base_url', '')

        targets = [it for it in items[:limit] if it.get('link')]
        if not targets:
            return items

        if need_js or max_workers <= 1:
            # Playwright 非线程安全 → 串行
            for item in targets:
                item['detail'] = self.fetch_detail(
                    item['link'], base_url, use_cffi=use_cffi, need_js=need_js)
            return items

        # 纯 HTTP/cffi 站点 → 线程池并发(每个 worker 独立会话,避免共享状态)
        import threading

        if not hasattr(self, '_worker_tls'):
            self._worker_tls = threading.local()

        def worker(item):
            session = getattr(self._worker_tls, 'session', None)
            if session is None:
                import requests as _requests
                session = _requests.Session()
                session.headers.update(self.session.headers)
                self._worker_tls.session = session
            try:
                resp = session.get(item['link'], timeout=DETAIL_TIMEOUT,
                                   verify=self._resolve_verify(config))
                resp.raise_for_status()
                resp.encoding = detect_encoding(resp)
                return item, extract_detail(resp.text, item['link'], base_url)
            except Exception as e:
                logger.debug('并发详情抓取失败,回退 fetch_detail: %s (%s)',
                             item.get('link'), e)
                return item, self.fetch_detail(
                    item['link'], base_url, use_cffi=use_cffi, need_js=False)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(worker, it) for it in targets]
            for fut in as_completed(futures):
                item, detail = fut.result()
                item['detail'] = detail
        return items

    def fetch_list_new(self, site_key: str, level: str = "national",
                       max_pages: Optional[int] = None,
                       store=None) -> List[Dict]:
        """增量采集:只返回没见过的链接(首次运行=全量)。

        用于政策监控场景(cron 定时跑,只报新政策)。
        新链接自动记入 SeenStore 并落盘;store 可传入自定义实例(如指定路径)。
        """
        if store is None:
            if getattr(self, '_seen_store', None) is None:
                try:
                    from .seen_store import SeenStore
                except ImportError:
                    from seen_store import SeenStore
                self._seen_store = SeenStore()
            store = self._seen_store

        items = self.fetch_list(site_key, level, max_pages=max_pages)
        new_items = store.filter_new(site_key, items)
        store.save()
        return new_items

    def close(self):
        """关闭资源"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        if self.cffi_session:
            self.cffi_session.close()

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
