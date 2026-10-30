#!/usr/bin/env python3
"""审计回归测试(llm-mcp 区域)—— 全部离线,不联网。

覆盖:
- LM-1 fetch_gov_doc_detail SSRF 防护(scheme/host 白名单 + 内网地址拒绝)
- LM-2 llm_triage: 非数值 confidence -> 0.0,clamp 到 [0,1],不抛异常
- LM-3 llm_extract._to_number 拒绝 bool
- LM-4 verify_quote: quote 不得跨句子边界
- LM-U1 _extract_json 优先取 category 合法的最后一个对象
- LM-U3 失败计数器加锁(并发自增不丢)
- LM-U4 MCP 工具 level 只接受 national/provincial,未知 level/站点返回 error 负载

运行:
    python scripts/test_audit_llm_mcp.py
    pytest scripts/test_audit_llm_mcp.py
"""
import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
SKILL_SCRIPTS = REPO_ROOT / 'skills' / 'policy-analyzer' / 'scripts'

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SKILL_SCRIPTS))

import llm_triage                                                   # noqa: E402
import llm_extract                                                  # noqa: E402


# ---------------- LM-1: SSRF ----------------

class TestSsrfGuard(unittest.TestCase):
    """LM-1: fetch_gov_doc_detail 只允许白名单主机,拒绝内网/IP 字面量"""

    def _call(self, url, base_url=''):
        import mcp_server
        # fetch_detail 一旦被调用就说明没拦住 —— 让它显式失败
        with mock.patch.object(mcp_server, 'get_fetcher') as gf:
            fetcher = mock.MagicMock()
            fetcher.fetch_detail.side_effect = AssertionError(
                'fetch_detail 被调用了,SSRF 未拦截')
            gf.return_value = fetcher
            return mcp_server.fetch_gov_doc_detail(url, base_url)

    def test_rejects_metadata_endpoint(self):
        r = self._call('http://169.254.169.254/latest/meta-data/')
        self.assertIn('error', r)
        self.assertFalse(r.get('has_content', False))

    def test_rejects_loopback(self):
        r = self._call('http://127.0.0.1:8080/')
        self.assertIn('error', r)

    def test_rejects_localhost_name(self):
        r = self._call('http://localhost:9000/x')
        self.assertIn('error', r)

    def test_rejects_private_rfc1918(self):
        r = self._call('http://10.1.2.3/x')
        self.assertIn('error', r)

    def test_rejects_non_http_scheme(self):
        r = self._call('file:///etc/passwd')
        self.assertIn('error', r)

    def test_rejects_unlisted_public_host(self):
        r = self._call('http://evil.example.com/x')
        self.assertIn('error', r)

    def test_rejects_private_base_url_arg(self):
        r = self._call('https://www.gov.cn/zhengce/a.htm',
                       base_url='http://169.254.169.254/')
        self.assertIn('error', r)

    def test_allows_gov_cn_host(self):
        import mcp_server
        with mock.patch.object(mcp_server, 'get_fetcher') as gf:
            fetcher = mock.MagicMock()
            fetcher.fetch_detail.return_value = {'has_content': True, 'url': 'x'}
            gf.return_value = fetcher
            # monkeypatch DNS,避免真实解析(用明确的公网地址)
            with mock.patch.object(
                    socket, 'getaddrinfo',
                    return_value=[(2, 1, 6, '', ('8.8.8.8', 80))]):
                r = mcp_server.fetch_gov_doc_detail(
                    'https://www.gov.cn/zhengce/a.htm')
        self.assertTrue(fetcher.fetch_detail.called)
        self.assertTrue(r.get('has_content'))


# ---------------- LM-2: confidence 容错 ----------------

class TestTriageConfidence(unittest.TestCase):
    """LM-2: 非数值/越界 confidence 不得抛异常"""

    def setUp(self):
        llm_triage.reset_failure_state()

    def tearDown(self):
        llm_triage.reset_failure_state()

    def _run(self, parsed):
        env = {'POLICY_LLM_API_KEY': 'sk-x'}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(llm_triage, 'chat_json', return_value=parsed):
                return llm_triage.classify_triage_with_llm(
                    't', 'body', 'notice', [])

    def test_non_numeric_confidence_becomes_zero(self):
        r = self._run({'category': 'apply', 'reason': 'ok', 'confidence': 'high'})
        self.assertIsNotNone(r)
        self.assertEqual(r['confidence'], 0.0)

    def test_confidence_clamped_high(self):
        r = self._run({'category': 'apply', 'reason': 'ok', 'confidence': 5})
        self.assertEqual(r['confidence'], 1.0)

    def test_confidence_clamped_low(self):
        r = self._run({'category': 'apply', 'reason': 'ok', 'confidence': -2})
        self.assertEqual(r['confidence'], 0.0)

    def test_none_confidence_ok(self):
        r = self._run({'category': 'apply', 'reason': 'ok', 'confidence': None})
        self.assertEqual(r['confidence'], 0.0)


# ---------------- LM-3: _to_number 拒绝 bool ----------------

class TestToNumberBool(unittest.TestCase):
    """LM-3: bool 不是模型要的数字"""

    def test_true_rejected(self):
        self.assertIsNone(llm_extract._to_number(True))

    def test_false_rejected(self):
        self.assertIsNone(llm_extract._to_number(False))

    def test_real_number_still_ok(self):
        self.assertEqual(llm_extract._to_number(3), 3.0)
        self.assertEqual(llm_extract._to_number('12.5'), 12.5)

    def test_normalize_item_rejects_bool_value(self):
        text = '从业人员不少于1人'
        r = llm_extract.normalize_item(
            {'field': 'headcount', 'op': '>=', 'value': True,
             'quote': '从业人员不少于1人'}, text)
        self.assertIsNone(r)


# ---------------- LM-4: verify_quote 不跨句 ----------------

class TestVerifyQuoteSentenceBoundary(unittest.TestCase):
    """LM-4: quote 不得跨越句子终止符"""

    def test_cross_sentence_rejected(self):
        text = '企业须成立。满2年以上,并且营收达标。'
        self.assertFalse(llm_extract.verify_quote('成立满2年', text))

    def test_cross_semicolon_rejected(self):
        text = '企业须注册;成立满2年。'
        self.assertFalse(llm_extract.verify_quote('注册成立满2年', text))

    def test_within_sentence_still_ok(self):
        text = '申报企业须在本市注册成立满2年,上年度营业收入不低于1000万元。'
        self.assertTrue(llm_extract.verify_quote('成立满2年', text))
        self.assertTrue(llm_extract.verify_quote(
            '上年度营业收入不低于1000万元', text))

    def test_comma_bridge_still_ok(self):
        text = '注册资本不低于500万元,且研发人员占比达标。'
        self.assertTrue(llm_extract.verify_quote('注册资本不低于500万元', text))


# ---------------- LM-U1: _extract_json 取合法的最后一个 ----------------

class TestExtractJsonPrefersValid(unittest.TestCase):
    """LM-U1: 多对象时优先 category 合法的最后一个"""

    def test_prefers_last_valid_category(self):
        content = ('noise {"category":"news","reason":"decoy"} '
                   'and real {"category":"apply","reason":"r"}')
        r = llm_triage._extract_json(content)
        self.assertIsNotNone(r)
        self.assertEqual(r['category'], 'apply')

    def test_single_object_unchanged(self):
        r = llm_triage._extract_json('{"category":"regulate","reason":"r"}')
        self.assertEqual(r['category'], 'regulate')


# ---------------- LM-U3: 失败计数器加锁 ----------------

class TestFailureCounterLock(unittest.TestCase):
    """LM-U3: 并发自增不丢(加锁)"""

    def test_lock_exists(self):
        self.assertTrue(hasattr(llm_triage, '_failure_lock'))

    def test_concurrent_record_failure_no_lost_update(self):
        llm_triage.reset_failure_state()
        n_threads, per = 20, 50

        def worker():
            for _ in range(per):
                llm_triage._record_failure()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(llm_triage._failure_count, n_threads * per)
        llm_triage.reset_failure_state()


# ---------------- LM-U4: level 白名单 ----------------

class TestLevelWhitelist(unittest.TestCase):
    """LM-U4: level 只能 national/provincial,未知 -> error 负载"""

    def test_list_sites_unknown_level(self):
        import mcp_server
        r = mcp_server.list_available_sites(level='../../etc')
        self.assertIn('error', r)

    def test_list_sites_known_level_ok(self):
        import mcp_server
        r = mcp_server.list_available_sites(level='national')
        self.assertNotIn('error', r)
        self.assertGreater(r.get('count', 0), 0)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    unittest.main(verbosity=2)
