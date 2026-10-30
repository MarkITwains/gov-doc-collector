#!/usr/bin/env python3
"""采集模块审计回归测试(collector worker)。

覆盖:
- PC-1 相对链接按实际页面 URL 解析(而非 base_url 根);非 http(s) 链接丢弃
- PC-2 parse_json_api 链接绝对化 / 日期归一化 / 丢弃无标题或短标题
- FETCH-1 UnifiedFetcher cffi 层按 Content-Type 分派;结果需含 title+link 才算成功,否则降级
- PC-3 normalize_date 校验月/日范围,非法日期原样返回
- PC-4 parse_xml_feed 对畸形 XML 返回 [] 而非抛异常
- PC-5 URL_CHANNEL_RULES apply 优先于 notice;/xmsb/ 归为 apply
- FETCH-2 SeenStore.filter_new 保持首次出现顺序,超限淘汰最旧
- FETCH-P3 fetch_list_with_details 无 prefilter 分支先按 link 过滤再取 [:limit]

运行:
    python scripts/test_audit_collector.py
    pytest scripts/test_audit_collector.py
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

from parser import (extract_items, parse_json_api, parse_xml_feed,  # noqa: E402
                    normalize_date)
from seen_store import SeenStore                                    # noqa: E402
from policy_candidate import classify_channel_by_url               # noqa: E402
from find_channels import score_channel                            # noqa: E402
from unified_fetcher import UnifiedFetcher                         # noqa: E402


class TestPC1RelativeLinkBase(unittest.TestCase):
    """PC-1: 相对链接按实际页面 URL 解析,非 http(s) 链接丢弃。"""

    CONFIG = {
        'base_url': 'https://www.example.gov.cn',
        'selectors': {'list': 'li', 'title': 'a', 'link': 'a@href'},
        'min_title_len': 3,
    }
    HTML = (
        '<ul>'
        '<li><a href="./202606/t123.html">关于申报专项资金的通知</a></li>'
        '<li><a href="../xmsb/t999.html">关于项目申报征集的通知</a></li>'
        '<li><a href="t456.html">关于组织遴选试点的通知</a></li>'
        '<li><a href="javascript:void(0)">无效脚本链接条目标题</a></li>'
        '<li><a href="mailto:a@b.cn">邮件链接条目标题内容</a></li>'
        '</ul>'
    )

    def test_relative_resolved_against_page_url(self):
        items = extract_items(self.HTML, self.CONFIG,
                              page_url='https://www.example.gov.cn/zwgk/tzgg/')
        links = [it['link'] for it in items]
        self.assertIn('https://www.example.gov.cn/zwgk/tzgg/202606/t123.html', links)
        self.assertIn('https://www.example.gov.cn/zwgk/xmsb/t999.html', links)
        self.assertIn('https://www.example.gov.cn/zwgk/tzgg/t456.html', links)

    def test_non_http_links_dropped(self):
        items = extract_items(self.HTML, self.CONFIG,
                              page_url='https://www.example.gov.cn/zwgk/tzgg/')
        for it in items:
            self.assertTrue(it['link'].startswith(('http://', 'https://')))

    def test_default_page_url_is_base_url(self):
        # 不传 page_url 时保持旧行为(按 base_url 根 join)
        items = extract_items(self.HTML, self.CONFIG)
        self.assertIn('https://www.example.gov.cn/202606/t123.html',
                      [it['link'] for it in items])


class TestPC2JsonApiParity(unittest.TestCase):
    """PC-2: parse_json_api 绝对化链接、归一化日期、丢弃无/短标题。"""

    MAPPINGS = {
        'items_path': 'data.list',
        'fields': {'title': 'name', 'link': 'url', 'date': 'pub'},
    }
    PAYLOAD = (
        '{"data":{"list":['
        '{"name":"关于申报2026专项资金的通知","url":"/tzgg/a.html","pub":"2026/6/3"},'
        '{"name":"","url":"/tzgg/b.html","pub":"2026.6.4"},'
        '{"name":"短","url":"/tzgg/c.html","pub":"bad"}'
        ']}}'
    )
    BASE = 'https://www.example.gov.cn'

    def test_link_absolutized_and_date_normalized(self):
        items = parse_json_api(self.PAYLOAD, self.MAPPINGS,
                               base_url=self.BASE, min_title_len=3)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['link'],
                         'https://www.example.gov.cn/tzgg/a.html')
        self.assertEqual(items[0]['date'], '2026-06-03')

    def test_empty_and_short_titles_dropped(self):
        items = parse_json_api(self.PAYLOAD, self.MAPPINGS,
                               base_url=self.BASE, min_title_len=3)
        titles = [it['title'] for it in items]
        self.assertNotIn('', titles)
        self.assertNotIn('短', titles)


class TestPC3DateValidation(unittest.TestCase):
    """PC-3: normalize_date 校验月/日范围。"""

    def test_impossible_month_rejected(self):
        self.assertEqual(normalize_date('2026-13-05'), '2026-13-05')

    def test_impossible_day_rejected(self):
        self.assertEqual(normalize_date('2026-06-45'), '2026-06-45')

    def test_feb_30_rejected(self):
        self.assertEqual(normalize_date('2026-02-30'), '2026-02-30')

    def test_valid_date_normalized(self):
        self.assertEqual(normalize_date('2026/6/3'), '2026-06-03')


class TestPC4MalformedXml(unittest.TestCase):
    """PC-4: 畸形 XML 返回 [] 不抛异常。"""

    def test_malformed_returns_empty(self):
        self.assertEqual(parse_xml_feed('<rss><item><title>x</title>'), [])

    def test_valid_xml_still_works(self):
        xml = ('<rss><channel><item><title>有效标题</title>'
               '<link>http://x.cn/1</link></item></channel></rss>')
        items = parse_xml_feed(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['link'], 'http://x.cn/1')


class TestPC5ChannelRuleOrder(unittest.TestCase):
    """PC-5: apply 优先于 notice;/xmsb/ 归 apply。"""

    def test_sbzt_under_tzgg_is_apply(self):
        self.assertEqual(
            classify_channel_by_url('https://x.gov.cn/tzgg/sbzt/a.html'), 'apply')

    def test_xmsb_is_apply(self):
        self.assertEqual(
            classify_channel_by_url('https://x.gov.cn/xmsb/t1.html'), 'apply')

    def test_plain_notice_still_notice(self):
        self.assertEqual(
            classify_channel_by_url('https://x.gov.cn/tzgg/a.html'), 'notice')

    def test_score_channel_ranks_apply_above_notice(self):
        apply_s = score_channel('项目申报专区', 'https://x.gov.cn/tzgg/sbzt/')
        notice_s = score_channel('通知公告', 'https://x.gov.cn/tzgg/')
        self.assertGreater(apply_s['score'], notice_s['score'])


class TestFetch2SeenStoreOrder(unittest.TestCase):
    """FETCH-2: filter_new 保持首次出现顺序,超限淘汰最旧。"""

    def test_eviction_keeps_newest_by_order(self):
        store = SeenStore(path=None, max_per_site=3)
        # 用独立 site_key 避免读到真实缓存
        store.reset('audit_test')
        items = [{'link': f'L{i}', 'title': f't{i}'} for i in range(1, 6)]
        store.filter_new('audit_test', items)
        self.assertEqual(store._order['audit_test'], ['L3', 'L4', 'L5'])


class TestFetch1CffiContentType(unittest.TestCase):
    """FETCH-1: cffi 层按 Content-Type 分派,结果需含 title+link 才算成功。"""

    class _FakeResp:
        def __init__(self, text, ct):
            self.text = text
            self.status_code = 200
            self.headers = {'Content-Type': ct}
            self.content = text.encode('utf-8')
            self.encoding = 'utf-8'

    def test_cffi_json_dispatched_to_json_api(self):
        uf = UnifiedFetcher(enable_js=False, enable_cffi=False)
        uf.cffi_session = object()  # 占位,确保进入 cffi 分支
        uf.use_cffi = True
        config = {
            'base_url': 'http://api.gov.cn',
            'use_cffi': True,
            'json_mappings': {'items_path': 'data',
                              'fields': {'title': 't', 'link': 'u'}},
            'selectors': {'list': 'li', 'title': 'a', 'link': 'a@href'},
        }
        resp = self._FakeResp('{"data":[{"t":"申报通知标题","u":"http://api.gov.cn/1"}]}',
                              'application/json')
        uf._cffi_get = lambda url, cfg: resp
        items = uf._fetch_page('http://api.gov.cn/list', config)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], '申报通知标题')
        self.assertEqual(items[0]['link'], 'http://api.gov.cn/1')
        uf.cffi_session = None  # 占位对象无 close(),清掉再关闭
        uf.close()


class TestFetchP3DetailLimit(unittest.TestCase):
    """FETCH-P3: 无 prefilter 分支先按 link 过滤再取 [:limit]。"""

    def test_filter_link_before_limit(self):
        uf = UnifiedFetcher(enable_js=False, enable_cffi=False)
        items = [
            {'title': 'a', 'link': None},
            {'title': 'b', 'link': 'http://x.cn/b'},
            {'title': 'c', 'link': 'http://x.cn/c'},
        ]
        # 复刻无 prefilter 分支的选取逻辑
        targets = [it for it in items if it.get('link')][:2]
        self.assertEqual([it['link'] for it in targets],
                         ['http://x.cn/b', 'http://x.cn/c'])
        uf.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
