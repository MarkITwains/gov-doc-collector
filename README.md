# gov-doc-collector

中国政府官网文档采集 Skill — 支持 30 个国家部委 + 31 个省级政府,集成 Playwright JS 渲染、curl_cffi 浏览器指纹反爬虫绕过、XML/JSON/HTML 多格式解析。

> Hermes Agent 生态下的采集器 skill,可作为 MCP Server 接入。

## ✨ 特性

- ✅ **30 个国家部委 + 31 个省级政府**统一配置
- ✅ **三级采集策略自动降级**:`curl_cffi` → `Playwright` → `requests`
- ✅ **Playwright JS 渲染**:支持动态加载和 iframe 嵌套
- ✅ **curl_cffi 浏览器指纹**:绕过 TLS 指纹检测的 WAF
- ✅ **XML / JSON / HTML** 多格式解析
- ✅ **MCP Server 接口**:可接入 Claude / Hermes / 其他 Agent

## 📊 当前状态 (v1.4.0)

| 指标 | 数值 |
|---|---|
| 配置站点 | 61 |
| 完整可用 (UnifiedFetcher) | **50/61 (82%)** |
| 基础可用 (plain requests) | 44/61 (72%) |
| 总记录数 | 3380 条真实政策 |

### 国家部委 (22/30 可用)

✅ 发改委(54)、财政部(25)、司法部(48)、央行(35)、能源局(81)、生态环境部(34)、农业农村部(24)、商务部(11)、交通运输部(495)、退役军人事务部(55)、审计署等

⚠️ 需 JS 渲染:工信部、人社部、应急部、住房城乡建设部、市场监管总局、民政部、文化和旅游部、国家宗教事务局、国家体育总局

❌ 反爬虫较严:公安部(521)、卫健委(412)、金融监管总局(RST)、水利部(超时)

### 省级政府 (24/31 可用)

✅ 北京(998)、福建(168)、广东(120)、宁夏(86)、安徽(72)、贵州(63)、河南(60)、江苏(54)、新疆(53)、云南(50)、内蒙古(49)、西藏(45)、黑龙江(38)、陕西(38)、山西(36)、吉林(35)、重庆(30)、四川(29)、上海(20)、山东(19)、辽宁(10)、河北(13)、浙江(41)、天津(31)、海南(128)

❌ 反爬虫/路径失效:江西、广西、青海、甘肃、湖北、湖南

## 🚀 安装

```bash
# 基础依赖
pip install -r requirements.txt

# 反爬虫依赖
pip install curl_cffi
playwright install chromium  # ~430MB
```

## 📖 使用

### Python API

```python
from scripts.unified_fetcher import UnifiedFetcher

fetcher = UnifiedFetcher()

# 采集发改委政策
items = fetcher.fetch_list('ndrc', 'national')
for item in items[:5]:
    print(f"[{item['date']}] {item['title']}")

fetcher.close()
```

### 命令行

```bash
# 全站诊断
python scripts/diagnose_sites.py

# 最终测试
python scripts/test_final.py

# JS 渲染测试
python scripts/test_js_rendering.py
```

### MCP Server

配置 `.claude/mcp_server_config.json`:

```json
{
  "mcpServers": {
    "gov-doc-collector": {
      "command": "python",
      "args": ["-u", "scripts/mcp_server.py"]
    }
  }
}
```

可用工具:
- `fetch_gov_docs` — 采集指定站点文档
- `list_available_sites` — 列出可用站点

## 🏗️ 架构

```
hermes_agent/
├── skill.md                  # Skill 定义 (Hermes Agent 用)
├── DIAGNOSIS_REPORT.md       # 根因分析报告
├── README.md                 # 本文件
├── requirements.txt          # 依赖
├── configs/
│   └── sites/
│       ├── national.json     # 30 个国家部委
│       └── provincial.json   # 31 个省级政府
├── scripts/
│   ├── unified_fetcher.py    # 统一采集器(主入口)
│   ├── fetcher.py            # 基础采集器
│   ├── parser.py             # HTML/XML/JSON 解析
│   ├── mcp_server.py         # MCP Server
│   ├── diagnose_sites.py     # 全站诊断
│   └── test_*.py             # 各类测试
└── docs/
    ├── MINISTRIES.md         # 部委列表
    ├── PROVINCES.md          # 省份列表
    └── ...
```

### 三级采集策略

```
请求 → curl_cffi (TLS 指纹)  ──→ 200 + items? → ✓
                                    ↓ 失败
       Playwright (JS 渲染)   ──→ items?     → ✓
                                    ↓ 失败
       plain requests         ──→ items?     → ✓
                                    ↓
                                  0 items
```

每种策略按 `use_cffi` / `need_js` 标记选择性启用,标记配置见 `configs/sites/*.json`。

## ⚙️ 站点配置

每个站点的配置示例:

```json
{
  "site_key": {
    "name": "网站名称",
    "base_url": "https://example.gov.cn",
    "search_path": "/path/to/policy/list",
    "selectors": {
      "list": "ul li",          // 列表项选择器
      "title": "a",             // 标题元素
      "link": "a@href",         // 链接属性
      "date": "span"            // 日期元素
    },
    "use_cffi": true,           // 可选: 启用 curl_cffi
    "need_js": true             // 可选: 启用 Playwright
  }
}
```

## 🔍 添加新站点

1. 打开 `configs/sites/national.json` 或 `provincial.json`
2. 按上述格式添加配置
3. 用浏览器手动找到**真实政策列表页** URL(不要用首页)
4. 跑 `python scripts/diagnose_sites.py` 验证

## 📋 已知限制

- **阿里云 WAF 412** (3 站):卫健委、湖北、甘肃 — curl_cffi 已被拦,需更高级 JA3 随机化
- **TCP RST / 超时** (4 站):金融监管总局、青海、广西、水利部 — 网络级封禁,需要代理
- **Cloudflare JS Challenge** (1 站):公安部 — 需要完整 JS Challenge 解决
- **选择器不稳定** (3 站):审计署、人社部、湖南 — Playwright 偶发时序问题
- **只采列表页**:不提取详情页正文

详见 [DIAGNOSIS_REPORT.md](DIAGNOSIS_REPORT.md) 根因分析。

## 📝 依赖

```
requests>=2.31.0
beautifulsoup4>=4.12.0
playwright>=1.40.0
curl_cffi>=0.6.0
```

## 📄 License

MIT
