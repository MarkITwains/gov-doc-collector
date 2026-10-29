#!/usr/bin/env python3
"""detail_extractor 审计回归测试(全部离线)。

固化 DETAIL-1..6 修复,可用 pytest 或直接运行:
    python scripts/test_audit_detail.py
    pytest scripts/test_audit_detail.py

导入风格与 scripts/test_regressions.py 一致(直接导入被测模块)。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

from detail_extractor import extract_detail  # noqa: E402


LONG = '正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文正文' * 6  # >100 字


class TestDetail1Table(unittest.TestCase):
    """DETAIL-1: 表格 td/th 文本必须进入 content_text(逐行,单元格空格连接),
    且不重复收集 <td><p>..</p></td>。"""

    def test_table_rows_in_content_text(self):
        html = (f'<html><body><div class="TRS_Editor">'
                f'<p>关于发布补助标准的通知。{LONG}</p>'
                '<table><tr><td>项目</td><td>金额</td></tr>'
                '<tr><td>研发补助</td><td>最高500万元</td></tr>'
                '<tr><td>设备补助</td><td>最高200万元</td></tr></table>'
                '<p>请遵照执行。</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a/b.html')
        txt = r['content_text']
        self.assertIn('研发补助', txt)
        self.assertIn('最高500万元', txt)
        self.assertIn('设备补助', txt)
        # 同一行的单元格以空格连接
        self.assertIn('研发补助 最高500万元', txt)

    def test_td_with_p_not_double_counted(self):
        html = (f'<html><body><div id="content">'
                f'<p>{LONG}</p>'
                '<table><tr><td><p>甲单元格</p></td><td>乙单元格</td></tr></table>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a/b.html')
        txt = r['content_text']
        self.assertEqual(txt.count('甲单元格'), 1)
        self.assertEqual(txt.count('乙单元格'), 1)
        # 仍然同一行
        self.assertIn('甲单元格 乙单元格', txt)


class TestDetail2Attachments(unittest.TestCase):
    """DETAIL-2: 附件检测与 type 用 URL path,忽略 query string。"""

    def test_pdf_with_query_string_is_attachment(self):
        for href, ext in [('/f/a.pdf?id=9', 'pdf'), ('/f/b.PDF?v=2', 'pdf')]:
            html = (f'<html><body><div id="content"><p>{LONG}</p>'
                    f'<p><a href="{href}">附件</a></p></div></body></html>')
            r = extract_detail(html, 'https://x.gov.cn/art/1.html')
            urls = [a['url'] for a in r['attachments']]
            self.assertEqual(len(r['attachments']), 1, f'{href} 应被识别为附件')
            self.assertTrue(urls[0].endswith(href.split('?')[0].split('/')[-1]
                                             ) or href.split('?')[0] in urls[0])
            self.assertEqual(r['attachments'][0]['type'], ext,
                             f'{href} 的 type 应为 {ext},不含 query')

    def test_servlet_query_pdf_not_matched(self):
        html = (f'<html><body><div id="content"><p>{LONG}</p>'
                '<p><a href="/download.jsp?fileName=x.pdf">下载</a></p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/art/1.html')
        self.assertEqual(r['attachments'], [],
                         'download.jsp?fileName=x.pdf 不应被扩展名规则命中')

    def test_plain_extension_still_matched(self):
        html = (f'<html><body><div id="content"><p>{LONG}</p>'
                '<p><a href="/f/c.docx">通知附件.docx</a></p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/doc/b.html')
        self.assertEqual(len(r['attachments']), 1)
        self.assertEqual(r['attachments'][0]['type'], 'docx')


class TestDetail3Issuer(unittest.TestCase):
    """DETAIL-3: issuer 优先含机关名的 meta,拒绝人名/媒体署名。"""

    def test_organ_meta_wins_over_author(self):
        html = ('<html><head>'
                '<meta name="author" content="刘畅">'
                '<meta name="ContentSource" content="国务院办公厅">'
                f'</head><body><div id="content"><p>{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['issuer'], '国务院办公厅')

    def test_person_author_rejected_when_no_organ(self):
        html = ('<html><head><meta name="author" content="刘畅">'
                f'</head><body><div id="content"><p>{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertIsNone(r['metadata']['issuer'],
                          '只有人名署名时 issuer 应为 None,不采用人名')

    def test_media_byline_rejected(self):
        html = ('<html><head><meta name="author" content="新华社">'
                f'</head><body><div id="content"><p>{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertIsNone(r['metadata']['issuer'], '媒体署名不是发文机关')

    def test_department_meta_accepted(self):
        html = ('<html><head><meta name="Department" content="财政部">'
                f'</head><body><div id="content"><p>{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['issuer'], '财政部')


    def test_bank_and_customs_organs_accepted(self):
        # 央行/海关是真实发文机关,名字里没有 部/委/局 等字,也不能被当成人名丢掉
        for organ in ('中国人民银行', '上海海关'):
            html = (f'<html><head><meta name="ContentSource" content="{organ}">'
                    f'</head><body><div id="content"><p>{LONG}</p></div></body></html>')
            r = extract_detail(html, 'https://x.gov.cn/a.html')
            self.assertEqual(r['metadata']['issuer'], organ)


class TestDetail4IssueDate(unittest.TestCase):
    """DETAIL-4: 无标签 issue_date 从署名块兜底。"""

    def test_signature_block_date_fallback(self):
        html = ('<html><body><div id="content">'
                '<p>各省、自治区、直辖市人民政府:</p>'
                f'<p>现将规划印发给你们。{LONG}</p>'
                '<p style="text-align:right">国务院</p>'
                '<p style="text-align:right">2026年5月30日</p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['issue_date'], '2026-05-30')

    def test_date_only_line_fallback(self):
        html = ('<html><body><div id="content">'
                f'<p>现予公布。{LONG}</p>'
                '<p>2026年6月3日</p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['issue_date'], '2026-06-03')

    def test_inbody_reference_date_not_picked(self):
        # 正文中部引用的日期(非署名块尾部),不应被当作 issue_date
        html = ('<html><body><div id="content">'
                '<p>根据2019年1月1日印发的旧办法,现调整如下。</p>'
                f'<p>各单位要认真落实。{LONG}</p>'
                '<p>以上要求请遵照执行,不得有误,务必抓好落实工作到位。</p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertIsNone(r['metadata']['issue_date'])

    def test_labelled_date_still_wins(self):
        html = ('<html><body><div id="content">'
                '<p>成文日期: 2025年3月8日</p>'
                f'<p>{LONG}</p>'
                '<p>国务院</p><p>2026年5月30日</p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['issue_date'], '2025-03-08')


class TestDetail5DocNumber(unittest.TestCase):
    """DETAIL-5: 去掉裸 第N号 兜底,只接受 令-锚定形式。"""

    def test_bare_diNhao_not_matched(self):
        for body in ['会议第3号议题讨论了相关工作。',
                     '详见第 12 号文件。',
                     '排名第1号种子选手。']:
            html = (f'<html><body><div id="content"><p>{body}{LONG}</p>'
                    '</div></body></html>')
            r = extract_detail(html, 'https://x.gov.cn/a.html')
            self.assertIsNone(r['metadata']['doc_number'],
                              f'{body!r} 不应产生 doc_number')

    def test_ling_anchored_still_matched(self):
        html = (f'<html><body><div id="content">'
                f'<p>中华人民共和国国务院令第 123 号{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['doc_number'], '国务院令第 123 号')

    def test_bracket_docnum_still_matched(self):
        html = (f'<html><body><div id="content">'
                f'<p>国办发〔2024〕12号{LONG}</p></div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertEqual(r['metadata']['doc_number'], '国办发〔2024〕12号')


class TestDetail6Usable(unittest.TestCase):
    """DETAIL-6: content_usable = 非空文本 且 quality != low,与 100 字 has_content 解耦。"""

    def test_short_medium_notice_usable(self):
        short = '关于端午节放假的通知'
        html = (f'<html><body><div id="content"><p>{short}</p>'
                '<p>各处室:端午节放假一天。特此通知。</p>'
                '<p>附件:<a href="/f/a.pdf">放假安排.pdf</a></p>'
                '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertNotEqual(r['content_quality']['level'], 'low')
        self.assertTrue(r['content_usable'],
                        '短通知(medium)应 usable,即使 has_content=False')
        # has_content 语义不变
        self.assertFalse(r['has_content'])

    def test_empty_not_usable(self):
        html = '<html><body><div id="content"></div></body></html>'
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        self.assertFalse(r['content_usable'])

    def test_low_quality_not_usable(self):
        nav = '\n'.join(['政务公开', '政务服务', '互动交流', '专题专栏',
                         '政府信息公开指南', '政府信息公开目录', '领导信息',
                         '机构职能', '政策文件', '规划计划'])
        html = ('<html><body><div id="content">'
                + ''.join(f'<p>{w}</p>' for w in nav.split('\n'))
                + '</div></body></html>')
        r = extract_detail(html, 'https://x.gov.cn/a.html')
        if r['content_quality']['level'] == 'low':
            self.assertFalse(r['content_usable'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
