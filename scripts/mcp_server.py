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
import json
import sys
from pathlib import Path
from typing import Optional

# 支持直接脚本执行:把 scripts/ 目录加入 sys.path(平铺导入回退可用)
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

# 兼容包内导入(scripts.mcp_server)与直接脚本执行
try:
    from .unified_fetcher import UnifiedFetcher
except ImportError:
    from unified_fetcher import UnifiedFetcher

mcp = FastMCP(
    'gov-doc-collector',
    instructions=(
        '国家部委与省级政府网站政策文档采集:列表采集、详情页正文'
        '(发文字号/发文日期/附件/正文)、列表+详情端到端。'
        '三级采集策略自动降级:curl_cffi → Playwright JS 渲染 → 普通请求。'
    ),
)

_fetcher = None


def get_fetcher() -> UnifiedFetcher:
    """懒加载单例 UnifiedFetcher(支持 curl_cffi / Playwright / requests 三级策略)"""
    global _fetcher
    if _fetcher is None:
        _fetcher = UnifiedFetcher()
    return _fetcher


@mcp.tool()
def fetch_gov_docs(site_key: str, level: str = 'national', limit: int = 10,
                   max_pages: Optional[int] = None) -> dict:
    """采集政府部委网站的文档列表(按站点配置自动启用 curl_cffi / JS 渲染)。

    Args:
        site_key: 站点标识符,如 gov_cn, ndrc, moe, miit;可用 list_available_sites 查询
        level: 政府级别,national(国家部委) / provincial(省级政府)
        limit: 返回记录数量上限
        max_pages: 翻页数(仅对配置了 pagination 的站点生效;缺省用站点配置值)
    """
    items = get_fetcher().fetch_list(site_key, level, max_pages=max_pages) or []
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
    return get_fetcher().fetch_detail(url, base_url,
                                      use_cffi=use_cffi, need_js=need_js)


@mcp.tool()
def fetch_gov_docs_with_details(site_key: str, level: str = 'national',
                                limit: int = 5) -> dict:
    """采集指定站点列表 + 前 N 条详情正文(端到端)。

    Args:
        site_key: 站点标识符
        level: 政府级别,national / provincial
        limit: 详情抓取条数(详情页抓取较慢,建议 ≤ 10)
    """
    items = get_fetcher().fetch_list_with_details(site_key, level, limit=limit)
    return {
        'site_key': site_key,
        'level': level,
        'fetched_details': min(limit, len(items)),
        'items': items,
    }


if __name__ == '__main__':
    # stdio 传输:MCP 客户端(Claude Code / Claude Desktop 等)按标准配置拉起即可
    mcp.run()
