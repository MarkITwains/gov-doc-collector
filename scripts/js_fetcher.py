#!/usr/bin/env python3
"""支持JavaScript渲染的增强采集器"""
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional
from fetcher import GovDocFetcher

warnings.filterwarnings('ignore')

class JSFetcher(GovDocFetcher):
    """支持JavaScript渲染的采集器"""

    def __init__(self, *args, use_js=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_js = use_js
        self.browser = None
        self.playwright = None
        self.context = None

    def _init_browser(self):
        """初始化浏览器"""
        if self.browser is None:
            try:
                from playwright.sync_api import sync_playwright
                self.playwright = sync_playwright().start()
                self.browser = self.playwright.chromium.launch(headless=True)
                self.context = self.browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='zh-CN'
                )
            except ImportError:
                print("警告: playwright未安装，无法使用JS渲染")
                print("安装命令: pip install playwright && playwright install chromium")
                self.use_js = False

    def fetch_with_js(self, url: str, timeout: int = 30000, wait_for: str = 'networkidle') -> str:
        """使用浏览器获取页面"""
        self._init_browser()
        if not self.context:
            return ""

        page = self.context.new_page()
        try:
            page.goto(url, timeout=timeout, wait_until=wait_for)
            page.wait_for_timeout(1500)
            content = page.content()
            return content
        finally:
            page.close()

    def fetch_list(self, site_key: str, level: str = "national", force_js: bool = False, **kwargs) -> List[Dict]:
        """采集文档列表（支持JS渲染）"""
        config = self.load_config(site_key, level)
        if not config:
            raise ValueError(f"站点 {site_key} 未配置")

        url = config['base_url'] + config['search_path']
        need_js = force_js or self.use_js or config.get('need_js', False)

        if need_js:
            try:
                html_content = self.fetch_with_js(url, timeout=40000, wait_for='load')
                if html_content:
                    from parser import extract_items
                    return extract_items(html_content, config)
            except Exception as e:
                print(f"JS渲染失败 {site_key}: {e}")

        return super().fetch_list(site_key, level, **kwargs)

    def close(self):
        """显式关闭浏览器"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.browser = None
        self.playwright = None
        self.context = None

    def __del__(self):
        """清理浏览器资源"""
        try:
            self.close()
        except:
            pass

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 测试JS渲染
    fetcher = JSFetcher(use_js=True)

    # 测试需要JS渲染的站点
    js_sites = [
        ('miit', 'national'),
        ('mohrss', 'national'),
        ('mwr', 'national'),
        ('zhejiang', 'provincial')
    ]

    print("测试JS渲染站点:\n")
    for site_key, level in js_sites:
        try:
            items = fetcher.fetch_list(site_key, level, force_js=True)
            print(f"✓ {site_key}: {len(items)} 条")
        except Exception as e:
            print(f"✗ {site_key}: {str(e)[:50]}")
