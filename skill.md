---
name: gov-doc-collector
description: 采集国家部委和省级政府网站的政策文档和公告
version: 1.7.0
author: Hermes Team
metadata:
  hermes:
    tags: [政府, 政策, 采集, 爬虫, gov, crawler]
    category: collection
    requires_toolsets: [terminal]
    fallback_for_tools: []
    config:
      - key: gov_doc_collector.timeout
        description: 单站点请求超时秒数
        default: "30"
        prompt: 采集单站点的 HTTP 超时,默认 30 秒
---

# 政府文档采集器 (gov-doc-collector)

## 概述

gov-doc-collector 是一个专门用于采集中国政府官网和政务公开平台文档的Skill。支持30个国家部委和31个省级政府网站，提供统一的API接口，整合JS渲染和反爬虫绕过技术。

## 功能特性

- ✅ 支持30个国家部委网站
- ✅ 支持31个省级政府（4直辖市+27省/自治区）
- ✅ **JS渲染支持** (Playwright)
- ✅ **反爬虫绕过** (curl_cffi浏览器指纹)
- ✅ **三级采集策略自动降级** (curl_cffi → Playwright → plain requests)
- ✅ XML/JSON/HTML 多格式解析
- ✅ **详情页正文提取** (发文字号/发文日期/附件/正文,30+ 容器选择器)
- ✅ 自动去重和数据清洗
- ✅ 提供MCP Server接口

## 已支持站点

### 国家级 (22/30 可用)

成功采集~1600条政策记录，**新增**: 工信部、人社部

详见 [docs/MINISTRIES.md](../docs/MINISTRIES.md)

### 省级 (24/31 可用)

成功采集~1946条政策记录，**新增**: 江西省、重庆市

**直辖市** (3/4): 北京(998)、上海(20)、重庆(30)

**省份** (16/27): 福建(168)、广东(120)、宁夏(86)、安徽(71)、贵州(63)、河南(60)、江苏(54)、云南(50)、内蒙古(49)、西藏(45)、黑龙江(40)、陕西(39)、四川(29)、山东(19)、江西(15)、辽宁(10)

详见 [docs/PROVINCES.md](../docs/PROVINCES.md)

### 总计

- **配置**: 61个站点
- **可用**: 50/61 站点 **(82%)** (UnifiedFetcher 完整能力)
- **基础可用**: 44/61 (72%) (仅 plain requests)
- **记录**: 3380 条政策
- **版本**: v1.7.0 (2026-08-03)

## 使用方法

### 1. 安装依赖

```bash
pip install -r requirements.txt
pip install playwright curl_cffi
playwright install chromium
```

### 2. Python API

```python
from scripts.unified_fetcher import UnifiedFetcher

# 初始化(自动启用JS渲染和curl_cffi)
fetcher = UnifiedFetcher()

# 采集国家发改委
items = fetcher.fetch_list('ndrc', 'national')

# 采集工信部(JS渲染)
items = fetcher.fetch_list('miit', 'national')

# 显示结果
for item in items[:5]:
    print(f"[{item['date']}] {item['title']}")

# 关闭资源
fetcher.close()
```

**获取详情页正文**:
```python
from scripts.unified_fetcher import UnifiedFetcher
fetcher = UnifiedFetcher()

# 单独抓一条政策详情
detail = fetcher.fetch_detail(
    'http://www.moj.gov.cn/pub/sfbgw/qmyfzg/202103/t20210331_349128.html',
    base_url='http://www.moj.gov.cn'
)
print(f"字数: {detail['word_count']}")
print(f"发文字号: {detail['metadata'].get('doc_number')}")
print(f"发文日期: {detail['metadata'].get('issue_date')}")
print(f"附件: {detail['attachments']}")
print(f"正文: {detail['content_text'][:200]}...")

# 一步到位:列表+前N条详情
items = fetcher.fetch_list_with_details('mof', 'national', limit=3)
for item in items[:3]:
    print(item['title'])
    print(item.get('detail', {}).get('metadata', {}))
```

### 3. 命令行

```bash
# 离线回归测试(12 用例,含 MCP 协议握手)
python scripts/test_regressions.py

# 最终测试
python scripts/test_final.py

# 站点诊断
python scripts/diagnose_sites.py
```

### 3. MCP Server

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

#### 可用工具

**fetch_gov_docs** - 采集政府文档
```json
{
  "site_key": "ndrc",
  "level": "national",
  "limit": 10
}
```

**list_available_sites** - 列出可用站点
```json
{
  "level": "national"
}
```

**fetch_gov_doc_detail** - 抓取详情页正文(发文字号/发文日期/附件/正文)
```json
{
  "url": "http://www.moj.gov.cn/pub/sfbgw/.../t20210331_349128.html",
  "base_url": "http://www.moj.gov.cn",
  "use_cffi": false,
  "need_js": false
}
```

**fetch_gov_docs_with_details** - 列表+前N条详情(端到端)
```json
{
  "site_key": "mof",
  "level": "national",
  "limit": 3
}
```

**fetch_new_gov_docs** - 增量采集,只返回新政策(政策监控,cron 友好)
```json
{
  "site_key": "ndrc",
  "level": "national",
  "limit": 20
}
```

## 数据格式

列表记录:
```json
{
  "title": "政策标题",
  "link": "https://example.gov.cn/...",
  "date": "2026-06-10"
}
```

详情记录 (`detail` 字段):
```json
{
  "url": "详情页 URL",
  "content_text": "清洗后的纯文本正文",
  "content_html": "正文 HTML(已截断到 200K)",
  "word_count": 3576,
  "attachments": [{"name": "...", "url": "...", "type": "pdf"}],
  "metadata": {
    "doc_number": "财建〔2026〕137号",
    "issue_date": "2026-06-10",
    "issuer": "财政部",
    "category": "...",
    "index_number": "..."
  },
  "has_content": true
}
```

## 站点标识符

| 站点 | 标识符 | 站点 | 标识符 |
|------|--------|------|--------|
| 中国政府网 | `gov_cn` | 发改委 | `ndrc` |
| 教育部 | `moe` | 科技部 | `most` |
| 财政部 | `mof` | 生态环境部 | `mee` |
| 交通部 | `mot` | 农业部 | `moa` |
| 商务部 | `mofcom` | 文旅部 | `mct` |
| 应急部 | `mem` | 央行 | `pbc` |
| 审计署 | `cnao` | 市场监管 | `samr` |
| 统计局 | `nsa` | 能源局 | `nea` |
| 证监会 | `csrc` | ... | ... |

完整列表: [docs/MINISTRIES.md](../docs/MINISTRIES.md)

## 配置文件

站点配置存储在 `configs/sites/`:
- `national.json` - 30个国家部委
- `provincial.json` - 省级政府

### 添加新站点

```json
{
  "site_key": {
    "name": "网站名称",
    "base_url": "https://example.gov.cn",
    "search_path": "/zhengce/",
    "selectors": {
      "list": "ul.list li",
      "title": "a.title",
      "link": "a@href",
      "date": ".date"
    }
  }
}
```

## 测试结果

- **基础成功率**: 25/30 国家部委 (83%), 19/31 省级 (61%) - plain requests
- **完整成功率**: 26/30 国家部委 (87%), 24/31 省级 (77%) - UnifiedFetcher (curl_cffi + Playwright)
- **总计**: 50/61 站点 (82%), 3380 条政策
- **测试日期**: 2026-07-31

## 已知限制

- **WAF 阿里云 412** (3个): nhc、hubei、gansu — curl_cffi 已试但需更高级 JA3 随机化
- **TCP RST/超时** (4个): cbirc、qinghai、guangxi、mwr — 网络级封禁,需要代理
- **Cloudflare JS Challenge** (1个): mps — 需要完整 JS Challenge 解决
- **选择器不匹配** (3个): cnao、mohrss、hunan — Playwright 偶发不稳定
- **详情正文提取**:依赖 30+ 容器选择器,部分站点正文容器不规范时可能为空;
  v1.5.0 起已支持详情页正文(发文字号/发文日期/附件/正文)

## 技术架构

### 统一采集器 (UnifiedFetcher)

三级策略自动降级：

1. **curl_cffi 浏览器指纹** - 解决 TLS 指纹检测的 WAF（江西、广西等）
2. **Playwright JS 渲染** - 解决动态加载和 iframe 嵌套（工信部、人社部、湖南等）
3. **标准HTTP请求** - 普通静态页面(兜底方案)

### 配置标记

```json
{
  "need_js": true,      // 启用JS渲染
  "use_cffi": true,     // 启用浏览器指纹
  "selectors": {...}    // CSS选择器配置
}
```

## 扩展

- 添加市县级政府: 创建 `configs/sites/municipal.json`
- 自定义解析器: 修改 `scripts/parser.py`
- 添加详情页提取: 扩展 `unified_fetcher.fetch_detail()`

## 依赖

```
requests>=2.31.0
beautifulsoup4>=4.12.0
playwright>=1.40.0
curl_cffi>=0.6.0
```

安装: 
```bash
pip install -r requirements.txt
playwright install chromium  # ~430MB
```

## 示例场景

### 政策监控
定期采集多个部委,监控新政策发布:
```python
new_items = fetcher.fetch_list_new('ndrc', 'national')  # 只报新政策(已见链接持久化在 .cache/)
```
或经 MCP 调用 `fetch_new_gov_docs`。

### 政策汇总
跨部委采集,生成政策动态报告

### 关键词搜索
在采集结果中过滤特定关键词

### 数据分析
导出JSON，进行政策趋势分析

## 文档

- [README.md](../README.md) - 项目说明
- [DIAGNOSIS_REPORT.md](../DIAGNOSIS_REPORT.md) - 站点根因分析
- [docs/MINISTRIES.md](../docs/MINISTRIES.md) - 部委列表
- [scripts/unified_fetcher.py](../scripts/unified_fetcher.py) - 主采集器

## 更新日志

### v1.7.0 (2026-08-03)
- ✨ **分页采集**:站点配置 `pagination`(template/query 两种),`max_pages` 可调,
  跨页 link 去重、空页即停
- ✨ **增量监控**:`SeenStore` 已见链接持久化 + `fetch_list_new()` /
  MCP `fetch_new_gov_docs`,cron 只报新政策
- ✨ **详情并发抓取**:线程池(默认 4),need_js 站点自动串行
- 🔧 **编码自动检测**:不再硬编码 utf-8(header/meta/探测/gb18030 兜底)
- 🔧 **TLS 安全优先**:默认校验证书,证书损坏站点自动降级并告警
- 🔧 **JSON API 路径支持数组下标**(`result[0].list`),缺失不崩溃
- 🔧 **日期归一化 ISO + 标题阈值放宽到 6 可配置**
- 🧹 删除遗留 enhanced/hybrid/js_fetcher 及一次性脚本;采集器加 logging
- ✅ 回归测试扩充到 36 用例

### v1.6.0 (2026-08-03)
- 🔧 **MCP Server 改用官方 mcp SDK(FastMCP)**:旧版不说 MCP 协议(无 initialize
  握手/无 JSON-RPC 封装),实际无法被 MCP 客户端接入;现标准协议,工具签名不变
- 🔧 **MCP 工具统一走 UnifiedFetcher**:修复 `fetch_gov_docs` 原先无视
  need_js/use_cffi 配置、JS 站点经 MCP 必失败的问题
- 🔧 **导入路径修复**:新增 `scripts/__init__.py` + 相对导入回退,
  `from scripts.unified_fetcher import UnifiedFetcher` 在仓库根目录可用
- 🐛 **parse_xml_feed 链接丢失修复**:三元表达式优先级 bug,RSS 条目 link 恒为 None
- 🐛 **详情页正文重复修复**:嵌套 div 不再重复抽取,word_count 不再虚高
- 🐛 **独立附件区修复**:div.attachment-list 里的附件不再被噪声清洗吞掉
- ✅ 新增 `scripts/test_regressions.py`(12 个离线回归测试,含 MCP 协议握手)

### v1.5.0 (2026-07-31)
- ✅ 详情页正文提取落地:`fetch_detail` / `fetch_gov_doc_detail` 支持
  发文字号/发文日期/附件/正文(30+ 容器选择器)
- ✅ `fetch_gov_docs_with_details` 端到端:列表 + 前 N 条详情一步到位
- ✅ frontmatter schema 统一为 `metadata.hermes`(tags/category/requires_toolsets/config)
- ✅ 文档:删除"不支持详情页"矛盾限制、省级可用数对齐(24/31)、版本号三处统一

### v1.4.0 (2026-06-11)
- ✅ 大规模修复 search_path 错误 (23 个部委的首页→真实政策栏目)
- ✅ 新增 use_cffi 标记到 WAF/JS Challenge 失败站点 (nhc/hubei/gansu/mps/cbirc/qinghai/xinjiang/hunan/guangxi)
- ✅ 修复 hunan 错误标记 (从 need_js 改为 use_cffi+need_js 组合)
- ✅ 修复 6 个 HTTP_404 站点路径 (河北/山西/吉林/浙江/天津/重庆)
- ✅ curl_cffi impersonate 升级到 chrome120
- ✅ Playwright wait_until 升级到 networkidle + 3秒等待
- ✅ UnifiedFetcher 三级策略重排: curl_cffi → Playwright → plain
- ✅ 修复诊断脚本超时 (20s→30s) 和 unified_fetcher timeout (25s→30s)
- ✅ 写 DIAGNOSIS_REPORT.md 根因分析
- ✅ 可用率提升: 72% → **82%** (UnifiedFetcher)

### v1.3.0 (2026-06-10)
- ✅ 新增Playwright JS渲染支持
- ✅ 新增curl_cffi浏览器指纹绕过
- ✅ 修复工信部、人社部、江西、重庆
- ✅ 可用率提升: 65% → **72%**
- ✅ 创建UnifiedFetcher统一采集器
- ✅ 新增诊断工具diagnose_sites.py
- ✅ 总记录数达到3546条

### v1.2.0 (2026-06-10)
- ✅ 新增27个国家部委配置
- ✅ 实现MCP Server接口
- ✅ 完善测试脚本
- ✅ 数据质量达到100%

### v1.0.0
- ✅ 基础功能实现
- ✅ 支持3个国家级站点
- ✅ 支持4个省级站点
