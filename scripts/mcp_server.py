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
                }
            ]
        }

    def call_tool(self, name, arguments):
        if name == "fetch_gov_docs":
            return self._fetch_gov_docs(**arguments)
        elif name == "list_available_sites":
            return self._list_available_sites(**arguments)
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
