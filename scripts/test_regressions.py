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
- P1  policy-analyzer 修复:非法 LLM 配置不崩、triage 误判(监管/名单类通知)、
      划型类资质(中小企业)假 fail、上下界条件丢失、funding 上限跨句误判

运行:
    python scripts/test_regressions.py
    pytest scripts/test_regressions.py
"""
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

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
from policy_candidate import (score_candidate, is_apply_candidate,  # noqa: E402
                              split_candidates, classify_channel_by_url)
from unified_fetcher import UnifiedFetcher                          # noqa: E402
from policy_parser import (parse_policy, parse_from_detail,           # noqa: E402
                           extract_conditions, split_paragraphs)
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


REGION_COND_FOR_FIX = {'text': '在本市注册', 'field': 'region', 'op': 'in',
                       'value_text': '在本市注册', 'hard': True,
                       'needs_llm_review': False}


class TestPolicyAnalyzerFixes(unittest.TestCase):
    """P1: 解析/匹配层修复固化(v1.5.0)"""

    def test_invalid_llm_timeout_does_not_crash(self):
        """非法 POLICY_LLM_TIMEOUT 不能让整条解析链路崩掉,只降级走规则"""
        import os
        from unittest import mock
        env = {'POLICY_LLM_API_KEY': 'sk-test',
               'POLICY_LLM_TIMEOUT': '30s',       # 常见误写
               'POLICY_LLM_BASE_URL': 'http://127.0.0.1:9/v1'}
        with mock.patch.dict(os.environ, env, clear=False):
            try:
                r = parse_policy('正文', title='关于组织申报XX资助的通知')
            finally:
                try:
                    import llm_triage
                    llm_triage.reset_failure_state()
                except ImportError:
                    pass
        self.assertEqual(r['triage_method'], 'rules')

    def test_regulate_notice_with_boilerplate_condition(self):
        """监管检查通知 + 句级兜底条件("应当具备…")不得被判成申报类"""
        doc = ('关于开展2026年工业领域数据安全检查工作的通知\n'
               '各有关企业:\n'
               '企业应当具备完善的数据安全管理制度,并按要求报送自查材料。\n')
        r = parse_policy(doc, use_llm_triage=False)
        self.assertEqual(r['triage_category'], 'regulate')

    def test_result_notice_is_not_apply(self):
        """公布名单类通知是申报结果,不是申报入口"""
        doc = ('某市关于公布2026年度重点企业名单的通知\n'
               '经审核,企业须同时具备独立法人资格和纳税信用A级。\n')
        self.assertEqual(parse_policy(doc, use_llm_triage=False)['triage_category'],
                         'other')

    def test_plan_title_not_apply(self):
        """"计划/规划"这类弱词不足以把无文种标题判成申报类"""
        from policy_parser import _classify_triage_by_rules
        self.assertEqual(
            _classify_triage_by_rules('XX市2026年国民经济和社会发展计划', '', [], '其他'),
            'other')

    def test_size_qualification_not_hard_fail(self):
        """【中小企业】是划型指标而非认证:画像没列不应直接 fail"""
        doc = ('某市关于组织申报中小企业扶持资金的通知\n\n'
               '一、支持对象\n'
               '在本市行政区域内注册登记、具有独立法人资格的中小企业。\n\n'
               '二、申报条件\n'
               '(一)上年度营业收入不低于500万元;\n'
               '(二)从业人员不超过300人。\n')
        parsed = parse_policy(doc, use_llm_triage=False)
        m = match_policy(parsed, {'region': '深圳市南山区', 'revenue': 3200,
                                  'headcount': 120, 'qualifications': []})
        quals = [c['result']['status'] for c in m['checks']
                 if c['condition']['field'] == 'qualification']
        self.assertEqual(quals, ['review'])
        self.assertNotEqual(m['verdict'], 'ineligible')

    def test_size_qualification_over_limit_fails(self):
        """确实超出划型上限时仍要判 fail"""
        cond = {'text': '中小企业', 'field': 'qualification', 'op': 'has',
                'value': ['中小企业'], 'hard': True, 'needs_llm_review': False}
        r = check_condition(cond, {'revenue': 500000, 'headcount': 5000})
        self.assertEqual(r['status'], 'fail', r['reason'])

    def test_both_bounds_extracted(self):
        """一条条件里的上下界都要抽出来,不能只留第一个"""
        from policy_parser import classify_condition
        head = [(c['op'], c['value'], c['unit']) for c in
                classify_condition('企业职工人数不少于20人且不超过300人')
                if c['field'] == 'headcount']
        self.assertEqual(head, [('>=', 20.0, '人'), ('<=', 300.0, '人')])
        age = [(c['op'], c['value']) for c in
               classify_condition('成立满2年且成立不超过10年')
               if c['field'] == 'company_age']
        self.assertEqual(age, [('>=', 2.0), ('<=', 10.0)])

    def test_adjacent_field_numbers_not_mixed(self):
        """相邻字段的数值不能串到同一字段上"""
        from policy_parser import classify_condition
        got = [(c['field'], c['value']) for c in
               classify_condition('从业人员不超过500人,研发费用占营业收入比例不低于4%')]
        self.assertEqual(got, [('rd_ratio', 4.0), ('headcount', 500.0)])

    def test_funding_cap_not_crossing_clause(self):
        """固定金额不能被后一小句的"最高不超过"标成上限"""
        from policy_parser import extract_funding
        got = [(f['kind'], f['value_wan']) for f in extract_funding(
            '对首次认定的企业给予一次性奖励50万元,最高不超过500万元。')]
        self.assertEqual(got, [('fixed', 50.0), ('cap', 500.0)])

    def test_format_report_tolerates_missing_value_wan(self):
        report = format_report({'policy_title': 't', 'verdict': 'likely', 'score': 1.0,
                                'summary': {'pass': 0, 'fail': 0, 'soft_fail': 0,
                                            'unknown': 0, 'review': 0},
                                'checks': [], 'missing_fields': [], 'review_items': [],
                                'funding': [{'kind': 'cap', 'text': 'x'}],
                                'support_measures': []})
        self.assertIn('(金额未解析)', report)

    def test_inline_numeric_enum_split(self):
        """同一行内空格分隔的 "1. … 2. …" 也要拆开"""
        from policy_parser import split_enum_items
        self.assertEqual(split_enum_items('1. 申报书 2. 营业执照'),
                         ['1. 申报书', '2. 营业执照'])

    def test_province_policy_city_mention_in_title(self):
        """标题中段提到某城市,不应把省级政策降级为市级(旧实现会误判 fail)"""
        r = check_condition(REGION_COND_FOR_FIX, {'region': '广州市天河区'},
                            {'issuer': '广东省工业和信息化厅',
                             'title': '关于推广深圳市经验的实施意见'})
        self.assertEqual(r['status'], 'pass', r['reason'])
        # 市级政策(城市名在发文机关)仍照旧按城市判定
        r2 = check_condition(REGION_COND_FOR_FIX, {'region': '广州市天河区'},
                             {'issuer': '深圳市工业和信息化局', 'title': ''})
        self.assertEqual(r2['status'], 'fail', r2['reason'])

    def test_only_soft_fail_is_not_eligible(self):
        """唯一没满足的是软性条件时,不能给 eligible"""
        parsed = {'title': 't', 'validity': {}, 'funding': [], 'support_measures': [],
                  'conditions': [{'text': '优先支持营收不低于100万元', 'field': 'revenue',
                                  'op': '>=', 'value': 100, 'unit': '万元',
                                  'hard': False, 'any_mode': False}]}
        self.assertEqual(match_policy(parsed, {'revenue': 50})['verdict'], 'likely')

    def test_parse_from_detail_empty_validity_shape(self):
        """空输入返回的 validity 结构与正常解析一致"""
        from policy_parser import parse_from_detail
        empty = parse_from_detail({})
        self.assertEqual(sorted(empty['validity']),
                         sorted(parse_policy('随便一段正文')['validity']))
        self.assertEqual(empty['parse_status'], 'empty_input')

    def test_credit_business_abnormal_list(self):
        """'未被列入经营异常名录' 也应识别为信用类条件(Eval P/R 发现的漏抽)"""
        from policy_parser import classify_condition
        fields = {c['field'] for c in classify_condition('未被列入经营异常名录')}
        self.assertIn('credit', fields)

    def test_regulation_doc_with_apply_section_is_apply(self):
        """管理办法里内嵌'申报条件'章节 → 按申报类处理(不是一律 regulate)"""
        doc = ('某市新能源汽车推广应用专项资金管理办法\n\n'
               '第二章 申报\n第八条 申报条件如下:\n'
               '(一)在本市注册的整车生产企业;\n'
               '(二)上年度营业收入不低于500万元。\n')
        self.assertEqual(parse_policy(doc, use_llm_triage=False)['triage_category'],
                         'apply')
        # 但纯规范类条文仍为 regulate
        law = ('中华人民共和国数据安全法\n\n第一章 总则\n'
               '第一条 为了规范数据处理活动,保障数据安全,制定本法。\n')
        self.assertEqual(parse_policy(law, use_llm_triage=False)['triage_category'],
                         'regulate')


class FakeDetailFetcher(UnifiedFetcher):
    """离线替身:不初始化 cffi/playwright,不联网,只验证筛选逻辑。"""

    def __init__(self, items, config):
        self._fake_items = items
        self._fake_config = config

    def load_config(self, site_key, level='national'):
        return self._fake_config

    def fetch_list(self, site_key, level='national', max_pages=None, **kwargs):
        return [dict(i) for i in self._fake_items]

    def fetch_detail(self, url, base_url='', use_cffi=False, need_js=False):
        return {'url': url, 'content_text': '正文' * 60, 'has_content': True,
                'attachments': [], 'metadata': {}, 'word_count': 120}


class TestCandidateFilter(unittest.TestCase):
    """P0-2: 抓详情前先筛"申报候选"(高精度)"""

    def test_apply_titles_selected(self):
        for t in ['关于组织申报2026年度专精特新中小企业培育资助的通知',
                  '关于征集2026年数字化转型试点项目的通知',
                  '关于推荐申报国家重点研发计划项目的通知']:
            self.assertTrue(is_apply_candidate(t, 'https://x.gov.cn/tzgg/a.html'), t)

    def test_non_apply_titles_rejected(self):
        cases = {
            '关于开展2026年工业领域数据安全检查工作的通知': '监管检查',
            '2026年度重点企业名单公示': '结果公示',
            'XX市所属事业单位2026年公开招聘工作人员公告': '招聘',
            '国家语言文字工作委员会工作职责': '栏目名',
            '中共中央、国务院印发《教育强国建设规划纲要》': '新闻',
        }
        for t, why in cases.items():
            self.assertFalse(is_apply_candidate(t, 'https://x.gov.cn/tzgg/a.html'),
                             f'{why} 被误判为候选: {t}')

    def test_regulatory_doc_rejected(self):
        # 规范/监管类文种:约束行为,不是申报入口
        for t in ['关于印发《XX市古树名木保护办法》的通知',
                  'XX省行政执法过错责任追究办法',
                  '中华人民共和国安全生产法']:
            self.assertFalse(is_apply_candidate(t, 'https://x.gov.cn/zcfg/a.html'), t)

    def test_support_doc_is_candidate(self):
        # 支持类"办法"**就是**申报入口(企业按办法申报),不能一刀切当规范类
        for t in ['关于印发《黄浦区中小微企业贷款贴息支持办法》的通知',
                  '关于印发《XX市产业发展专项资金管理办法》的通知']:
            self.assertTrue(is_apply_candidate(t, 'https://x.gov.cn/zcfg/a.html'), t)

    def test_law_channel_still_allows_apply_title(self):
        r = score_candidate('关于组织申报2026年度高新技术企业的通知',
                            'https://x.gov.cn/zcfg/a.html')
        self.assertEqual(r['decision'], 'apply', r['reasons'])

    def test_channel_prior(self):
        self.assertEqual(classify_channel_by_url('https://x.gov.cn/tzgg/a.html'), 'notice')
        self.assertEqual(classify_channel_by_url('https://x.gov.cn/shenbao/a.html'), 'apply')
        self.assertEqual(classify_channel_by_url('https://x.gov.cn/xwzx/a.html'), 'news')
        # 栏目声明为申报专区 → 整栏目放行(栏目本身比标题可靠)
        self.assertTrue(is_apply_candidate(
            'XX市2026年第二批资金项目', 'https://x.gov.cn/a/b.html', channel_type='apply'))

    def test_split_candidates_tags_items(self):
        items = [{'title': '关于组织申报XX资金的通告', 'link': 'https://x.gov.cn/tzgg/1.html'},
                 {'title': 'XX市召开经济工作会议', 'link': 'https://x.gov.cn/tzgg/2.html'},
                 {'title': '无链接条目', 'link': ''}]
        cands, skipped = split_candidates(items)
        self.assertEqual(len(cands), 1)
        self.assertEqual(len(skipped), 2)
        self.assertIn('candidate', cands[0])
        no_link = [it for it in skipped if not it.get('link')]
        self.assertEqual(no_link[0]['candidate']['reasons'], ['无有效链接'])


class TestMultiColumnConfig(unittest.TestCase):
    """P0-1: search_path 支持多栏目(申报公告分散在不同栏目)"""

    def setUp(self):
        self.fetcher = GovDocFetcher()

    def test_columns_normalization(self):
        self.assertEqual(self.fetcher._columns({'search_path': '/a/'}), ['/a/'])
        self.assertEqual(self.fetcher._columns({'search_path': ['/a/', 'a/', '/b/']}),
                         ['/a/', '/b/'])
        self.assertEqual(self.fetcher._columns({'search_path': '/a/',
                                                'extra_paths': ['/b/']}),
                         ['/a/', '/b/'])
        self.assertEqual(self.fetcher._columns({}), ['/'])

    def test_multi_column_page_urls(self):
        config = {'base_url': 'http://x.gov.cn',
                  'search_path': ['/tzgg/', '/zcsb/'],
                  'pagination': {'type': 'template', 'url_template': '/tzgg/index_{page}.html',
                                 'start': 1, 'max_pages': 2}}
        urls = self.fetcher._page_urls(config)
        # 模板只属于 /tzgg/;/zcsb/ 不匹配模板 → 只采第 1 页(不翻到别的栏目上)
        self.assertEqual(urls, ['http://x.gov.cn/tzgg/', 'http://x.gov.cn/tzgg/index_1.html',
                                'http://x.gov.cn/zcsb/'])
        self.assertEqual(self.fetcher._page_urls(config, column='/tzgg/'),
                         ['http://x.gov.cn/tzgg/', 'http://x.gov.cn/tzgg/index_1.html'])

    def test_multi_column_with_column_placeholder(self):
        config = {'base_url': 'http://x.gov.cn',
                  'search_path': ['/tzgg/', '/zcsb/'],
                  'pagination': {'type': 'template', 'url_template': '{column}index_{page}.html',
                                 'start': 1, 'max_pages': 2}}
        self.assertEqual(self.fetcher._page_urls(config),
                         ['http://x.gov.cn/tzgg/', 'http://x.gov.cn/tzgg/index_1.html',
                          'http://x.gov.cn/zcsb/', 'http://x.gov.cn/zcsb/index_1.html'])

    def test_fetch_list_iterates_columns_and_tags_source_path(self):
        fetcher = GovDocFetcher()
        pages = {
            'http://x.gov.cn/tzgg/': [],
            'http://x.gov.cn/zcsb/': [{'title': '关于组织申报XX资金的通知',
                                       'link': 'http://x.gov.cn/zcsb/1.html'}],
        }
        fetcher._fetch_page = lambda url, config: pages.get(url, [])
        config = {'base_url': 'http://x.gov.cn', 'search_path': ['/tzgg/', '/zcsb/']}
        fetcher.load_config = lambda site_key, level='national': config
        items = fetcher.fetch_list('x', 'national')
        # 第一个栏目空页不影响第二个栏目继续采集
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['source_path'], '/zcsb/')


class TestDetailPrefilter(unittest.TestCase):
    """P0-2: fetch_list_with_details(only_apply=True) 只为候选抓详情"""

    BASE = {'base_url': 'http://x.gov.cn', 'need_js': True}

    ITEMS = [
        {'title': '关于组织申报2026年度专精特新企业资助的通知',
         'link': 'http://x.gov.cn/tzgg/1.html'},
        {'title': '应急管理部季度例行新闻发布会', 'link': 'http://x.gov.cn/tzgg/2.html'},
        {'title': '2026年度重点企业名单公示', 'link': 'http://x.gov.cn/tzgg/3.html'},
        {'title': '关于征集2026年数字化转型试点项目的通知',
         'link': 'http://x.gov.cn/zcsb/4.html'},
    ]

    def test_only_apply_fetches_candidates_only(self):
        f = FakeDetailFetcher(self.ITEMS, dict(self.BASE))
        items = f.fetch_list_with_details('x', 'national', limit=5, only_apply=True)
        fetched = [it for it in items if it.get('detail')]
        self.assertEqual([it['link'] for it in fetched],
                         ['http://x.gov.cn/tzgg/1.html', 'http://x.gov.cn/zcsb/4.html'])
        skipped = [it for it in items if it.get('skip_reason')]
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all(it.get('detail') is None for it in skipped))

    def test_limit_applies_to_candidates(self):
        f = FakeDetailFetcher(self.ITEMS, dict(self.BASE))
        items = f.fetch_list_with_details('x', 'national', limit=1, only_apply=True)
        fetched = [it for it in items if it.get('detail')]
        self.assertEqual(len(fetched), 1)
        over = [it for it in items if 'limit' in (it.get('skip_reason') or '')]
        self.assertEqual(len(over), 1)

    def test_default_keeps_old_behaviour(self):
        """不传 only_apply → 保持"前 N 条全抓"的旧行为(向后兼容)"""
        f = FakeDetailFetcher(self.ITEMS, dict(self.BASE))
        items = f.fetch_list_with_details('x', 'national', limit=2)
        fetched = [it for it in items if it.get('detail')]
        self.assertEqual(len(fetched), 2)
        self.assertFalse(any(it.get('skip_reason') for it in items))


NAV_PAGE_TEXT = """无障碍浏览 | 网站地图 | 主办单位:某某委员会 | ICP备12345678号
首页 工作职责 联系我们 相关链接
工作职责 拟定国家语言文字工作的方针、政策,制订语言文字工作中长期规划
工作职责 拟定国家语言文字工作的方针、政策,制订语言文字工作中长期规划
委员单位 中央宣传部 中央统战部 中央网信办
首页 工作职责 联系我们 相关链接
"""

NORMAL_POLICY_TEXT = """某市关于组织申报2026年度专精特新企业培育资助的通知

各有关单位:
为加快培育专精特新中小企业,现组织开展2026年度培育资助申报工作,
现将有关事项通知如下。

一、支持对象
在本市行政区域内注册登记、具有独立法人资格的中小企业。

二、申报条件
申报企业须同时满足以下条件:
(一)在本市注册成立满2年,上年度营业收入不低于1000万元;
(二)从业人员不超过500人,研发费用占营业收入比例不低于4%;
(三)拥有有效发明专利2件以上,或获得高新技术企业认定;
(四)未被列入严重违法失信名单。

三、支持标准
经认定的企业给予一次性奖励50万元;对首次获评国家级专精特新小巨人的,
按其上年度研发投入的30%给予补助,最高不超过500万元。

四、申报材料
(一)申报书;
(二)营业执照复印件;
(三)上年度审计报告及纳税证明。

五、其他事项
申报截止时间为2026年7月31日,逾期不予受理。
"""


class TestContentQuality(unittest.TestCase):
    """P1-1: 正文质量门禁(识别错采的导航页/目录页)"""

    def test_nav_page_is_low(self):
        from detail_extractor import assess_content_quality
        q = assess_content_quality(NAV_PAGE_TEXT)
        self.assertEqual(q['level'], 'low', q)
        self.assertLess(q['score'], 55)

    def test_normal_policy_is_high(self):
        from detail_extractor import assess_content_quality
        q = assess_content_quality(NORMAL_POLICY_TEXT)
        self.assertEqual(q['level'], 'high', q)

    def test_empty_text_is_low(self):
        from detail_extractor import assess_content_quality
        self.assertEqual(assess_content_quality('')['level'], 'low')

    def test_extract_detail_exposes_quality_fields(self):
        html = (f'<html><body><div id="content"><p>{NORMAL_POLICY_TEXT}</p>'
                '</div></body></html>')
        r = extract_detail(html, 'http://x.gov.cn/a.html')
        self.assertIn('content_quality', r)
        self.assertIn('content_usable', r)
        self.assertTrue(r['content_usable'])
        self.assertEqual(r['content_quality']['level'], 'high')

    def test_parser_passes_quality_through(self):
        low = {'score': 20, 'level': 'low', 'flags': ['导航/模板词']}
        parsed = parse_from_detail({'content_text': NORMAL_POLICY_TEXT,
                                    'content_quality': low,
                                    'content_usable': False})
        self.assertEqual(parsed['content_quality'], low)
        self.assertFalse(parsed['content_usable'])


SAMPLE_FOR_LLM = """某市关于组织申报2026年度绿色工厂的通知

一、申报条件
(一)在本市注册成立满3年;
(二)已通过清洁生产审核。
"""


class TestLlmExtract(unittest.TestCase):
    """P1-2: LLM 条件抽取补漏(quote 回验 + 缓存 + 正则优先合并)"""

    TEXT = '申报企业须在本市注册成立满2年,上年度营业收入不低于1000万元。'

    def test_verify_quote_rejects_hallucination(self):
        from llm_extract import verify_quote
        self.assertTrue(verify_quote('成立满2年', self.TEXT))
        self.assertTrue(verify_quote('上年度营业收入不低于1000万元', self.TEXT))
        self.assertFalse(verify_quote('从业人员不超过500人', self.TEXT))
        self.assertFalse(verify_quote('', self.TEXT))
        self.assertFalse(verify_quote('成立', self.TEXT))  # 太短不算证据

    def test_normalize_item_whitelist(self):
        from llm_extract import normalize_item
        ok = normalize_item({'field': 'revenue', 'op': '>=', 'value': 1000,
                             'hard': True, 'quote': '营业收入不低于1000万元'}, self.TEXT)
        self.assertEqual(ok['unit'], '万元')
        self.assertIsNone(normalize_item({'field': 'hack', 'op': '>=', 'value': 1,
                                          'quote': '成立满2年'}, self.TEXT))
        self.assertIsNone(normalize_item({'field': 'revenue', 'op': 'has', 'value': 1,
                                          'quote': '成立满2年'}, self.TEXT))
        self.assertIsNone(normalize_item({'field': 'revenue', 'op': '>=', 'value': 'x',
                                          'quote': '成立满2年'}, self.TEXT))

    def _mock_env(self, tmpdir):
        return mock.patch.dict(os.environ, {
            'POLICY_LLM_API_KEY': 'sk-test',
            'POLICY_LLM_CACHE_DIR': str(tmpdir),
        }, clear=False)

    LLM_REPLY = {'conditions': [
        {'field': 'company_age', 'op': '>=', 'value': 3, 'hard': True,
         'quote': '成立满3年'},                              # 与正则重复 → 不加
        {'field': 'qualification', 'op': 'has', 'value': ['绿色工厂'], 'hard': True,
         'quote': '已通过清洁生产审核'},                      # 新增 → 采纳
        {'field': 'headcount', 'op': '<=', 'value': 500, 'hard': True,
         'quote': '从业人员不超过500人'},                    # 正文里没有 → 丢弃
    ]}

    def test_merge_with_mocked_llm(self):
        import tempfile
        import llm_extract
        with tempfile.TemporaryDirectory() as td, self._mock_env(td), \
                mock.patch.object(llm_extract, 'chat_json', return_value=self.LLM_REPLY):
            baseline = parse_policy(SAMPLE_FOR_LLM, use_llm_triage=False,
                                    use_llm_extract=False)
            self.assertEqual(baseline['extract_method'], 'rules')
            parsed = parse_policy(SAMPLE_FOR_LLM, use_llm_triage=False,
                                  use_llm_extract=True)
        self.assertEqual(parsed['extract_method'], 'rules+llm')
        self.assertEqual(parsed['llm_extract_added'], 1)
        fields = {(c['field'], c['op']) for c in parsed['conditions']}
        self.assertIn(('qualification', 'has'), fields)       # LLM 补的
        self.assertNotIn('headcount', {c['field'] for c in parsed['conditions']})
        self.assertGreater(len(parsed['conditions']), len(baseline['conditions']))
        llm_cond = [c for c in parsed['conditions'] if c.get('source') == 'llm'][0]
        self.assertTrue(llm_cond['needs_llm_review'] is False)

    def test_cache_hit_avoids_second_request(self):
        import tempfile
        import llm_extract
        with tempfile.TemporaryDirectory() as td, self._mock_env(td), \
                mock.patch.object(llm_extract, 'chat_json',
                                  return_value=self.LLM_REPLY) as mocked:
            first = llm_extract.extract_conditions_with_llm(
                '标题', SAMPLE_FOR_LLM, '通知', existing=[])
            second = llm_extract.extract_conditions_with_llm(
                '标题', SAMPLE_FOR_LLM, '通知', existing=[])
        self.assertEqual(mocked.call_count, 1)          # 第二次命中缓存
        self.assertEqual([c['field'] for c in first], [c['field'] for c in second])

    def test_merge_prefers_rules_on_conflict(self):
        from policy_parser import merge_llm_conditions
        rules = [{'field': 'revenue', 'op': '>=', 'value': 1000, 'text': '规则'}]
        llm = [{'field': 'revenue', 'op': '>=', 'value': 1000, 'text': '模型'},
               {'field': 'patents', 'op': '>=', 'value': 2, 'text': '模型2'}]
        merged, added = merge_llm_conditions(rules, llm)
        self.assertEqual(added, 1)
        self.assertEqual([c['text'] for c in merged], ['规则', '模型2'])

    def test_llm_extract_off_by_default(self):
        parsed = parse_policy(SAMPLE_FOR_LLM, use_llm_triage=False)
        self.assertEqual(parsed['extract_method'], 'rules')
        self.assertEqual(parsed['llm_extract_added'], 0)


CHANNEL_PAGE_HTML = """
<html><body>
<div class="nav">
  <a href="/xwzx/">新闻动态</a>
  <a href="/xwzx/">新闻动态</a>
</div>
<ul class="menu">
  <li><a href="/tzgg/">通知公告</a></li>
  <li><a href="/zcsb/">项目申报</a></li>
  <li><a href="/zhengce/zhengcefagui/">政策法规</a></li>
  <li><a href="/gywm/gzzz.html">工作职责</a></li>
  <li><a href="https://other.example.com/tzgg/">外站链接</a></li>
  <li><a href="javascript:void(0)">展开</a></li>
</ul>
</body></html>
"""


class TestChannelProbe(unittest.TestCase):
    """P0-1: 栏目探测工具(离线跑解析与排序逻辑)"""

    def test_extract_channels_offline(self):
        from find_channels import extract_channels
        chans = extract_channels(CHANNEL_PAGE_HTML, 'https://x.gov.cn/')
        urls = [c['url'] for c in chans]
        self.assertIn('https://x.gov.cn/tzgg/', urls)
        self.assertNotIn('https://other.example.com/tzgg/', urls)  # 外站过滤
        self.assertFalse(any('javascript' in u for u in urls))

    def test_apply_and_notice_channels_rank_first(self):
        from find_channels import extract_channels
        top = extract_channels(CHANNEL_PAGE_HTML, 'https://x.gov.cn/')
        self.assertEqual(top[0]['url'], 'https://x.gov.cn/zcsb/')  # apply 栏目得分最高
        scores = {c['url']: c['score'] for c in top}
        self.assertGreater(scores['https://x.gov.cn/zcsb/'],
                           scores['https://x.gov.cn/xwzx/'])

    def test_score_channel_reasons(self):
        from find_channels import score_channel
        r = score_channel('项目申报', 'https://x.gov.cn/zcsb/')
        self.assertEqual(r['channel'], 'apply')
        self.assertGreater(r['score'], 0)
        r2 = score_channel('工作职责', 'https://x.gov.cn/gywm/gzzz.html')
        self.assertLess(r2['score'], r['score'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
