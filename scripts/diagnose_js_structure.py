#!/usr/bin/env python3
"""诊断JS渲染后的HTML结构"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from js_fetcher import JSFetcher
from bs4 import BeautifulSoup

TEST_SITES = [
    ('miit', 'national', '工业和信息化部'),
    ('mohrss', 'national', '人力资源社会保障部'),
    ('hunan', 'provincial', '湖南省'),
]

def diagnose_one(site_key, level, name):
    print(f"\n{'='*70}")
    print(f"诊断: {name} ({site_key})")
    print('='*70)

    fetcher = JSFetcher(use_js=True)
    try:
        config = fetcher.load_config(site_key, level)
        url = config['base_url'] + config['search_path']

        html = fetcher.fetch_with_js(url, timeout=40000, wait_for='load')
        print(f"\nHTML长度: {len(html)} 字节")

        # 解析HTML找列表项
        soup = BeautifulSoup(html, 'html.parser')

        # 尝试原选择器
        orig_sel = config['selectors']['list']
        print(f"\n原选择器: {orig_sel}")
        orig_items = soup.select(orig_sel)
        print(f"匹配到: {len(orig_items)} 项")

        # 如果原选择器失败，尝试常见列表选择器
        if len(orig_items) == 0:
            print("\n尝试常见选择器:")
            common_sels = [
                'ul li', 'ul.list li', 'ul.news-list li', 'div.list-item',
                'ul li a', 'div.item', 'li.item', 'tr',
                'ul[class*=list] li', 'div[class*=list]', '.list_01 li'
            ]
            for sel in common_sels:
                items = soup.select(sel)
                if len(items) > 5:
                    print(f"  {sel:30s} -> {len(items):4d} 项")
                    # 显示前3项的HTML
                    for i, item in enumerate(items[:3], 1):
                        text = item.get_text(strip=True)[:80]
                        print(f"    [{i}] {text}")
        else:
            # 显示匹配到的前3项
            for i, item in enumerate(orig_items[:3], 1):
                print(f"\n  项 {i}:")
                print(f"    HTML: {str(item)[:200]}")
                title_sel = config['selectors']['title']
                title_elem = item.select_one(title_sel) if '@' not in title_sel else item
                if title_elem:
                    print(f"    标题: {title_elem.get_text(strip=True)[:80]}")

        # 保存HTML样本
        sample_file = f"debug_{site_key}_js.html"
        with open(sample_file, 'w', encoding='utf-8') as f:
            f.write(html[:50000])
        print(f"\n✓ HTML样本已保存到: {sample_file}")

    except Exception as e:
        print(f"\n✗ 错误: {e}")
    finally:
        fetcher.close()

if __name__ == '__main__':
    for site_key, level, name in TEST_SITES:
        diagnose_one(site_key, level, name)
