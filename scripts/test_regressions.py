#!/usr/bin/env python3
"""回归测试(全部离线,不需要网络)— 固化 v1.6.0/v1.7.0 修复,可用 pytest 或直接运行。

覆盖:
- B1  README 导入路径 (from scripts.unified_fetcher import ...) + 直接脚本执行兼容
- B2  MCP 协议握手 (initialize / tools/list,JSON-RPC 2.0 封装 + id 回显)
- B4  RSS/Atom feed 链接提取 (parse_xml_feed 三元表达式优先级 bug)
- B5  详情页嵌套 div 不再重复抽取正文
- B5b 独立附件区容器(div.attachment-list)的附件不再被噪声清洗吞掉
- B6  申报条件不再被后续资金章节(支持标准 等)跨块污染
- B7  编码自动检测(不再硬编码 utf-8)
- B8  triage_method 反映实际分类路径
- B10 parse_json_api 支持数组下标、类型不匹配不崩溃
- B11 标题阈值可配置 + 日期归一化
- F2  分页 URL 构造(template / query 两种)
- F3  增量采集缓存 SeenStore
- F5  报告标注已过截止日
- F6  属地条件按发文机关推断(省/市两级)

运行:
    python scripts/test_regressions.py
    pytest scripts/test_regressions.py
"""
import json
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
SKILL_SCRIPTS = REPO_ROOT / 'skills' / 'policy-analyzer' / 'scripts'

# 离线直接导入被测模块
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SKILL_SCRIPTS))

from parser import parse_xml_feed, parse_json_api, normalize_date, extract_items  # noqa: E402
from detail_extractor import extract_detail                         # noqa: E402
from fetcher import GovDocFetcher, detect_encoding                  # noqa: E402
from seen_store import SeenStore                                    # noqa: E402
from policy_parser import parse_policy, extract_conditions, split_paragraphs  # noqa: E402
from company_matcher import match_policy, check_condition, format_report  # noqa: E402


def run_py(code: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-c', code], cwd=str(cwd),
                          capture_output=True, text=True, timeout=90,
                          encoding='utf-8', errors='replace')


class TestImportPaths(unittest.TestCase):
    """B1: README 宣称的导入路径必须可用,直接脚本执行也不能坏"""

    def test_readme_package_import(self):
        r = run_py('from scripts.unified_fetcher import UnifiedFetcher;'
                   'from scripts.fetcher import GovDocFetcher;'
                   'from scripts.detail_extractor import extract_detail;'
                   'print("OK")', cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('OK', r.stdout)

    def test_direct_script_flat_import(self):
        code = (f'import sys; sys.path.insert(0, {str(SCRIPTS_DIR)!r});'
                'import unified_fetcher, fetcher, parser, detail_extractor;'
                'print("OK")')
        r = run_py(code, cwd=REPO_ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('OK', r.stdout)

    def test_skill_package_import(self):
        code = ('from scripts.policy_parser import parse_policy;'
                'from scripts.company_matcher import match_policy;'
                'print("OK")')
        r = run_py(code, cwd=REPO_ROOT / 'skills' / 'policy-analyzer')
        self.assertEqual(r.returncode, 0, r.stderr)


class TestXmlFeed(unittest.TestCase):
    """B4: parse_xml_feed 运算符优先级 bug"""

    RSS = ('<?xml version="1.0"?><rss version="2.0"><channel>'
           '<item><title>关于XX工作的通知</title>'
           '<link>http://a.gov.cn/x.html</link>'
           '<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>'
           '</channel></rss>')

    ATOM = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>关于YY的公告</title>'
            '<link href="http://b.gov.cn/y.html"/>'
            '<published>2026-06-01T00:00:00Z</published></entry></feed>')

    def test_rss_link_preserved(self):
        items = parse_xml_feed(self.RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['link'], 'http://a.gov.cn/x.html')

    def test_atom_link_preserved(self):
        items = parse_xml_feed(self.ATOM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['link'], 'http://b.gov.cn/y.html')


class TestDetailExtractor(unittest.TestCase):
    """B5: 嵌套 div 重复抽取;独立附件区丢失"""

    def test_nested_div_no_duplication(self):
        html = ('<html><body><div id="content"><div class="TRS_Editor">'
                '<p>第一段正文内容,这是测试文本,用来验证嵌套容器不重复抽取。</p>'
                '<p>第二段正文内容,继续测试文本,确保超过阈值让容器被选中。</p>'
                '</div></div></body></html>')
        r = extract_detail(html, 'http://x.gov.cn/a.html')
        self.assertEqual(r['content_text'].count('第一段正文内容'), 1)
        self.assertEqual(r['content_text'].count('第二段正文内容'), 1)

    def test_attachments_in_separate_container(self):
        long_p = '正文内容测试。' * 30  # >100 字保证 has_content
        html = (f'<html><body><div id="content"><p>{long_p}</p></div>'
                '<div class="attachment-list">'
                '<a href="/files/a.pdf">实施细则附件.pdf</a></div>'
                '</body></html>')
        r = extract_detail(html, 'http://x.gov.cn/doc/b.html')
        urls = [a['url'] for a in r['attachments']]
        self.assertIn('http://x.gov.cn/files/a.pdf', urls)

    def test_inline_attachments_still_work(self):
        long_p = '正文内容测试。' * 30
        html = (f'<html><body><div id="content"><p>{long_p}</p>'
                '<p>附件:<a href="/f/c.docx">通知附件.docx</a></p></div>'
                '</body></html>')
        r = extract_detail(html, 'http://x.gov.cn/doc/b.html')
        urls = [a['url'] for a in r['attachments']]
        self.assertIn('http://x.gov.cn/f/c.docx', urls)


CONTAMINATION_SAMPLE = '''二、申报条件
申报企业须同时满足以下条件:
(一)在本市注册成立满2年;
(二)近三年无重大安全事故。

三、支持标准
经认定的企业,给予一次性奖励50万元;对首次获评国家级专精特新小巨人的,按其上年度研发投入的30%给予补助,最高不超过500万元。'''


class TestConditionContamination(unittest.TestCase):
    """B6: 资金章节(支持标准)不得污染申报条件"""

    def test_funding_text_not_in_conditions(self):
        conds = extract_conditions(split_paragraphs(CONTAMINATION_SAMPLE))
        for c in conds:
            self.assertNotIn('给予', c['text'],
                             f"资金描述混入条件: {c['text'][:60]}")
            self.assertNotIn('小巨人', c['text'])

    def test_real_conditions_still_extracted(self):
        conds = extract_conditions(split_paragraphs(CONTAMINATION_SAMPLE))
        fields = {c['field'] for c in conds}
        self.assertIn('company_age', fields)  # 合法条件仍然要能抽到


MATCHER_SAMPLE = """市工信局关于组织申报2026年度专精特新中小企业培育资助的通知

一、支持对象
在本市行政区域内注册登记、具有独立法人资格的中小企业。

二、申报条件
申报企业须同时满足以下条件:
(一)在本市注册成立满2年,且上年度营业收入不低于1000万元;
(二)从业人员不超过500人,研发费用占营业收入比例不低于4%;
(三)拥有有效发明专利2件以上,或获得高新技术企业、科技型中小企业认定;
(四)未被列入严重违法失信名单,近三年无重大安全生产事故。

三、支持标准
经认定的企业,给予一次性奖励50万元;对首次获评国家级专精特新"小巨人"的,
按其上年度研发投入的30%给予补助,最高不超过500万元。

五、其他
申报截止时间为2026年7月31日。
"""

MATCHER_PROFILE = {
    'name': '深圳市某某智能科技有限公司',
    'region': '深圳市南山区',
    'industry': '工业软件研发',
    'company_age': 4.5,
    'revenue': 3200,
    'headcount': 120,
    'rd_ratio': 8.5,
    'patents': 1,
    'qualifications': ['国家高新技术企业'],
    'credit_clean': True,
}


class TestMatcherVerdict(unittest.TestCase):
    """B6 端到端:资金段不再产生假资质 fail"""

    def test_no_fake_qualification_fail(self):
        parsed = parse_policy(MATCHER_SAMPLE, use_llm_triage=False)
        m = match_policy(parsed, MATCHER_PROFILE)
        for c in m['checks']:
            reason = c['result'].get('reason', '')
            self.assertNotIn('专精特新', reason,
                             f"资金描述被误判为资质条件: {reason}")
            self.assertNotIn('小巨人', reason)
        # (四)信用条款应当干净通过,而不是被污染后 fail
        credit_checks = [c for c in m['checks']
                         if c['condition']['field'] == 'credit']
        self.assertTrue(credit_checks)
        self.assertTrue(all(c['result']['status'] == 'pass'
                            for c in credit_checks))


class TestMCPProtocol(unittest.TestCase):
    """B2: MCP Server 必须说标准 MCP 协议(JSON-RPC 2.0 + initialize 握手)"""

    def test_initialize_and_tools_list(self):
        proc = subprocess.Popen(
            [sys.executable, '-u', str(SCRIPTS_DIR / 'mcp_server.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(REPO_ROOT))
        lines = queue.Queue()

        def reader():
            try:
                for raw in proc.stdout:
                    lines.put(raw)
            except ValueError:
                pass

        threading.Thread(target=reader, daemon=True).start()

        def send(obj):
            proc.stdin.write((json.dumps(obj) + '\n').encode('utf-8'))
            proc.stdin.flush()

        try:
            # 1) initialize 握手
            send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '2024-11-05',
                             'capabilities': {},
                             'clientInfo': {'name': 'regression-test',
                                            'version': '1.0'}}})
            resp = json.loads(lines.get(timeout=30))
            self.assertEqual(resp.get('jsonrpc'), '2.0')
            self.assertEqual(resp.get('id'), 1)
            self.assertIn('serverInfo', resp.get('result', {}))

            # 2) initialized 通知(服务端不应回包),然后 tools/list
            send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
            resp = json.loads(lines.get(timeout=30))
            self.assertEqual(resp.get('id'), 2)
            names = {t['name'] for t in resp['result']['tools']}
            self.assertTrue(
                {'fetch_gov_docs', 'list_available_sites',
                 'fetch_gov_doc_detail', 'fetch_gov_docs_with_details',
                 'fetch_new_gov_docs'} <= names,
                f'工具缺失: {names}')

            # 3) list_available_sites 离线可用(读本地配置,不发网络请求)
            send({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                  'params': {'name': 'list_available_sites',
                             'arguments': {'level': 'national'}}})
            resp = json.loads(lines.get(timeout=30))
            self.assertEqual(resp.get('id'), 3)
            content = resp['result']['content'][0]['text']
            data = json.loads(content)
            self.assertGreaterEqual(data['count'], 30)
        finally:
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


class TestEncodingDetection(unittest.TestCase):
    """B7: 编码自动检测(不再硬编码 utf-8)"""

    class FakeResp:
        def __init__(self, content: bytes, encoding=None):
            self.content = content
            self.encoding = encoding

    def test_header_charset_respected(self):
        r = self.FakeResp('任意'.encode('gbk'), encoding='GBK')
        self.assertEqual(detect_encoding(r), 'GBK')

    def test_requests_iso8859_default_falls_through(self):
        r = self.FakeResp('中文'.encode('utf-8'), encoding='ISO-8859-1')
        self.assertEqual(detect_encoding(r), 'utf-8')

    def test_meta_charset(self):
        html = b'<html><head><meta charset="gb2312"></head><body>x</body></html>'
        self.assertEqual(detect_encoding(self.FakeResp(html)), 'gb2312')

    def test_gbk_content_without_hint(self):
        r = self.FakeResp('各地要做好相关工作'.encode('gbk'))
        self.assertEqual(detect_encoding(r), 'gb18030')


class TestJsonApi(unittest.TestCase):
    """B10: parse_json_api 支持数组下标,类型不匹配不崩溃"""

    def test_array_index_path(self):
        data = json.dumps({'result': [{'list': [
            {'name': '政策A', 'url': 'http://a'}, {'name': '政策B', 'url': 'http://b'}]}]})
        items = parse_json_api(data, {
            'items_path': 'result[0].list',
            'fields': {'title': 'name', 'link': 'url'}})
        self.assertEqual([i['title'] for i in items], ['政策A', '政策B'])

    def test_missing_path_returns_empty(self):
        items = parse_json_api('{"data": []}', {'items_path': 'x.y.z', 'fields': {}})
        self.assertEqual(items, [])

    def test_field_path_missing_is_none(self):
        items = parse_json_api('{"data": [{"a": 1}]}',
                               {'items_path': 'data', 'fields': {'t': 'b.c'}})
        self.assertEqual(items, [{'t': None}])


class TestNormalizeDate(unittest.TestCase):
    """B11: 日期归一化为 ISO"""

    def test_formats(self):
        cases = {
            '2026-06-10': '2026-06-10',
            '2026/6/10': '2026-06-10',
            '2026.06.10': '2026-06-10',
            '2026年6月10日': '2026-06-10',
            '[2026-06-10]': '2026-06-10',
            '2026-06-10T08:00:00Z': '2026-06-10',
            'Mon, 01 Jun 2026 00:00:00 GMT': '2026-06-01',
        }
        for raw, expect in cases.items():
            self.assertEqual(normalize_date(raw), expect, raw)

    def test_no_year_kept_as_is(self):
        self.assertEqual(normalize_date('06-10'), '06-10')
        self.assertIsNone(normalize_date(None))


class TestTitleFilter(unittest.TestCase):
    """B11: 标题过滤阈值放宽到 6 且可配置"""

    HTML = ('<ul><li><a href="/a.html">XX公告事项</a></li>'
            '<li><a href="/b.html">导航</a></li>'
            '<li><a href="/c.html">关于加强XX工作的完整通知</a></li></ul>')
    CONFIG = {'base_url': 'http://x.gov.cn',
              'selectors': {'list': 'ul li', 'title': 'a', 'link': 'a@href'}}

    def test_default_threshold(self):
        items = extract_items(self.HTML, self.CONFIG)
        titles = [i['title'] for i in items]
        self.assertIn('XX公告事项', titles)     # 6 字 → 旧阈值(>10)会丢
        self.assertNotIn('导航', titles)        # 2 字噪声仍被过滤
        self.assertIn('关于加强XX工作的完整通知', titles)

    def test_configurable_threshold(self):
        cfg = dict(self.CONFIG, min_title_len=2)
        titles = [i['title'] for i in extract_items(self.HTML, cfg)]
        self.assertIn('导航', titles)


class TestPagination(unittest.TestCase):
    """F2: 分页 URL 构造(离线,不发请求)"""

    def setUp(self):
        self.fetcher = GovDocFetcher()

    def test_template_pagination(self):
        config = {'base_url': 'http://x.gov.cn',
                  'search_path': '/list/index.html',
                  'pagination': {'type': 'template',
                                 'url_template': '/list/index_{page}.html',
                                 'start': 1, 'max_pages': 3}}
        urls = self.fetcher._page_urls(config)
        self.assertEqual(urls, ['http://x.gov.cn/list/index.html',
                                'http://x.gov.cn/list/index_1.html',
                                'http://x.gov.cn/list/index_2.html'])

    def test_query_pagination(self):
        config = {'base_url': 'http://x.gov.cn',
                  'search_path': '/list?cat=1',
                  'pagination': {'type': 'query', 'param': 'page',
                                 'start': 2, 'max_pages': 2}}
        urls = self.fetcher._page_urls(config)
        self.assertEqual(urls, ['http://x.gov.cn/list?cat=1',
                                'http://x.gov.cn/list?cat=1&page=2'])

    def test_override_and_default_single_page(self):
        config = {'base_url': 'http://x.gov.cn', 'search_path': '/list',
                  'pagination': {'type': 'query', 'max_pages': 5}}
        self.assertEqual(len(self.fetcher._page_urls(config, max_pages=1)), 1)
        no_pag = {'base_url': 'http://x.gov.cn', 'search_path': '/list'}
        self.assertEqual(self.fetcher._page_urls(no_pag),
                         ['http://x.gov.cn/list'])


class TestSeenStore(unittest.TestCase):
    """F3: 增量采集缓存"""

    def test_filter_new_and_persist(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'seen.json'
            store = SeenStore(path=path)
            items = [{'title': 'A', 'link': 'http://a'},
                     {'title': 'A2', 'link': 'http://a'},   # 同批次重复链接
                     {'title': 'B', 'link': 'http://b'}]
            new1 = store.filter_new('ndrc', items)
            store.save()
            self.assertEqual(len(new1), 2)  # 批次内去重

            # 重新加载(模拟下次 cron 运行)→ 全部已见
            store2 = SeenStore(path=path)
            self.assertEqual(store2.filter_new('ndrc', items), [])
            # 新链接只报增量
            items.append({'title': 'C', 'link': 'http://c'})
            new3 = store2.filter_new('ndrc', items)
            self.assertEqual([i['link'] for i in new3], ['http://c'])
            # 站点隔离
            self.assertEqual(len(store2.filter_new('mof', items)), 3)


REGION_COND = {'text': '在本市注册', 'field': 'region', 'op': 'in',
               'value_text': '在本市注册', 'hard': True,
               'needs_llm_review': False}


class TestRegionInference(unittest.TestCase):
    """F6: 属地条件用发文机关推断,减少无谓 review"""

    def test_province_policy_city_profile_pass(self):
        # 广东省政策 + 深圳企业(经 城市→省 映射)
        r = check_condition(REGION_COND, {'region': '深圳市南山区'},
                            {'issuer': '广东省工业和信息化厅', 'title': '关于组织申报的通知'})
        self.assertEqual(r['status'], 'pass', r['reason'])

    def test_city_policy_same_city_pass(self):
        r = check_condition(REGION_COND, {'region': '深圳市南山区'},
                            {'issuer': '深圳市工业和信息化局', 'title': ''})
        self.assertEqual(r['status'], 'pass', r['reason'])

    def test_city_policy_other_city_fail(self):
        r = check_condition(REGION_COND, {'region': '广州市天河区'},
                            {'issuer': '深圳市工业和信息化局', 'title': ''})
        self.assertEqual(r['status'], 'fail', r['reason'])

    def test_municipality_pass(self):
        r = check_condition(REGION_COND, {'region': '北京市海淀区'},
                            {'issuer': '北京市经济和信息化局', 'title': ''})
        self.assertEqual(r['status'], 'pass', r['reason'])

    def test_unknown_issuer_stays_review(self):
        r = check_condition(REGION_COND, {'region': '深圳市南山区'},
                            {'issuer': None, 'title': '关于组织申报的通知'})
        self.assertEqual(r['status'], 'review')


class TestDeadlineAwareness(unittest.TestCase):
    """F5: 报告标注已过截止日的政策"""

    def _match(self, deadline):
        return {'policy_title': '测试政策', 'verdict': 'likely', 'score': 50.0,
                'deadline': deadline,
                'summary': {'pass': 1, 'fail': 0, 'soft_fail': 0,
                            'unknown': 0, 'review': 0},
                'checks': [], 'funding': [], 'missing_fields': [],
                'review_items': []}

    def test_past_deadline_warned(self):
        report = format_report(self._match('2026-01-01'))
        self.assertIn('已过申报截止日', report)

    def test_future_deadline_no_warning(self):
        report = format_report(self._match('2099-12-31'))
        self.assertNotIn('已过申报截止日', report)


class TestTriageMethodLabel(unittest.TestCase):
    """B8: triage_method 必须反映实际分类路径"""

    def test_no_api_key_labels_rules(self):
        import os
        if os.environ.get('POLICY_LLM_API_KEY'):
            self.skipTest('本机配置了 LLM key,无法离线验证')
        r = parse_policy('正文', title='关于组织申报XX资助的通知',
                         use_llm_triage=True)
        self.assertEqual(r['triage_method'], 'rules')

    def test_forced_rules_labels_rules(self):
        r = parse_policy('正文', title='关于组织申报XX资助的通知',
                         use_llm_triage=False)
        self.assertEqual(r['triage_method'], 'rules')


if __name__ == '__main__':
    unittest.main(verbosity=2)
