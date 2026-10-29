#!/usr/bin/env python3
"""审计回归测试(matcher)— 固化 MAT-1..MAT-4 + format_report 兜底修复。

全部离线,不需要网络 / LLM。可用 pytest 或直接运行:
    python scripts/test_audit_matcher.py
"""
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'scripts'))
sys.path.insert(0, str(REPO_ROOT / 'skills' / 'policy-analyzer' / 'scripts'))

from company_matcher import (match_policy, match_with_triage, check_condition,  # noqa: E402
                             format_report, _to_float, _company_age,
                             VERDICT_LABELS)
from policy_parser import parse_policy  # noqa: E402


_POLICY_TXT = """市工信局关于组织申报2026年度资助的通知
一、申报条件
(一)上年度营业收入不低于1000万元;
五、其他
申报截止时间为2026年7月31日。
"""


class TestMat2ToFloat(unittest.TestCase):
    """MAT-2: _to_float 解析文档约定的字符串形态。"""

    def test_plain_and_whitespace_comma(self):
        self.assertEqual(_to_float(3200), 3200.0)
        self.assertEqual(_to_float('3200'), 3200.0)
        self.assertEqual(_to_float(' 3200 '), 3200.0)
        self.assertEqual(_to_float('3,200'), 3200.0)

    def test_money_suffix_to_wan_base(self):
        self.assertEqual(_to_float('3200万'), 3200.0)
        self.assertEqual(_to_float('3200万元'), 3200.0)
        self.assertEqual(_to_float('1.2亿'), 12000.0)
        self.assertEqual(_to_float('1.2亿元'), 12000.0)
        self.assertEqual(_to_float('5000000元'), 500.0)  # 元→万元

    def test_headcount_and_age_suffix(self):
        self.assertEqual(_to_float('100人'), 100.0)
        self.assertEqual(_to_float('3年'), 3.0)
        self.assertEqual(_to_float('18个月'), 1.5)

    def test_reject_bool(self):
        self.assertIsNone(_to_float(True))
        self.assertIsNone(_to_float(False))

    def test_unparseable_returns_none(self):
        self.assertIsNone(_to_float('abc'))
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float(''))


class TestMat1NoCrash(unittest.TestCase):
    """MAT-1: 画像数值为字符串不再崩,解析失败降级为 unknown。"""

    def test_match_policy_string_revenue(self):
        parsed = parse_policy(_POLICY_TXT)
        m = match_policy(parsed, {'revenue': '3,200', 'headcount': 100},
                         today=date(2026, 1, 1))
        # 3,200万 >= 1000万 → pass,不抛异常
        self.assertNotEqual(m['verdict'], 'ineligible')
        rev_check = next(c for c in m['checks']
                         if c['condition'].get('field') == 'revenue')
        self.assertEqual(rev_check['result']['status'], 'pass')

    def test_check_condition_unparseable_is_unknown(self):
        cond = {'field': 'revenue', 'op': '>=', 'value': 1000, 'unit': '万元',
                'hard': True, 'text': '营收'}
        r = check_condition(cond, {'revenue': 'abc'})
        self.assertEqual(r['status'], 'unknown')

    def test_revenue_condition_with_percent_unit_needs_review(self):
        # 增速类条件(单位 %)不能和画像营收(万元)直接比,否则几乎所有企业都"通过"
        cond = {'field': 'revenue', 'op': '>=', 'value': 10, 'unit': '%',
                'hard': True, 'text': '营业收入年均增长不低于10%'}
        r = check_condition(cond, {'revenue': 3200})
        self.assertEqual(r['status'], 'review')


    def test_company_age_unparseable_returns_none(self):
        self.assertIsNone(_company_age({'company_age': 'abc'}))

    def test_company_age_string_months(self):
        self.assertEqual(_company_age({'company_age': '18个月'}), 1.5)


class TestMat3Deadline(unittest.TestCase):
    """MAT-3: 已过截止不得判为可申报。"""

    def test_past_deadline_expired(self):
        parsed = parse_policy(_POLICY_TXT)
        m = match_policy(parsed, {'revenue': 3200}, today='2026-10-29')
        self.assertEqual(m['verdict'], 'expired')
        self.assertEqual(m['eligibility_verdict'], 'eligible')
        self.assertTrue(m['deadline_passed'])
        self.assertEqual(m['deadline'], '2026-07-31')

    def test_future_deadline_not_expired(self):
        parsed = parse_policy(_POLICY_TXT)
        m = match_policy(parsed, {'revenue': 3200}, today='2026-01-01')
        self.assertEqual(m['verdict'], 'eligible')
        self.assertFalse(m['deadline_passed'])

    def test_no_deadline_passed_none(self):
        m = match_policy({'conditions': [], 'title': 'x'}, {'name': 'J'},
                         today='2026-10-29')
        self.assertIsNone(m['deadline_passed'])

    def test_ineligible_keeps_but_carries_flag(self):
        parsed = parse_policy(_POLICY_TXT)
        # 营收不满足 → ineligible,即使过期仍为 ineligible,但带 deadline_passed
        m = match_policy(parsed, {'revenue': 10}, today='2026-10-29')
        self.assertEqual(m['verdict'], 'ineligible')
        self.assertTrue(m['deadline_passed'])

    def test_expired_label_exists(self):
        self.assertIn('expired', VERDICT_LABELS)

    def test_format_report_shows_expired(self):
        parsed = parse_policy(_POLICY_TXT)
        m = match_policy(parsed, {'revenue': 3200}, today='2026-10-29')
        report = format_report(m)
        self.assertIn(VERDICT_LABELS['expired'], report)

    def test_triage_also_takes_today(self):
        parsed = parse_policy(_POLICY_TXT)
        m = match_with_triage(parsed, {'revenue': 3200}, today='2026-10-29')
        self.assertEqual(m['verdict'], 'expired')


class TestMat4NoneTitle(unittest.TestCase):
    """MAT-4: title=None 不再使 debug 日志崩溃。"""

    def test_none_title_no_crash(self):
        m = match_policy({'conditions': [], 'title': None}, {'name': 'J'})
        self.assertEqual(m['verdict'], 'uncertain')


class TestFormatReportFallback(unittest.TestCase):
    """format_report: 词表外 verdict/status 用兜底 label,不抛 KeyError。"""

    def test_unknown_verdict_fallback(self):
        report = format_report({
            'policy_title': 'x', 'verdict': '__bogus__', 'score': 1.0,
            'summary': {'pass': 0, 'fail': 0, 'soft_fail': 0, 'unknown': 0, 'review': 0},
            'checks': [], 'missing_fields': [], 'review_items': []})
        self.assertIn('__bogus__', report)

    def test_unknown_status_fallback(self):
        report = format_report({
            'policy_title': 'x', 'verdict': 'eligible', 'score': 1.0,
            'summary': {'pass': 0, 'fail': 0, 'soft_fail': 0, 'unknown': 0, 'review': 0},
            'checks': [{'condition': {'text': 'c'}, 'result': {'status': '__st__', 'reason': 'r'}}],
            'missing_fields': [], 'review_items': []})
        self.assertIsInstance(report, str)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    unittest.main(verbosity=2)
