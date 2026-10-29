#!/usr/bin/env python3
"""审计回归测试(core-audit)— policy_parser 的 extract_validity / 数值与资金抽取修复。

离线、无网络、无 LLM(POLICY_LLM_* 保持未设置)。可用 pytest 或直接运行:
    python scripts/test_audit_parser.py
    pytest scripts/test_audit_parser.py

覆盖(与 review/parser-triage、review/parser-extract 报告一一对应):
- TRI-1/TRI-2  extract_validity 不把"注册成立/完成建设"类日期当申报截止
- TRI-3        中文数字日期(二〇二六年七月三十一日)可被解析
- TRI-4        "申报/受理时间为 DATE 起至 DATE" → deadline 取第二个日期
- TRI-5        extract_validity(None) 等价于 extract_validity('')
- EXT-1        数值区间(500-1000万元 / 5000万元至1亿元 / 500万~1000万元)→ 双界
- EXT-2        裸"万/亿"金额(最高奖励500万)识别为万元,保留资金动词上下文护栏
- EXT-4        同一子句重叠的数值匹配去重(达到1亿元以上 → 单条)
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
SKILL_SCRIPTS = REPO_ROOT / 'skills' / 'policy-analyzer' / 'scripts'

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SKILL_SCRIPTS))

from policy_parser import (extract_validity, classify_condition,  # noqa: E402
                           extract_funding)


def _conds(text):
    return [(c['field'], c['op'], c['value'], c['unit'])
            for c in classify_condition(text)]


class TestValidityTriage(unittest.TestCase):
    def test_tri1_eligibility_cutoff_not_deadline(self):
        """注册成立截止日不得被当成申报截止日"""
        v = extract_validity(
            '2024年1月1日前注册成立的企业方可申报。请于2026年7月1日前报送。')
        self.assertEqual(v['deadline'], '2026-07-01')

    def test_tri2_completion_milestone_not_deadline(self):
        """完成项目建设/验收的里程碑日不是申报截止日"""
        v = extract_validity('确保2025年12月31日前完成项目建设,并上报验收结果。')
        self.assertIsNone(v['deadline'])

    def test_tri3_chinese_numeral_dates(self):
        """中文数字日期可被解析(〇/零=0,十位日)"""
        v = extract_validity(
            '本办法自二〇二六年一月一日起施行,申报截止时间为二〇二六年七月'
            '三十一日,有效期至二〇二八年十二月三十一日。')
        self.assertEqual(v['effective_from'], '2026-01-01')
        self.assertEqual(v['deadline'], '2026-07-31')
        self.assertEqual(v['valid_until'], '2028-12-31')

    def test_tri4_acceptance_range_deadline(self):
        """申报/受理时间 DATE 起至 DATE → 截止取第二个日期"""
        v = extract_validity('申报受理时间为2026年3月1日起至2026年7月31日。')
        self.assertEqual(v['deadline'], '2026-07-31')

    def test_tri5_none_behaves_like_empty(self):
        """extract_validity(None) 不崩,等价于空串"""
        self.assertEqual(extract_validity(None), extract_validity(''))


class TestNumericAndFunding(unittest.TestCase):
    def test_ext1_hyphen_range(self):
        """500-1000万元 → >=500 且 <=1000,高值不得变成 >= 下限"""
        rev = [(c['op'], c['value'], c['unit']) for c in
               classify_condition('年营业收入500-1000万元')
               if c['field'] == 'revenue']
        self.assertEqual(rev, [('>=', 500.0, '万元'), ('<=', 1000.0, '万元')])

    def test_ext1_mixed_unit_range(self):
        """营业收入在5000万元至1亿元之间 → 双界,各自单位"""
        rev = [(c['op'], c['value'], c['unit']) for c in
               classify_condition('营业收入在5000万元至1亿元之间')
               if c['field'] == 'revenue']
        self.assertEqual(rev, [('>=', 5000.0, '万元'), ('<=', 1.0, '亿元')])

    def test_ext1_tilde_range(self):
        """500万~1000万元 → 双界"""
        rev = [(c['op'], c['value'], c['unit']) for c in
               classify_condition('营业收入500万~1000万元')
               if c['field'] == 'revenue']
        self.assertEqual(rev, [('>=', 500.0, '万元'), ('<=', 1000.0, '万元')])

    def test_ext2_bare_wan_funding(self):
        """最高奖励500万 → 识别为 500 万元(cap)"""
        got = [(f['kind'], f['value_wan']) for f in extract_funding('最高奖励500万')]
        self.assertEqual(got, [('cap', 500.0)])

    def test_ext2_bare_wan_funding2(self):
        """最高1000万 → 1000 万元(cap)"""
        got = [(f['kind'], f['value_wan']) for f in extract_funding('补助最高1000万。')]
        self.assertEqual(got, [('cap', 1000.0)])

    def test_ext2_context_guard_preserved(self):
        """非资金语境的"万"(平方米/万人)不得被当成资金"""
        self.assertEqual(extract_funding('项目占地500万平方米。'), [])
        self.assertEqual(extract_funding('带动就业10万人。'), [])

    def test_ext4_overlap_dedup(self):
        """年营收达到1亿元以上 → 只产生一条 revenue 条件"""
        rev = [(c['op'], c['value'], c['unit']) for c in
               classify_condition('年营收达到1亿元以上')
               if c['field'] == 'revenue']
        self.assertEqual(rev, [('>=', 1.0, '亿元')])

class TestIntegrationRegressions(unittest.TestCase):
    """集成复核时发现的回归:修 TRI-1/2 不能误伤最常见的截止写法。"""

    def test_common_deadline_phrasings_kept(self):
        for text in ('请于2026年7月31日前完成网上申报。',
                     '请各单位于2026年7月31日前在线申报。',
                     '项目申报单位应于2026年7月31日前登录系统填报。',
                     '申报单位须于2026年7月31日前将申报材料报送至市科技局。'):
            self.assertEqual(extract_validity(text)['deadline'], '2026-07-31', text)

    def test_as_of_statistics_date_not_deadline(self):
        """"截至X日,全市共有…"是统计口径,不是申报截止"""
        v = extract_validity('截至2025年12月31日，全市共认定高新技术企业1200家。')
        self.assertIsNone(v['deadline'])

    def test_labelled_and_trailing_deadlines(self):
        self.assertEqual(extract_validity('材料报送截止时间为2026年9月30日。')['deadline'],
                         '2026-09-30')
        self.assertEqual(extract_validity('本次申报工作于2026年6月30日截止。')['deadline'],
                         '2026-06-30')

    def test_calendar_year_range_not_numeric_bounds(self):
        """"2024-2026年"是年份区间,不能变成数值条件"""
        conds = _conds('营业收入在2024-2026年间年均增长10%以上')
        self.assertFalse(any(v in (2024.0, 2026.0) for _f, _o, v, _u in conds), conds)

    def test_growth_rate_percent_is_not_revenue_threshold(self):
        """营收增速 10% 不是营收门槛;不能当成 revenue >= 10"""
        rev = [c for c in _conds('营业收入年均增长不低于10%')
               if c[0] == 'revenue' and c[2] is not None]
        self.assertEqual(rev, [])



if __name__ == '__main__':
    unittest.main(verbosity=2)
