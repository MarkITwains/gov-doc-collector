---
name: policy-analyzer
description: 解析政府政策正文为结构化数据,并与给定企业画像匹配,输出 eligible/likely/uncertain/ineligible/not_applicable 五档适配结论与可读 Markdown 报告。
version: 1.1.0
author: Hermes Team
metadata:
  hermes:
    tags: [policy, gov, analyzer, nlp, eligibility, triage]
    category: analysis
    requires_toolsets: [terminal]
    fallback_for_tools: []
    config:
      - key: policy_analyzer.profile
        description: 默认企业画像 JSON 路径(可选,未配置时调用方传入)
        default: ""
        prompt: 企业画像 JSON 文件绝对路径,留空则调用时通过 --profile 传入
      - key: policy_analyzer.max_chars
        description: 解析时正文截断长度(避免超大 HTML)
        default: "200000"
        prompt: 单条政策正文超过该字符数会被截断,默认 20 万
---

# 政策内容识别整理 (policy-analyzer)

把"非结构化政策正文"变成"可机读结构化条件",再与企业画像逐条核对,
输出可投送到飞书/企微/邮件的 Markdown 报告。

设计原则:**确定性优先**。能用正则/规则解析的绝不丢给 LLM;语义模糊的
(行业契合、属地"本市/本省")标记 `needs_llm_review`,由上层 Agent 复核。

可作为 `policy-monitor` 的下游:采集→详情正文→结构化→匹配→推送。

## When to Use

触发场景:
- 拿到政策详情页正文(`gov-doc-collector.fetch_detail()` 的 `content_text`),
  需要提取"支持对象 / 支持方式 / 资金额度 / 申报条件 / 申报材料 / 有效期"。
- 有企业画像(profile),想快速筛出"本公司能不能申报"。
- 给 cron 任务产出日报、给 LLM 准备结构化 prompt。

不适用:
- 政策分类/主题打标 → 用 `gov-doc-collector` 的 `category` 字段。
- 法条实体抽取 / 责任主体识别 → 这是 NLP 任务,本 skill 不做。
- 多文档对比/版本追踪 → 用 `policy-monitor` 的 diff 能力。

## Procedure

### 1. 解析政策正文 + 分流(triage)

```python
from scripts.policy_parser import parse_policy, parse_from_detail

parsed = parse_from_detail(detail, title=item['title'])

# parsed 包含 doc_type(文种) / triage_category(分流类别)
print(parsed['triage_category'])
#   - 'apply'           申报/资助/奖励类 → 进入 match_policy
#   - 'regulate'        规范类(法/条例/管理办法)→ 不属申报,自动 not_applicable
#   - 'news'            新闻/会议/招聘/吹风会 → not_applicable
#   - 'other'           其它
```

### 2. 准备企业画像

`company_matcher.PROFILE_SCHEMA` 定义了支持的画像字段,所有字段可选,
字段越全,结论越确定。最小可用画像:

```python
profile = {
    'name': '深圳市某某智能科技有限公司',
    'region': '深圳市南山区',          # 用于核对属地
    'industry': '工业软件研发',         # 行业契合(语义)
    'company_age': 4.5,                # 年;或传 'established': '2021-09-01'
    'revenue': 3200,                   # 万元(基准单位,会自动换算)
    'headcount': 120,
    'rd_ratio': 8.5,                   # %
    'patents': 1,                      # 发明专利件数
    'qualifications': ['国家高新技术企业', '小微企业'],
    'credit_clean': True,
}
```

### 3. 匹配 + 生成报告(带分流)

```python
from scripts.company_matcher import match_with_triage, match_policy, format_report

# 推荐: match_with_triage — 自动跳过非申报类
match = match_with_triage(parsed, profile)
print(format_report(match))
print(match['verdict'], match['score'])

# 或: match_policy — 强制对所有 doc 做匹配(忽略 triage)
match = match_policy(parsed, profile)
```

`verdict` 取值:
- `eligible` ✅ — 全部硬性条件确定通过,无 unknown/review。
- `likely` 🟡 — 多数已核通过,部分字段画像缺数据或需语义复核;
             或是**全 review**(纯语义条件,交给 LLM 复盘)。
- `uncertain` ❓ — 没解析出条件,或 known/known 数太少。
- `ineligible` ❌ — 任一硬性条件不满足(返回后建议仍可关注申报截止前条件放宽)。
- `not_applicable` ⏭ — `triage_category` 判定为 新闻/规范/其它(非申报类),
                       跳过匹配。

## 解析输出

`parse_policy()` 返回 dict(中文示例):

```json
{
  "title": "市工信局关于组织申报2026年度专精特新中小企业培育资助的通知",
  "doc_type": "通知",
  "doc_number": "深工信〔2026〕XX号",
  "issuer": "深圳市工业和信息化局",
  "issue_date": "2026-06-01",
  "validity": {
    "effective_from": "发布之日",
    "valid_until": "2028-12-31",
    "valid_years": 3,
    "deadline": "2026-07-31"
  },
  "support_targets": ["在本市行政区域内注册登记、具有独立法人资格的中小企业。"],
  "support_measures": ["资金补贴", "评定授牌", "贷款贴息"],
  "funding": [
    {"text": "给予一次性奖励50万元", "kind": "fixed", "value_wan": 50},
    {"text": "按其上年度研发投入的30%给予补助,最高不超过500万元",
     "kind": "cap", "value_wan": 500},
    {"text": "按比例30%", "kind": "ratio", "ratio_pct": 30}
  ],
  "conditions": [
    {"text": "在本市注册成立满2年", "field": "company_age",
     "op": ">=", "value": 2, "unit": "年", "hard": true,
     "needs_llm_review": false},
    {"text": "上年度营业收入不低于1000万元", "field": "revenue",
     "op": ">=", "value": 1000, "unit": "万元", "hard": true,
     "needs_llm_review": false},
    {"text": "拥有有效发明专利2件以上", "field": "patents",
     "op": ">=", "value": 2, "unit": "件", "hard": true,
     "needs_llm_review": false},
    {"text": "高新技术企业", "field": "qualification",
     "op": "has", "value": ["高新技术企业"], "hard": true,
     "needs_llm_review": false}
  ],
  "application": {
    "materials": ["申报书", "营业执照复印件", "上年度审计报告及纳税证明"],
    "process_text": null
  },
  "outline": [
    {"level": 1, "marker": "一", "title": "支持对象"},
    {"level": 1, "marker": "二", "title": "申报条件"},
    {"level": 1, "marker": "三", "title": "支持标准"}
  ],
  "triage_category": "apply",
  "stats": {"chars": 543, "paragraphs": 20, "conditions": 8}
}
```

### 字段说明

| 字段 | 提取方式 | 备注 |
|---|---|---|
| `doc_type` | 标题关键词 + 书名号规则 | 18 种文种:办法/细则/通知/法/条例/规划…;"法"避免误匹配"管理办法" |
| `doc_number` | 正则:发文字号或"部令" | 兜底仅取正文前 3KB |
| `validity.deadline` | "申报截止...日" / "请于...日前报送" | 申报类政策的关键字段 |
| `validity.valid_until` | "有效期至...日" / "有效期至...为止" | 兜底"为止"格式 |
| `support_measures` | 9 类政策手段关键词 | 资金补贴/税收优惠/融资支持/贷款贴息/贷款额度… |
| `funding[]` | 数字 + 上下文"给予/奖励/补贴" + 贷款额度类 | 过滤营收/注册资本等阈值类金额 |
| `conditions[]` | "申报条件/支持对象/支持内容" 块 + 句级兜底 | 拆分到字段(age/revenue/qualification…) |
| `application.materials` | "申报材料"块枚举 | 限 15 条 |
| `triage_category` | 标题关键词 + 招聘/会议信号 | apply/regulate/news/other |

## 匹配状态语义

| status | 含义 | 触发条件 |
|---|---|---|
| `pass` ✓ | 画像字段满足硬性条件 | 数值比较通过 / 资质已具备 |
| `fail` ✗ | 画像字段不满足硬性条件 | 数值比较失败 / 资质缺失 |
| `soft_fail` △ | 不满足软性条件(优先/鼓励) | 原文含 "优先/鼓励/原则上" |
| `unknown` ? | 画像缺少该字段 | 补全字段后可消除 |
| `review` ⊙ | 语义条件(行业/属地),交给 Agent 复核 | 行业关键词/未解析出具体值 |

**"或"关系救援**:同条款内若含"或"且任一子条件 `pass`,整组降级到 `pass`。
匹配时,`unknown` / `review` 计入 `likely` 而非直接判 `eligible`,避免
字段不全时被错判通过。

**资质别名**:政策中"中小企业"对应画像的"小微企业/中小微企业/中型企业"等;
"高新技术企业"对应"国家高新技术企业/国高新/高企"。

## CLI 用法

```bash
# 跑样例(无参数): 自带申报通知 + profile → 输出 verdict=ineligible/likely
python scripts/company_matcher.py

# 解析单文件(读 stdin,输出 JSON)
python scripts/policy_parser.py < policy.txt > parsed.json
```

## Pitfalls

- **大正文**:HTML 详情页 > 20 万字会被 `policy_analyzer.max_chars` 截断,
  影响 `validity`、`outline` 提取。建议在调用前清理 `<script>`/`<style>`/导航。
- **重复段落**:部分政府网详情页正文被复制多次,parser 端用 `seen` 去重;
  fetch 端也应考虑对 `content_text` 做去重。
- **"或"歧义**:"发明专利 2 件以上,或获得高新技术企业认定"——只要分公司
  拿高新认了,这条整体 pass。`match_policy` 已自动处理。
- **行业契合**:"符合我市重点支持产业方向"——这是语义条件,无法纯靠正则判定,
  应走 `review` 让 LLM 介入。
- **多政策混杂**:一段文字里同时塞了"申报 + 资金拨付",`outline` 会出现两个
  章节,正常。`conditions` 不会混淆,因为有 STOP_HEADER 边界。
- **币种/单位**:条件里"亿元"自动换算成"万元";但条件文本里出现"投资额"
  会被识别为阈值,过滤掉,不计入可获支持。
- **doc_type=法/条例**:这些是规范类政策,**默认不作为申报分流对象**
  (`triage_category=regulate` → `not_applicable`)。除非 conditions 文本里
  含"申报/资助/补助"等关键词才会被识别为 apply。

## Verification

```bash
# 单元测试 1: parser 端 → 8 条 conditions, 含 4 个结构化字段
python scripts/policy_parser.py

# 单元测试 2: matcher 端 → verdict=ineligible/likely, 报告含"待复核"区
python scripts/company_matcher.py

# 端到端: 真实采集数据
python scripts/test_policy_analyzer_real.py
python scripts/diag_v3_full.py       # 覆盖率
python scripts/diag_v3_e2e.py        # E2E 分流分布
```

## 真实数据精度(v1.1, n=54)

| 字段 | 覆盖率 |
|---|---|
| doc_type (识别出文种) | 37% (其中 20 条是'规范/申报'真实文种, 34 条是'新闻/会议'被正确判为'其他') |
| doc_number | 30% |
| outline (≥3 节) | 31% |
| support_measures | 44% |
| funding | 6% |
| conditions | 9% (5/54, 真实数据中'申报类'占 ~10%) |
| support_targets | 7% |
| validity | 17% |
| **triage 准确分类** | **apply 12 / regulate 6 / news 20 / other 16** |

> 真实政府网采集到的内容,大部分是新闻/会议/招聘/法条,**不属申报类**。
> `triage_category` 把这 70% 提前过滤为 `not_applicable`,聚焦剩下的
> 申报/规范类 18 条,避免对'新闻'做无意义的 match。

## 输入依赖

- `policy_parser` 仅依赖 Python 标准库 + `re`。
- `company_matcher` 仅依赖 Python 标准库。
- 不依赖 `gov-doc-collector`,但配合使用效果最好(同目录 `scripts/` 提供
  `UnifiedFetcher`,输出 `detail` 直接喂给 `parse_from_detail`)。

## 依赖安装

无外部依赖,Python 3.9+ 即可。

## 更新日志

### v1.1.0 (2026-06-11)
- ✅ **triage_category 字段**: 4 档分流 apply/regulate/news/other,自动过滤非申报类
- ✅ `match_with_triage()`: 自动跳过 news/regulate/other,直接给 not_applicable
- ✅ `valid_until` 兜底"有效期至…为止"格式
- ✅ COND_HEADER 拓宽: 支持对象/支持内容/支持范围/支持方式/支持标准
- ✅ 描述段紧跟时不打断(拼接而非 break)
- ✅ 章节标题/发问句/招聘/分配方式 等噪声过滤
- ✅ funding 识别: 贷款额度/单户额度/贴息额度 类
- ✅ support_measures: 贷款贴息/贷款额度 类别
- ✅ 资质别名: 中小企业↔小微企业↔中小微企业↔中型企业↔微型企业↔小型企业
- ✅ doc_type: 书名号规则 + 关键词优先,单字"法"与"办法/条例"区分
- ✅ `detect_measures()` 抽取方法注释完善

### v1.0.0 (2026-06-11)
- ✅ `policy_parser.parse_policy()` 提取 doc_type/doc_number/validity/
  support_targets/support_measures/funding/conditions/application/outline
- ✅ `company_matcher.match_policy()` 四档结论 + 逐条核对 + Markdown 报告
- ✅ 资质/数值/资质别名/单位换算/或关系救援
