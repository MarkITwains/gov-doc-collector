# gov-doc-collector

> 中国政府官网文档采集 + 政策结构化的 Hermes Agent Skill 套件。
> 覆盖 **30 个国家部委 + 31 个省级政府**,三级反爬降级(`curl_cffi` → `Playwright` → `requests`),
> 可作为 **MCP Server** 接入 Claude / Hermes / 其他 Agent。

一条完整链路:

```
列表采集 → 候选预筛 → 详情正文 → 正文质量门禁 → 结构化(条件/资金/截止) → 企业画像匹配 → 报告
          (P0-2)                        (P1-1)                    (policy-analyzer skill)
```

## 目录

- [✨ 特性](#-特性)
- [🚀 快速开始](#-快速开始)
- [📊 数据与评测](#-数据与评测)
- [📖 使用](#-使用)
- [🧩 配套 Skill: policy-analyzer](#-配套-skill-policy-analyzer)
- [🏗️ 架构](#️-架构)
- [⚙️ 站点配置](#️-站点配置)
- [🔍 添加新站点](#-添加新站点)
- [📈 当前状态](#-当前状态)
- [📋 已知限制](#-已知限制)
- [🩺 排错](#-排错)
- [📝 依赖](#-依赖)

## ✨ 特性

**采集**

- ✅ **30 个国家部委 + 31 个省级政府**统一配置
- ✅ **三级采集策略自动降级**:`curl_cffi`(TLS 指纹)→ `Playwright`(JS 渲染/iframe)→ `requests`
- ✅ **多栏目采集**:一个站点可同时配"通知公告 + 申报专区 + 政策法规";分页支持 `template` / `query`,单栏目空页即停
- ✅ **XML / JSON / HTML** 多格式解析,列表自动去重、日期归一化为 ISO

**质量(决定命中率的两道闸)**

- ✅ **候选预筛(先筛后抓)**:只用列表页信息(栏目类型 → URL 特征 → 标题信号)判断"值不值得抓详情",
  真实语料里 90%+ 条目是新闻/法规,**不该为它们付详情抓取的代价**
- ✅ **栏目先验**:配置可声明 `channel_type`(apply/notice/law/news),比正文关键词可靠;
  `find_channels.py` 可自动探测站点的申报/通知公告栏目
- ✅ **正文质量门禁**:识别错采的导航页/目录页(模板词、段落重复率、句读密度),输出
  `content_quality{score,level,flags}` + `content_usable`

**可度量**

- ✅ **评测闭环**:`eval_policy_analyzer.py` 输出漏斗、字段覆盖率、候选预筛校准(精确率/召回率)、
  标注集 P/R/F1,并与 `eval/baseline.json` 基线对比;`--fail-on-drop` 可直接进 CI
- ✅ **80 条离线回归用例**:`python scripts/test_regressions.py`(含 MCP 协议握手,不需要网络)

## 🚀 快速开始

```bash
git clone https://github.com/MarkITwains/gov-doc-collector.git
cd gov-doc-collector

pip install -r requirements.txt
playwright install chromium     # 可选:~430MB,只有 need_js 站点需要

# 1) 离线自检(80 用例,不联网)
python scripts/test_regressions.py

# 2) 看真实语料基线(漏斗 / 覆盖率 / 质量分布)
python scripts/eval_policy_analyzer.py

# 3) 采一个站点试试(只对申报候选抓详情)
python -c "
from scripts.unified_fetcher import UnifiedFetcher
f = UnifiedFetcher()
for it in f.fetch_list_with_details('ndrc', 'national', limit=5, only_apply=True):
    if it.get('detail'):
        print(it['title'][:40], it['detail']['content_quality']['level'])
    else:
        print('跳过:', it['title'][:40], '|', it.get('skip_reason'))
f.close()"
```

## 📊 数据与评测

评测不是"有更好",是**没有它就只能拍脑袋调规则**。下面是 `eval/baseline.json`
(由 `eval_policy_analyzer.py` 在真实采集语料上生成,n=67)的实测数字:

> 语料快照 `scripts/detail_full_results.json` 是**本地文件**(`.gitignore` 忽略,2.4MB),
> 不随仓库分发;新克隆想复现请先跑 `python scripts/test_all_sites.py` 生成,
> 或直接用标注集模式 `--golden`。

| 漏斗(triage) | 占比 | | 字段覆盖(全样本) | 覆盖率 |
|---|---|---|---|---|
| `news` 新闻/会议/招聘 | 35.8% | | `support_measures` | 35.8% |
| `other` 其它 | 46.3% | | `doc_number` | 26.9% |
| `regulate` 规范类 | 16.4% | | `validity`(截止日等) | 13.4% |
| **`apply` 申报类** | **1.5%** | | `conditions` / `support_targets` | 6.0% |
| | | | `funding` | 4.5% |

正文质量:`high 30 / medium 33 / low 4`(4 条是错采的栏目页/目录页);
候选预筛:通过 2 条,其中真是申报类 1 条(样本太小,仅作链路校准)。

> **结论:瓶颈在"采什么",不在"怎么解析"**。申报类只占 1.5%,意味着分母里 98.5% 的内容
> 根本不该进入解析与匹配 —— 所以 v1.8.0 把漏斗做在便宜的一侧:
> **栏目对准 → 标题级预筛 → 才抓详情**(抓详情是链路里最贵的一步)。
> 语料里 85% 的标题没有任何申报信号,靠标题就能挡掉绝大部分。

**怎么用评测**

```bash
python scripts/eval_policy_analyzer.py                       # 语料模式:与基线对比
python scripts/eval_policy_analyzer.py --update-baseline     # 刷新基线(改动前后各跑一次)
python scripts/eval_policy_analyzer.py --fail-on-drop 0.05   # CI:覆盖率掉 >5 个百分点即失败
python scripts/eval_policy_analyzer.py --show-low-quality    # 列出疑似错采页面
python scripts/eval_policy_analyzer.py --golden eval/golden.example.jsonl   # 标注集 P/R/F1
```

**关于标注集**:`eval/golden.example.jsonl` 目前只有 8 条 **seed-synthetic** 种子
(用于固化已知行为,100% 只说明"已知模式能过")。要真正提升抽取精度,需要补
50–100 篇**真实人工标注**,格式:

```json
{"id": "real-001", "source": "real-labeled", "title": "...", "text": "政策正文",
 "use_llm_triage": false,
 "expect": {"triage_category": "apply",
            "conditions": [["revenue", ">=", 1000], ["company_age", ">=", 2]],
            "funding": [["fixed", 50], ["cap", 500]],
            "deadline": "2026-07-31"},
 "profile": {"region": "深圳市南山区", "revenue": 3200},
 "expect_verdict": "likely"}
```

条件下标按 `(field, op, 归一化 value)` 集合比较(金额→万元、月→年),`--golden` 会输出
逐字段 P/R/F1 与**逐条 diff**(缺了哪条、多了哪条),这就是后续调优的目标函数。

## 📖 使用

### Python API

```python
from scripts.unified_fetcher import UnifiedFetcher

fetcher = UnifiedFetcher()

# 1) 列表(多栏目 + 分页,自动启用 cffi / JS 渲染)
items = fetcher.fetch_list('ndrc', 'national')
for item in items[:5]:
    print(f"[{item['date']}] {item['title']}")

# 2) 详情页正文(发文字号/发文日期/附件/正文/正文质量)
detail = fetcher.fetch_detail(
    'http://www.moj.gov.cn/pub/sfbgw/qmyfzg/202103/t20210331_349128.html',
    base_url='http://www.moj.gov.cn'
)
print(detail['word_count'], detail['metadata'].get('doc_number'), detail['attachments'])
print(detail['content_quality'])     # {'score': 92, 'level': 'high', 'flags': []}
print(detail['content_usable'])      # False 时建议不要喂给解析器

# 3) 端到端:列表 + 只给申报候选抓详情(推荐)
items = fetcher.fetch_list_with_details('mof', 'national', limit=5, only_apply=True)
for it in items:
    if it.get('skip_reason'):
        print('跳过:', it['title'][:30], '|', it['skip_reason'])
    else:
        print('已抓:', it['title'][:30], it['candidate']['reasons'])

# 4) 增量监控(cron 友好,只报新政策;已见链接持久化在 .cache/)
new_items = fetcher.fetch_list_new('ndrc', 'national')

fetcher.close()
```

### 命令行

```bash
# 全站诊断(可用率 / 失败原因)
python scripts/diagnose_sites.py

# 栏目探测:找出站点的"通知公告/申报专区"栏目(回填 search_path)
python scripts/find_channels.py beijing --level provincial
python scripts/find_channels.py --level national --all --limit 5 --json

# 评测(见上一节)
python scripts/eval_policy_analyzer.py

# 离线回归测试(80 用例,含 MCP 协议握手)
python scripts/test_regressions.py
```

### MCP Server

基于官方 [mcp SDK](https://pypi.org/project/mcp/)(FastMCP,stdio 传输),
实现标准 MCP JSON-RPC 2.0 协议(initialize 握手 / tools/list / tools/call)。

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

| 工具 | 说明 |
|---|---|
| `fetch_gov_docs` | 列表采集(`max_pages` 翻页;`only_apply=true` 只返回申报候选,**不抓详情**) |
| `fetch_new_gov_docs` | 增量采集,只返回上次之后的新政策(政策监控/cron) |
| `list_available_sites` | 列出已配置站点 |
| `fetch_gov_doc_detail` | 抓指定详情页(发文字号/发文日期/附件/正文/质量) |
| `fetch_gov_docs_with_details` | 列表 + 详情端到端(**默认 `only_apply=true`**,只为申报候选抓详情) |

## 🧩 配套 Skill: policy-analyzer

`skills/policy-analyzer/` 把采集到的正文变成**可核对的申报条件**与**企业适配结论**:

```python
# 在 skills/policy-analyzer/ 目录下执行(或把该目录的 scripts/ 加入 sys.path)
from scripts.policy_parser import parse_from_detail
from scripts.company_matcher import match_with_triage, format_report

parsed = parse_from_detail(detail, title=item['title'])   # 采集器的 detail 直接喂进来
print(parsed['triage_category'])        # apply / regulate / news / other
print(parsed['conditions'][:2])         # 结构化条件(字段/运算符/值/是否硬性)
print(parsed['validity']['deadline'])   # 申报截止日

match = match_with_triage(parsed, profile)      # 企业画像 vs 政策
print(match['verdict'], match['score'])         # eligible/likely/uncertain/ineligible/not_applicable
print(format_report(match))                     # 可直接推送的 Markdown 报告
```

- **确定性优先**:正则负责高精度抽取;语义不明的条件标 `needs_llm_review` 交给上层
- **可选 LLM 补漏**:`use_llm_extract=True` / `POLICY_LLM_EXTRACT=1`,仅在正则抽到的结构化条件
  少于 3 条时触发,每条必须通过**原文 quote 回验**才采纳,并落盘缓存
- **正文质量联动**:`parse_from_detail` 会透传 `content_quality` / `content_usable`,
  低质量正文(导航页/目录页)可据此跳过
- 自测:`cd skills/policy-analyzer && python scripts/company_matcher.py`
- 详见 [skills/policy-analyzer/SKILL.md](skills/policy-analyzer/SKILL.md)

## 🏗️ 架构

```
hermes_agent/
├── skill.md                      # gov-doc-collector 的 Skill 定义(Hermes Agent 用)
├── README.md / CHANGELOG.md      # 本文件 / 版本日志
├── DIAGNOSIS_REPORT.md           # 站点失败根因分析
├── requirements.txt
├── configs/sites/
│   ├── national.json             # 30 个国家部委
│   └── provincial.json           # 31 个省级政府
├── scripts/
│   ├── unified_fetcher.py        # 统一采集器(主入口:三级降级/并发详情)
│   ├── fetcher.py                # 基础采集器(多栏目 / 分页 / 编码检测)
│   ├── parser.py                 # HTML/XML/JSON 列表解析
│   ├── detail_extractor.py       # 详情正文提取 + 正文质量评估
│   ├── policy_candidate.py       # 列表级候选筛选(抓详情前先筛)
│   ├── find_channels.py          # 栏目探测(找申报栏目)
│   ├── eval_policy_analyzer.py   # 评测/回归(漏斗、覆盖率、P/R/F1)
│   ├── mcp_server.py             # MCP Server(官方 SDK)
│   ├── diagnose_sites.py         # 全站诊断
│   ├── seen_store.py             # 增量采集缓存
│   └── test_*.py                 # 各类测试
├── skills/policy-analyzer/       # 配套 Skill:政策结构化 + 企业适配匹配
├── eval/
│   ├── baseline.json             # 语料指标基线(回归对比)
│   └── golden.example.jsonl      # 标注集种子(需补真实标注)
└── docs/                         # MINISTRIES / PROVINCES / QUICKSTART / ...
```

**三级采集策略**

```
请求 → curl_cffi (TLS 指纹)  ──→ 200 + items? → ✓
                                    ↓ 失败
       Playwright (JS 渲染)   ──→ items?     → ✓
                                    ↓ 失败
       plain requests         ──→ items?     → ✓
                                    ↓
                                  0 items
```

每种策略按 `use_cffi` / `need_js` 标记选择性启用,标记见 `configs/sites/*.json`。

**命中率漏斗(v1.8.0 起)**

```
站点栏目          列表条目              候选           详情            结构化 + 匹配
search_path[]  →  标题/URL/栏目先验  →  only_apply  →  content_usable  →  policy-analyzer
(多栏目)          (便宜,不联网抓详情)   (只抓申报候选)   (低质量正文丢弃)    (条件/资金/截止)
```

## ⚙️ 站点配置

```json
{
  "site_key": {
    "name": "网站名称",
    "base_url": "https://example.gov.cn",
    "search_path": ["/tzgg/", "/zcsb/", "/zhengce/zhengcefagui/"],
    "channel_type": "notice",   // 可选: 整站栏目性质 apply/notice/law/news(比标题可靠)
    "selectors": {
      "list": "ul li",          // 列表项选择器
      "title": "a",             // 标题元素
      "link": "a@href",         // 链接属性
      "date": "span"            // 日期元素
    },
    "use_cffi": true,           // 可选: 启用 curl_cffi
    "need_js": true,            // 可选: 启用 Playwright
    "verify_ssl": false,        // 可选: 强制关闭 TLS 校验(默认自动降级)
    "min_title_len": 6,         // 可选: 标题最短长度过滤(默认 6)
    "pagination": {             // 可选: 分页
      "type": "template",                        // template 或 query
      "url_template": "/tzgg/index_{page}.html", // template 模式(只对该栏目生效)
      "param": "page",                           // query 模式的参数名
      "start": 1,                                // 第 2 页起代入的页码
      "max_pages": 3                             // 最多翻页数
    }
  }
}
```

> `search_path` 可以是字符串(单栏目,兼容旧配置)或**数组**(多栏目)。
> 申报公告常分散在"通知公告 / 申报专区 / 政策法规",只配一个栏目是采不到申报通知的主因
> —— 实测 67 篇详情里申报类只占 1.5%。`channel_type: "apply"` 表示整栏目都是申报专区
> (该栏目所有条目都当候选),适合"申报专栏"这类页面。

## 🔍 添加新站点

1. `python scripts/find_channels.py <site_key> --level national` 找出候选栏目
2. 把申报/通知公告栏目填进 `search_path` **数组**(不要用首页)
3. `python scripts/diagnose_sites.py` 验证站点可用
4. `python scripts/eval_policy_analyzer.py --show-low-quality` 检查有没有错采页面

## 📈 当前状态

**版本**:v1.8.0(2026-09-13,详见 [CHANGELOG.md](CHANGELOG.md))

| 指标 | 数值 |
|---|---|
| 配置站点 | 61(30 国家部委 + 31 省级) |
| 完整可用 (UnifiedFetcher) | **50/61 (82%)** |
| 基础可用 (plain requests) | 44/61 (72%) |
| 列表记录 | 3380 条真实政策 |
| 详情提取 | 30+ 容器选择器,自动元数据 + 附件 + 正文质量 |
| 离线回归用例 | 80(含 MCP 协议握手) |

### 国家部委 (22/30 可用)

✅ 发改委(54)、财政部(25)、司法部(48)、央行(35)、能源局(81)、生态环境部(34)、农业农村部(24)、商务部(11)、交通运输部(495)、退役军人事务部(55)、审计署等

⚠️ 需 JS 渲染:工信部、人社部、应急部、住房城乡建设部、市场监管总局、民政部、文化和旅游部、国家宗教事务局、国家体育总局

❌ 反爬虫较严:公安部(521)、卫健委(412)、金融监管总局(RST)、水利部(超时)

### 省级政府 (24/31 可用)

✅ 北京(998)、福建(168)、广东(120)、宁夏(86)、安徽(72)、贵州(63)、河南(60)、江苏(54)、新疆(53)、云南(50)、内蒙古(49)、西藏(45)、黑龙江(38)、陕西(38)、山西(36)、吉林(35)、重庆(30)、四川(29)、上海(20)、山东(19)、辽宁(10)、河北(13)、浙江(41)、天津(31)、海南(128)

❌ 反爬虫/路径失效:江西、广西、青海、甘肃、湖北、湖南

完整列表见 [docs/MINISTRIES.md](docs/MINISTRIES.md)、[docs/PROVINCES.md](docs/PROVINCES.md)。

## 📋 已知限制

- **阿里云 WAF 412** (3 站):卫健委、湖北、甘肃 — curl_cffi 已被拦,需更高级 JA3 随机化
- **TCP RST / 超时** (4 站):金融监管总局、青海、广西、水利部 — 网络级封禁,需要代理
- **Cloudflare JS Challenge** (1 站):公安部 — 需要完整 JS Challenge 解决
- **选择器不稳定** (3 站):审计署、人社部、湖南 — Playwright 偶发时序问题
- **栏目未对准申报公告(最大瓶颈)**:多数站点配置指向"政策法规/政务公开",
  实测申报类只占 1.5%;用 `find_channels.py` 把申报栏目补进 `search_path` 是当前最高 ROI 的动作
- **正文可能采到导航页/目录页**:已加 `content_quality` 门禁 + `--show-low-quality` 清单,
  但根因仍在栏目与容器选择器;这类正文会让解析结果为空,建议直接丢弃重采
- **结构化精度**:`conditions`/`funding` 在"申报类"样本内表现良好,但真实语料里申报类样本量太小,
  精度数字需要先补标注集才有统计意义(见 [数据与评测](#-数据与评测))

失败根因分析见 [DIAGNOSIS_REPORT.md](DIAGNOSIS_REPORT.md)。

## 🩺 排错

| 现象 | 原因 / 处理 |
|---|---|
| 列表采到 0 条 | 栏目路径失效或改版 → 用 `find_channels.py` 重新定位;确认该站是否需要 `use_cffi` / `need_js` |
| 采到的正文是导航/目录页 | 看 `detail['content_quality']['level']`;`eval_policy_analyzer.py --show-low-quality` 列清单 |
| 详情大多不是申报通知 | 栏目没对准。把申报栏目填进 `search_path`,或用 `only_apply=True` 只抓候选 |
| `ModuleNotFoundError: mcp.server.fastmcp` | 装了 mcp 2.x(改名 `MCPServer`)→ `pip install "mcp>=1.2.0,<2"` |
| 某站点偶发失败 | 三级降级是自动的,看日志的 `debug`;WAF/网络级封禁需代理(见已知限制) |
| triage / 抽取结果不符合预期 | **先补标注集**,跑 `--golden` 看 P/R/F1 与逐条 diff,再决定改规则还是开 LLM 补漏 |
| 想省 LLM 调用 | 默认只用规则(`POLICY_LLM_EXTRACT=0`);开启时也有数量触发门槛 + 结果缓存 |
| 中文乱码 | 采集侧自动探测编码(header/meta/utf-8/gb18030);CLI 解析也支持 utf-8 → gb18030 兜底 |

## 📝 依赖

```
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
playwright>=1.40.0
curl_cffi>=0.6.0
mcp>=1.2.0,<2          # v2 把 FastMCP 改名为 MCPServer,mcp_server.py 走 v1 接口
```

`policy-analyzer` 的解析/匹配只需 Python 3.9+ 标准库;仅 LLM 相关能力(分流 / 抽取补漏)额外需要 `requests`。

## 📄 License

MIT
