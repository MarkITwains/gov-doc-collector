#!/usr/bin/env python3
"""MCP Server for Government Document Collector

用官方 mcp SDK(FastMCP,stdio 传输)实现,遵循 MCP JSON-RPC 2.0 协议
(initialize 握手 / tools/list / tools/call / 通知处理全部由 SDK 接管)。

启动方式(与 .claude/mcp_server_config.json 一致):
    python -u scripts/mcp_server.py

历史注脚:v1.5 之前的版本是自研逐行 JSON 读写器,不说 MCP 协议
(无 initialize 握手、响应无 JSON-RPC 封装),实际无法被 MCP 客户端接入;
v1.6.0 起改为官方 SDK 实现,并统一走 UnifiedFetcher(三级采集策略)。
"""
import ipaddress
import json
import socket
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# 支持直接脚本执行:把 scripts/ 目录加入 sys.path(平铺导入回退可用)
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

# 兼容包内导入(scripts.mcp_server)与直接脚本执行
try:
    from .unified_fetcher import UnifiedFetcher
    from .policy_candidate import split_candidates
except ImportError:
    from unified_fetcher import UnifiedFetcher
    from policy_candidate import split_candidates

mcp = FastMCP(
    'gov-doc-collector',
    instructions=(
        '国家部委与省级政府网站政策文档采集:列表采集、详情页正文'
        '(发文字号/发文日期/附件/正文)、列表+详情端到端。'
        '三级采集策略自动降级:curl_cffi → Playwright JS 渲染 → 普通请求。'
    ),
)

_fetcher = None

# MCP 工具允许的政府级别(对应 configs/sites/<level>.json)
VALID_LEVELS = ('national', 'provincial')


def get_fetcher() -> UnifiedFetcher:
    """懒加载单例 UnifiedFetcher(支持 curl_cffi / Playwright / requests 三级策略)"""
    global _fetcher
    if _fetcher is None:
        _fetcher = UnifiedFetcher()
    return _fetcher


def _configured_hosts() -> set:
    """收集所有已配置站点 base_url 的主机名(national + provincial)。

    配置读不出来时返回空集合(宁可少放行,也不因配置问题放过内网)。
    """
    hosts = set()
    try:
        config_dir = get_fetcher().config_dir
    except Exception:  # noqa: BLE001
        return hosts
    for level in VALID_LEVELS:
        try:
            f = config_dir / f'{level}.json'
            configs = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            continue
        if not isinstance(configs, dict):
            continue
        for c in configs.values():
            host = urlparse((c or {}).get('base_url', '')).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def _host_allowed(host: str) -> bool:
    """主机名是否属于配置站点,或 .gov.cn(含 gov.cn 本身)。"""
    if not host:
        return False
    host = host.lower().rstrip('.')
    if host == 'gov.cn' or host.endswith('.gov.cn'):
        return True
    return host in _configured_hosts()


def _is_bad_ip(addr: str) -> bool:
    """地址是否为 loopback/私有/链路本地/保留/多播等不可对外的地址。"""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True  # 解析不了的当作不安全
    return (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _url_is_safe(url: str) -> bool:
    """SSRF 校验:只允许 http/https + 白名单主机,且所有解析地址都对外安全。

    - scheme 必须是 http/https
    - 主机不能是 IP 字面量(只允许走配置/.gov.cn 域名)
    - 主机必须在白名单(配置站点 host 或 .gov.cn)
    - DNS 解析出的每个地址都不能是内网/环回/保留地址
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https'):
        return False
    host = parsed.hostname
    if not host:
        return False
    # IP 字面量一律拒绝(含 169.254.x / 127.x / 10.x 等)
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if not _host_allowed(host):
        return False
    # 回验 DNS:任一解析地址落在内网/环回/保留 → 拒绝(防 DNS rebinding)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        if _is_bad_ip(addr):
            return False
    return True


def _fetch_error(url: str, msg: str) -> dict:
    """与 UnifiedFetcher.fetch_detail 失败时同形的错误负载。"""
    return {
        'url': url,
        'content_text': '',
        'content_html': '',
        'word_count': 0,
        'attachments': [],
        'metadata': {},
        'has_content': False,
        'content_quality': {'score': 0, 'level': 'low', 'flags': ['url_rejected']},
        'content_usable': False,
        'error': msg,
    }


def _check_level(level: str) -> Optional[dict]:
    """level 必须是 national/provincial,否则返回 error 负载(否则 None)。"""
    if level not in VALID_LEVELS:
        return {'level': level,
                'error': f'unknown level: {level!r} (expected one of {VALID_LEVELS})'}
    return None


def _check_site(site_key: str, level: str) -> Optional[dict]:
    """level 合法且站点已配置,否则返回 error 负载(否则 None)。"""
    err = _check_level(level)
    if err is not None:
        return err
    try:
        cfg = get_fetcher().load_config(site_key, level)
    except (OSError, json.JSONDecodeError):
        return {'site_key': site_key, 'level': level, 'error': '配置目录读取失败'}
    if not cfg:
        return {'site_key': site_key, 'level': level,
                'error': f'unknown site: {site_key!r} (level={level})'}
    return None


@mcp.tool()
def fetch_gov_docs(site_key: str, level: str = 'national', limit: int = 10,
                   max_pages: Optional[int] = None,
                   only_apply: bool = False) -> dict:
    """采集政府部委网站的文档列表(按站点配置自动启用 curl_cffi / JS 渲染)。

    Args:
        site_key: 站点标识符,如 gov_cn, ndrc, moe, miit;可用 list_available_sites 查询
        level: 政府级别,national(国家部委) / provincial(省级政府)
        limit: 返回记录数量上限
        max_pages: 翻页数(仅对配置了 pagination 的站点生效;缺省用站点配置值)
        only_apply: 只返回"申报/资助候选"(按标题 + 栏目 + URL 先验打分,不抓详情),
                    用来核对站点栏目是否对准了申报公告
    """
    err = _check_site(site_key, level)
    if err is not None:
        return err
    items = get_fetcher().fetch_list(site_key, level, max_pages=max_pages) or []
    if only_apply:
        candidates, skipped = split_candidates(items)
        return {
            'site_key': site_key,
            'level': level,
            'total_count': len(items),
            'candidate_count': len(candidates),
            'skipped_count': len(skipped),
            'items': candidates[:limit],
        }
    return {
        'site_key': site_key,
        'level': level,
        'total_count': len(items),
        'items': items[:limit],
    }


@mcp.tool()
def fetch_new_gov_docs(site_key: str, level: str = 'national', limit: int = 20,
                       max_pages: Optional[int] = None) -> dict:
    """增量采集:只返回上次采集后新发布的政策(政策监控场景)。

    首次运行等同于全量采集;已见链接记录在 .cache/seen_links.json,
    跨进程持久化,适合 cron 定时调用。

    Args:
        site_key: 站点标识符
        level: 政府级别,national / provincial
        limit: 返回记录数量上限
        max_pages: 翻页数(缺省用站点配置值)
    """
    err = _check_site(site_key, level)
    if err is not None:
        return err
    items = get_fetcher().fetch_list_new(site_key, level, max_pages=max_pages) or []
    return {
        'site_key': site_key,
        'level': level,
        'new_count': len(items),
        'items': items[:limit],
    }


@mcp.tool()
def list_available_sites(level: str = 'national') -> dict:
    """列出所有已配置的政府站点。

    Args:
        level: national(30 个国家部委) / provincial(31 个省级政府)
    """
    if level not in VALID_LEVELS:
        return {'level': level, 'count': 0, 'sites': [],
                'error': f'unknown level: {level!r} (expected one of {VALID_LEVELS})'}
    config_file = get_fetcher().config_dir / f'{level}.json'
    configs = json.loads(config_file.read_text(encoding='utf-8'))
    sites = [{'key': k, 'name': c['name'], 'url': c['base_url']}
             for k, c in configs.items()]
    return {'level': level, 'count': len(sites), 'sites': sites}


@mcp.tool()
def fetch_gov_doc_detail(url: str, base_url: str = '',
                         use_cffi: bool = False, need_js: bool = False) -> dict:
    """采集指定政策详情页正文(发文字号/发文日期/附件/正文)。

    Args:
        url: 详情页 URL
        base_url: 站点 base_url(可选,用于附件链接绝对化)
        use_cffi: 启用 curl_cffi 绕过 WAF(反爬虫较严的站点设 True)
        need_js: 启用 Playwright JS 渲染(动态加载页面设 True)
    """
    # SSRF 防护:只允许 http/https + 白名单主机,拒绝 IP 字面量/内网地址,
    # 在任何网络请求之前拦截(base_url 参数同样校验)。
    if not _url_is_safe(url):
        return _fetch_error(url, 'url not allowed')
    if base_url and not _url_is_safe(base_url):
        return _fetch_error(url, 'base_url not allowed')
    return get_fetcher().fetch_detail(url, base_url,
                                      use_cffi=use_cffi, need_js=need_js)


@mcp.tool()
def fetch_gov_docs_with_details(site_key: str, level: str = 'national',
                                limit: int = 5,
                                only_apply: bool = True) -> dict:
    """采集指定站点列表 + 前 N 条详情正文(端到端)。

    Args:
        site_key: 站点标识符
        level: 政府级别,national / provincial
        limit: 详情抓取条数(详情页抓取较慢,建议 ≤ 10)
        only_apply: 默认 True —— 先用候选筛选,**只为申报/资助候选**抓详情
                    (真实语料里 90%+ 是新闻/法规,默认全抓是纯浪费);
                    被筛掉的条目仍返回,带 `skip_reason` 便于核对
    """
    err = _check_site(site_key, level)
    if err is not None:
        return err
    items = get_fetcher().fetch_list_with_details(
        site_key, level, limit=limit, only_apply=only_apply)
    fetched = sum(1 for it in items if it.get('detail'))
    return {
        'site_key': site_key,
        'level': level,
        'total_count': len(items),
        'fetched_details': fetched,
        'skipped_count': sum(1 for it in items if it.get('skip_reason')),
        'items': items,
    }


if __name__ == '__main__':
    # stdio 传输:MCP 客户端(Claude Code / Claude Desktop 等)按标准配置拉起即可
    mcp.run()
