#!/usr/bin/env python3
"""MCP Server for Government Document Collector"""
import sys
import json
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from fetcher import GovDocFetcher

class GovDocMCPServer:
    def __init__(self):
        self.fetcher = GovDocFetcher()

    def list_tools(self):
        return {
            "tools": [
                {
                    "name": "fetch_gov_docs",
                    "description": "采集政府部委网站的文档和公告",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "site_key": {
                                "type": "string",
                                "description": "站点标识符，如 gov_cn, ndrc, moe 等"
                            },
                            "level": {
                                "type": "string",
                                "enum": ["national", "provincial"],
                                "default": "national",
                                "description": "政府级别"
                            },
                            "limit": {
                                "type": "integer",
                                "default": 10,
                                "description": "返回记录数量限制"
                            }
                        },
                        "required": ["site_key"]
                    }
                },
                {
                    "name": "list_available_sites",
                    "description": "列出所有可用的政府网站",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "level": {
                                "type": "string",
                                "enum": ["national", "provincial"],
                                "default": "national"
                            }
                        }
                    }
                },
                {
                    "name": "fetch_gov_doc_detail",
                    "description": "采集指定政策详情页正文(发文字号/发文日期/附件/正文)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "详情页 URL"
                            },
                            "base_url": {
                                "type": "string",
                                "description": "站点 base_url(可选,用于附件绝对化)"
                            },
                            "use_cffi": {
                                "type": "boolean",
                                "default": False,
                                "description": "是否启用 curl_cffi 绕过 WAF"
                            },
                            "need_js": {
                                "type": "boolean",
                                "default": False,
                                "description": "是否需要 Playwright JS 渲染"
                            }
                        },
                        "required": ["url"]
                    }
                },
                {
                    "name": "fetch_gov_docs_with_details",
                    "description": "采集指定站点列表+前 N 条详情正文",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "site_key": {
                                "type": "string",
                                "description": "站点标识符"
                            },
                            "level": {
                                "type": "string",
                                "enum": ["national", "provincial"],
                                "default": "national"
                            },
                            "limit": {
                                "type": "integer",
                                "default": 5,
                                "description": "详情抓取条数(详情抓取慢,建议 ≤ 10)"
                            }
                        },
                        "required": ["site_key"]
                    }
                }
            ]
        }

    def call_tool(self, name, arguments):
        if name == "fetch_gov_docs":
            return self._fetch_gov_docs(**arguments)
        elif name == "list_available_sites":
            return self._list_available_sites(**arguments)
        elif name == "fetch_gov_doc_detail":
            return self._fetch_gov_doc_detail(**arguments)
        elif name == "fetch_gov_docs_with_details":
            return self._fetch_gov_docs_with_details(**arguments)
        else:
            return {"error": f"Unknown tool: {name}"}

    def _fetch_gov_docs(self, site_key, level="national", limit=10):
        try:
            items = self.fetcher.fetch_list(site_key, level)
            limited_items = items[:limit] if items else []

            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "site_key": site_key,
                            "level": level,
                            "total_count": len(items),
                            "items": limited_items
                        }, ensure_ascii=False, indent=2)
                    }
                ]
            }
        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e)}, ensure_ascii=False)
                    }
                ],
                "isError": True
            }

    def _list_available_sites(self, level="national"):
        try:
            config_file = self.fetcher.config_dir / f"{level}.json"
            with open(config_file, 'r', encoding='utf-8') as f:
                configs = json.load(f)

            sites = [
                {
                    "key": key,
                    "name": config["name"],
                    "url": config["base_url"]
                }
                for key, config in configs.items()
            ]

            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "level": level,
                            "count": len(sites),
                            "sites": sites
                        }, ensure_ascii=False, indent=2)
                    }
                ]
            }
        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e)}, ensure_ascii=False)
                    }
                ],
                "isError": True
            }

    def _fetch_gov_doc_detail(self, url, base_url='', use_cffi=False, need_js=False):
        try:
            # 优先用 UnifiedFetcher(支持 cffi + js)
            from unified_fetcher import UnifiedFetcher
            if not hasattr(self, '_unified') or self._unified is None:
                self._unified = UnifiedFetcher()
            detail = self._unified.fetch_detail(url, base_url, use_cffi=use_cffi, need_js=need_js)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(detail, ensure_ascii=False, indent=2)
                    }
                ]
            }
        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e)}, ensure_ascii=False)
                    }
                ],
                "isError": True
            }

    def _fetch_gov_docs_with_details(self, site_key, level="national", limit=5):
        try:
            from unified_fetcher import UnifiedFetcher
            if not hasattr(self, '_unified') or self._unified is None:
                self._unified = UnifiedFetcher()
            items = self._unified.fetch_list_with_details(site_key, level, limit=limit)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "site_key": site_key,
                            "level": level,
                            "fetched_details": limit,
                            "items": items
                        }, ensure_ascii=False, indent=2)
                    }
                ]
            }
        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e)}, ensure_ascii=False)
                    }
                ],
                "isError": True
            }

def main():
    server = GovDocMCPServer()

    for line in sys.stdin:
        try:
            request = json.loads(line)

            if request.get("method") == "tools/list":
                response = server.list_tools()
            elif request.get("method") == "tools/call":
                params = request.get("params", {})
                response = server.call_tool(
                    params.get("name"),
                    params.get("arguments", {})
                )
            else:
                response = {"error": "Unknown method"}

            print(json.dumps(response, ensure_ascii=False))
            sys.stdout.flush()

        except Exception as e:
            error_response = {"error": str(e)}
            print(json.dumps(error_response))
            sys.stdout.flush()

if __name__ == "__main__":
    main()
