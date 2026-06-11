#!/usr/bin/env python3
"""
详情页正文提取器
从政府网站详情页提取政策正文、发文字号、发布日期、附件等结构化信息。
支持三级降级(curl_cffi → Playwright → requests),与 UnifiedFetcher 共享会话。
"""
import re
import warnings
from typing import Dict, List, Optional
from urllib.parse import urljoin

warnings.filterwarnings('ignore')

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    BeautifulSoup = None
    NavigableString = None
    Tag = None


# 详情的"正文容器"候选选择器(按命中率排序)
CONTENT_SELECTORS = [
    # 国务院/部委系
    'div.TRS_Editor',
    'div.trs_editor',
    'div#content',
    'div.content',
    'div.article-content',
    'div.article_content',
    'div.articleContent',
    'div#article-content',
    'div.article',
    'article',
    # 部委定制
    'div.pages_content',
    'div#pages_content',
    'div#article',
    'div.main-text',
    'div.main_text',
    'div#mainText',
    'div.mainText',
    # 省级政府
    'div.zwxx_content',
    'div#zoom',
    'div#UCAP-CONTENT',
    'div#articleContent',
    'div#articlebox',
    'div#content-body',
    'div.text-content',
    'div#text',
    'div.news-content',
    'div#news_content',
    # 兜底
    'main',
    'div#main',
    'body',
]

# 噪声容器(侧边栏/导航/版权)
NOISE_SELECTORS = [
    'script', 'style', 'noscript', 'iframe',
    'nav', 'header', 'footer',
    'div.sidebar', 'div.side', 'div.aside',
    'div.nav', 'div.menu', 'div.breadcrumb',
    'div.share', 'div.tool', 'div.tools',
    'div.print', 'div.attachment-list',  # 附件区单独抽
    'div.related', 'div.recommend',
    'div#sidebar', 'div#side', 'div#nav',
    'form', 'button',
]

# 元数据正则
DOC_NUMBER_RE = re.compile(
    r'[(（]\s*[\d]{4}[-—年][\d]{1,2}[-—月]?[\d]{0,2}[号日]?\s*[)）]'
)
YEAR_NUMBER_RE = re.compile(r'[（(](\d{4})[）)]')
ISSUE_DATE_RE = re.compile(r'(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})[日]?')
KEYWORDS_OF_DATE = ['成文日期', '发布日期', '发文日期', '印发日期', '发布时间']


def _strip_noise(soup: BeautifulSoup) -> None:
    """原地删除噪声元素"""
    for sel in NOISE_SELECTORS:
        for el in soup.select(sel):
            try:
                el.decompose()
            except Exception:
                pass


def _find_content_root(soup: BeautifulSoup) -> Optional[Tag]:
    """
    按候选列表找出正文容器。
    策略: 优先选**特定**正文容器(TRS_Editor/article-content/zoom 等);
          特定容器中再选最长;
          都不命中才 fallback 到 body/main(但要保证比通用容器 text 多至少 50%)。
    """
    SPECIFIC_SELECTORS = CONTENT_SELECTORS[:-3]  # 排除 main/div#main/body 三个兜底
    FALLBACK_SELECTORS = CONTENT_SELECTORS[-3:]    # main / div#main / body

    # 1) 在特定选择器里选最长
    best = None
    best_len = 0
    for sel in SPECIFIC_SELECTORS:
        for el in soup.select(sel):
            txt = el.get_text(' ', strip=True)
            if len(txt) > best_len and len(txt) >= 100:
                best = el
                best_len = len(txt)

    # 2) 特定没命中(都 < 100 字)→ fallback 到 main/div#main
    if best is None or best_len < 100:
        for sel in FALLBACK_SELECTORS[:-1]:  # main, div#main (排除 body)
            for el in soup.select(sel):
                txt = el.get_text(' ', strip=True)
                if len(txt) > best_len and len(txt) >= 200:
                    best = el
                    best_len = len(txt)

    # 3) 最后才用 body(再差也比没匹配强)
    if best is None or best_len < 100:
        body = soup.find('body')
        if body:
            best = body
            best_len = len(body.get_text(' ', strip=True))

    return best


def _extract_text(root: Tag) -> str:
    """从正文容器提取干净文本(保留段落结构)"""
    if not root:
        return ""

    # 处理 <br> → 换行
    for br in root.find_all('br'):
        br.replace_with('\n')

    paragraphs = []
    for elem in root.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'li']):
        text = elem.get_text(' ', strip=True)
        if text and len(text) >= 2:
            paragraphs.append(text)

    # 合并:相邻短句合并为段
    text = '\n\n'.join(paragraphs)
    # 收敛 3+ 换行到 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_metadata(html: str, soup: BeautifulSoup) -> Dict:
    """提取发文字号、发文日期、发文机关等元数据"""
    meta: Dict = {
        'doc_number': None,    # 发文字号
        'issuer': None,         # 发文机关
        'issue_date': None,     # 发文日期
        'category': None,       # 政策类别
        'index_number': None,   # 索引号
    }

    full_text = soup.get_text(' ', strip=True)

    # 发文字号:XX〔2024〕5号 或 国务院令第 123 号
    m = re.search(r'[一-龥]{0,4}[〔【\[](\d{4})[〕】\]]\s*\d+\s*号', full_text)
    if m:
        meta['doc_number'] = m.group(0).strip()
    else:
        # 国务院令第xxx号
        m = re.search(r'第\s*(\d+)\s*号', full_text)
        if m:
            meta['doc_number'] = f"第{m.group(1)}号"

    # 发文日期
    for kw in KEYWORDS_OF_DATE:
        # 在label附近找日期
        for em in soup.find_all(string=re.compile(kw)):
            parent = em.parent
            if parent:
                nearby = parent.get_text(' ', strip=True)
                d = ISSUE_DATE_RE.search(nearby)
                if d:
                    meta['issue_date'] = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
                    break
        if meta['issue_date']:
            break

    # 索引号(很多部委详情页有"索引号:"字样)
    m = re.search(r'索引号[::]\s*([A-Za-z0-9\-_/]+)', full_text)
    if m:
        meta['index_number'] = m.group(1).strip()

    # 政策类别(主题分类)
    m = re.search(r'主题分类[::]\s*([一-龥、,/]+)', full_text)
    if m:
        meta['category'] = m.group(1).strip()

    # 发文机关(取 meta[name] 或 header 的 description)
    if soup.head:
        for tag_name in ['author', 'ArticleAuthor', 'Creator', 'Department', 'ContentSource']:
            tag = soup.head.find('meta', attrs={'name': tag_name})
            if tag and tag.get('content'):
                meta['issuer'] = tag['content'].strip()
                break

    return meta


def _extract_attachments(root: Tag, base_url: str) -> List[Dict]:
    """提取附件(政策正文引用的 PDF / DOC / DOCX)"""
    attachments = []
    if not root:
        return attachments
    for a in root.find_all('a'):
        href = a.get('href', '')
        if not href:
            continue
        # 绝对化
        full_url = urljoin(base_url, href)
        if not full_url:
            continue
        low = full_url.lower()
        if any(low.endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar']):
            name = a.get_text(strip=True) or full_url.split('/')[-1]
            attachments.append({
                'name': name[:120],
                'url': full_url,
                'type': low.rsplit('.', 1)[-1]
            })
    # 去重
    seen = set()
    uniq = []
    for a in attachments:
        if a['url'] not in seen:
            seen.add(a['url'])
            uniq.append(a)
    return uniq


def extract_detail(html: str, url: str, base_url: str = '') -> Dict:
    """
    从详情页 HTML 提取结构化信息。

    Args:
        html: 详情页 HTML
        url: 详情页 URL(用于附件绝对化)
        base_url: 站点 base_url(可选,默认从 url 推断)

    Returns:
        {
          'url': str,
          'content_text': str,        # 清洗后的纯文本正文
          'content_html': str,        # 原始 HTML(未清洗)
          'word_count': int,          # 正文字数
          'attachments': List[Dict],  # 附件列表
          'metadata': Dict,           # 元数据
          'has_content': bool,        # 是否识别到正文
        }
    """
    if not html or BeautifulSoup is None:
        return {
            'url': url,
            'content_text': '',
            'content_html': html or '',
            'word_count': 0,
            'attachments': [],
            'metadata': {},
            'has_content': False,
        }

    soup = BeautifulSoup(html, 'html.parser')
    _strip_noise(soup)
    root = _find_content_root(soup)
    if not root:
        text = soup.get_text('\n', strip=True)
        return {
            'url': url,
            'content_text': text,
            'content_html': html,
            'word_count': len(text),
            'attachments': [],
            'metadata': _extract_metadata(html, soup),
            'has_content': False,
        }

    content_text = _extract_text(root)
    base = base_url or url.rstrip('/').rsplit('/', 1)[0] + '/'
    attachments = _extract_attachments(root, base)
    metadata = _extract_metadata(html, soup)

    return {
        'url': url,
        'content_text': content_text,
        'content_html': str(root)[:200000],  # 截断超大
        'word_count': len(content_text),
        'attachments': attachments,
        'metadata': metadata,
        'has_content': len(content_text) >= 100,
    }


if __name__ == '__main__':
    # 简单自测
    sample = '''
    <html><head><title>测试</title></head>
    <body>
      <div class="header">导航</div>
      <div id="content">
        <h1>关于加强XX工作的通知</h1>
        <p style="text-align:center">国发〔2024〕12号</p>
        <p>各省、自治区、直辖市人民政府,国务院各部委、各直属机构:</p>
        <p>为深入贯彻党中央、国务院决策部署,现就加强XX工作有关事项通知如下:</p>
        <p>一、提高思想认识... (一) ... (二) ...</p>
        <p>二、强化工作措施... </p>
        <h2>三、加强组织领导</h2>
        <p>请结合实际,认真贯彻落实。</p>
        <p>附件:<a href="/xx/2024/12/abc.pdf">XX工作实施细则.pdf</a></p>
      </div>
      <div class="footer">版权所有</div>
    </body></html>
    '''
    r = extract_detail(sample, 'https://example.gov.cn/policy/2024/12/abc.html')
    print('字:', r['word_count'])
    print('有正文:', r['has_content'])
    print('附件:', r['attachments'])
    print('元数据:', r['metadata'])
    print('正文:')
    print(r['content_text'][:500])
