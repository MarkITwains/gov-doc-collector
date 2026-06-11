#!/usr/bin/env python3
"""最终采集器 - 整合JS渲染+curl_cffi+普通请求"""
import json
import warnings
from pathlib import Path
from typing import Dict, List
from fetcher import GovDocFetcher
from parser import extract_items, parse_xml_feed, parse_json_api

warnings.filterwarnings('ignore')

class UnifiedFetcher(GovDocFetcher):
    """统一采集器:支持JS渲染、curl_cffi浏览器指纹、普通请求"""

    def __init__(self, *args, enable_js=True, enable_cffi=True, **kwargs):
        super().__init__(*args, **kwargs)

        # curl_cffi初始化
        self.use_cffi = enable_cffi
        self.cffi_session = None
        if enable_cffi:
            try:
                from curl_cffi import requests as cffi_requests
                # 使用 ja3=firefox 绕过部分 WAF（比 chrome 更宽松的指纹）
                self.cffi_session = cffi_requests.Session(impersonate="chrome120")
            except ImportError:
                self.use_cffi = False

        # Playwright初始化
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
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    locale='zh-CN'
                )
            except ImportError:
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

    def fetch_list(self, site_key: str, level: str = "national", **kwargs) -> List[Dict]:
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        url = config['base_url'] + config['search_path']
        last_err = None

        # 策略1: curl_cffi浏览器指纹 (解决 TLS指纹检测的 WAF)
        if config.get('use_cffi', False) and self.cffi_session:
            try:
                resp = self.cffi_session.get(url, timeout=30, verify=False, headers={
                    'Referer': config['base_url'],
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                })
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    items = extract_items(resp.text, config)
                    if items:
                        return items
            except Exception as e:
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
                last_err = e

        # 策略3: 普通请求(兜底)
        try:
            return super().fetch_list(site_key, level, **kwargs)
        except Exception as e:
            last_err = e
            raise

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
        except:
            pass
