#!/usr/bin/env python3
"""测试curl_cffi浏览器指纹模拟对反爬虫站点的效果"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import warnings
warnings.filterwarnings('ignore')

from curl_cffi import requests as cffi_requests

# 失败的反爬虫/连接类站点
TEST_SITES = [
    ('mps',      'https://www.mps.gov.cn/n2254536/n4904355/index.html', 'HTTP_521 公安部'),
    ('nhc',      'http://www.nhc.gov.cn/wjw/index.html', 'HTTP_412 卫健委'),
    ('gansu',    'https://www.gansu.gov.cn/', 'HTTP_412 甘肃'),
    ('hubei',    'http://www.hubei.gov.cn/zfwj/ezbf/', 'HTTP_412 湖北'),
    ('cbirc',    'https://www.cbirc.gov.cn/cn/view/pages/index/index.html', 'CONN_RESET 金融监管'),
    ('qinghai',  'https://www.qh.gov.cn/', 'CONN_RESET 青海'),
    ('xinjiang', 'https://www.xinjiang.gov.cn/', 'TIMEOUT 新疆'),
    ('mwr',      'https://www.mwr.gov.cn/zwgk/gknr/202406/', 'CONN_ERROR 水利部'),
    ('guangxi',  'https://www.gxzf.gov.cn/', 'CONN_ERROR 广西'),
    ('jiangxi',  'https://www.jiangxi.gov.cn/', 'CONN_ERROR 江西'),
    ('miit',     'https://www.miit.gov.cn/zwgk/zcwj/wjfb/index.html', 'NEED_JS 工信部'),
    ('mohrss',   'https://www.mohrss.gov.cn/xxgk2020/fdzdgknr/zcfg/gfxwj/', 'NEED_JS 人社部'),
    ('hunan',    'http://www.hunan.gov.cn/hnszf/xxgk/wjk/szfwj/', 'NEED_JS 湖南'),
]

def test_one(key, url, note):
    try:
        resp = cffi_requests.get(
            url, impersonate="chrome", timeout=25, verify=False,
            headers={'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'}
        )
        body = resp.text
        return f"{key:10s} [{note:22s}] -> HTTP {resp.status_code}, body={len(body)}B"
    except Exception as e:
        return f"{key:10s} [{note:22s}] -> FAIL: {str(e)[:70]}"

if __name__ == '__main__':
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for line in ex.map(lambda t: test_one(*t), TEST_SITES):
            print(line)
