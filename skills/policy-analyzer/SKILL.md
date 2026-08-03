---
name: policy-analyzer
description: 解析政府政策正文为结构化数据,并与给定企业画像匹配,输出 eligible/likely/uncertain/ineligible/not_applicable 五档适配结论与可读 Markdown 报告。
version: 1.4.0
author: Hermes Team
metadata:
  hermes:
    tags: [policy, gov, analyzer, nlp, eligibility, triage, llm]
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
      - key: policy_analyzer.llm_triage
        description: triage 分类是否调用 LLM(默认开启,不可用时自动回退规则)
        default: "true"
        prompt: 设 false 则强制走关键词规则;设 true 优先用模型判断
---

# 政策内容识别整理 (policy-analyzer)

把"非结构化政策正文"变成"可机读结构化条件",再与企业画像逐条核对,
输出可投送到飞书/企微/邮件的 Markdown 报告。

设计原则:**确定性优先**。能用正则/规则解析的绝不丢给 LLM;语义模糊的
(行业契合、属地"本市/本省")标记 `needs_llm_review`,由上层 Agent 复核。

可作为采集器的下游:采集→详情正文→结构化→匹配→推送。

## When to Use

> **真实数据精度提示(v1.1, n=54)**:真实政府网采集到的内容约 70% 为
> 新闻/会议/招聘/法条,非申报类;`triage_category` 分流准确率较高。
> 申报类核心字段提取率偏低(`conditions` 9%、`funding` 6%、`support_targets` 7%),
> 当前版本更适合作为**分流过滤器**,结构化提取与匹配作为辅助,语义复核仍需 LLM 介入。

触发场景:
- 拿到政策详情页正文(`gov-doc-collector.fetch_detail()` 的 `content_text`),
  需要提取"支持对象 / 支持方式 / 资金额度 / 申报条件 / 申报材料 / 有效期"。
- 有企业画像(profile),想快速筛出"本公司能不能申报"。
- 给 cron 任务产出日报、给 LLM 准备结构化 prompt。

不适用:
- 政策分类/主题打标 → 用 `gov-doc-collector` 的 `category` 字段。
- 法条实体抽取 / 责任主体识别 → 这是 NLP 任务,本 skill 不做。
- 多文档对比/版本追踪 → 当前版本暂不支持(规划中)。

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
    'as_of': '2026-07-31',             # 计算 company_age 的基准日,缺省取当天
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

### triage 分类:LLM 优先 + 规则兜底

`triage_category`(apply/regulate/news/other)默认调用 LLM 判断,
解决纯关键词规则的歧义问题(如"检查"既是新闻信号又是监管动作)。
LLM 不可用/超时/返回异常时自动回退到关键词规则,保证可用性。

```python
from scripts.policy_parser import parse_policy

# 默认:LLM 可用时用模型,不可用时回退规则
parsed = parse_policy(text, title=title)

# 强制走规则(不调 LLM)
parsed = parse_policy(text, title=title, use_llm_triage=False)

# 结果含 triage_method 标记实际用了哪种方式
print(parsed['triage_category'], parsed['triage_method'])  # apply / 'llm' 或 'rules'
```

**LLM 配置**(环境变量,兼容任意 OpenAI 兼容 API):

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `POLICY_LLM_API_KEY` | (空) | API Key。未设置则跳过 LLM,走规则 |
| `POLICY_LLM_BASE_URL` | `https://api.openai.com/v1` | API 基址,可指向 Doubao/Qwen/DeepSeek 等 |
| `POLICY_LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `POLICY_LLM_TIMEOUT` | `15` | 超时秒数 |

> 规则版作为兜底始终可用;LLM 仅用于提升分流准确率,不改变其它字段提取逻辑。

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
     "needs_llm_review": false, "any_mode": false},
    {"text": "上年度营业收入不低于1000万元", "field": "revenue",
     "op": ">=", "value": 1000, "unit": "万元", "hard": true,
     "needs_llm_review": false, "any_mode": false},
    {"text": "拥有有效发明专利2件以上", "field": "patents",
     "op": ">=", "value": 2, "unit": "件", "hard": true,
     "needs_llm_review": false, "any_mode": false},
    {"text": "高新技术企业", "field": "qualification",
     "op": "has", "value": ["高新技术企业"], "hard": true,
     "needs_llm_review": false, "any_mode": false}
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
| `conditions[]` | "申报条件/支持对象/支持内容" 块 + 句级兜底 | 拆分到字段(age/revenue/qualification…),含 `any_mode` 标记"或"关系 |
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

**"或"关系救援**:同条款内若含"或/或者"(`any_mode=true`)且任一子条件 `pass`,
整组降级到 `pass`。匹配时,`unknown` / `review` 计入 `likely` 而非直接判 `eligible`,
避免字段不全时被错判通过。

**资质别名**:政策中"中小企业"对应画像的"小微企业/中小微企业/中型企业"等;
"高新技术企业"对应"国家高新技术企业/国高新/高企"。

## CLI 用法

```bash
# 跑样例(无参数): 自带申报通知 + profile → 输出 verdict=ineligible/likely
python scripts/company_matcher.py

# 解析单文件(读 stdin,输出 JSON)
python scripts/policy_parser.py < policy.txt > parsed.json
```

> 注:两个脚本均支持直接运行(`python scripts/xxx.py`)和包内导入
> (`from scripts.policy_parser import parse_policy`)两种方式,后者需在
> `skills/policy-analyzer/` 目录下执行,已通过 `__init__.py` + 相对导入回退兼容。

## Pitfalls

- **大正文**:HTML 详情页 > 20 万字会被 `max_chars`(默认 `DEFAULT_MAX_CHARS=200000`)
  截断,`stats.truncated=True` 标记;截断可能影响 `validity`、`outline` 提取。
  建议在调用前清理 `<script>`/`<style>`/导航,或通过 `parse_policy(..., max_chars=0)` 关闭截断。
- **重复段落**:部分政府网详情页正文被复制多次,parser 端用 `seen` 去重;
  fetch 端也应考虑对 `content_text` 做去重。
- **"或"歧义**:"发明专利 2 件以上,或获得高新技术企业认定"——只要分公司
  拿高新认了,这条整体 pass。`match_policy` 已通过 `any_mode` 标记自动处理,
  数值类与资质类"或"关系均可救援。
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
python scripts/diagnose_sites.py     # 站点覆盖率诊断
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

- `policy_parser` 依赖 Python 标准库 + `re`,引入 `logging`(默认无输出,
  调用方配置 logging 后可见 debug/warning)。
- `llm_triage` 依赖 `requests`(已在项目 requirements.txt),调用 OpenAI 兼容 API;
  未配置 `POLICY_LLM_API_KEY` 时自动跳过,回退规则。
- `company_matcher` 仅依赖 Python 标准库 + `datetime`。
- `scripts/__init__.py` 使目录可作为包导入,兼容直接脚本运行。
- 不依赖 `gov-doc-collector`,但配合使用效果最好(同目录 `scripts/` 提供
  `UnifiedFetcher`,输出 `detail` 直接喂给 `parse_from_detail`)。

## 依赖安装

无外部依赖,Python 3.9+ 即可。

## 更新日志

### v1.4.0 (2026-08-03)
- ✨ **属地条件推断**:`region` 条件不再一律 review——用政策 `issuer`/标题
  识别辖区(31 省 + 直辖市 + 36 个主要地级市→省映射),画像注册地可解析时:
  - 省级政策 vs 画像同省(直接含省名,或经 城市→省 映射)→ `pass`
  - 市级政策 vs 画像同城 → `pass`;跨城 → `fail`
  - 发文机关识别不出辖区 / 画像无法解析 → 保持 `review`
- ✨ **截止时间感知**:`format_report` 对已过 `deadline` 的政策标注
  "⚠️ 已过申报截止日 N 天,本期不可申报"
- 🐛 **triage_method 标签修复**:`_classify_triage` 返回 (category, method),
  method 反映实际产出路径;LLM 超时回退规则时不再误标 `'llm'`

### v1.3.1 (2026-08-03)
- 🐛 **修复申报条件跨章节污染**(结论级 bug):"支持标准/支持内容/支持方式/支持范围"
  等资金章节原先会被当作条件块收集,资金描述("获评专精特新小巨人…给予补助")
  被误判为资质要求,产生假 fail、把企业误判为 ineligible
  - 新增 `FUNDING_HEADER_RE`:资助类章节头直接跳过,不当条件块
  - 收集循环加块边界(`block_added`):描述段只拼接**本块**自己的条目,
    不再跨块拼接到上一条件块的最后一条
- 🧹 DOC_TYPES 清理:"保底冗余"重复项删除;兜底关键词循环与 chunk 扫描层一致,
  均跳过单字"法"(避免"执法/法治"类标题误判为法律文种)
- ✅ 回归测试:`scripts/test_regressions.py`(仓库根)固化以上修复

### v1.3.0 (2026-07-31)
- ✅ **triage 分类改为 LLM 优先 + 规则兜底**:不再依赖纯关键词归属判定
  - 新增 `llm_triage.py`:OpenAI 兼容 API 调用(仅依赖 requests,不增依赖)
  - `_classify_triage` 编排:LLM 可用时优先用模型,不可用/超时自动回退规则
  - 原 `_classify_triage` 改名 `_classify_triage_by_rules` 作为兜底
  - `parse_policy` / `parse_from_detail` 新增 `use_llm_triage` 参数(默认 True)
  - 结果新增 `triage_method` 字段(`llm` / `rules`),标记实际分类方式
  - 环境变量配置:`POLICY_LLM_API_KEY` / `POLICY_LLM_BASE_URL` / `POLICY_LLM_MODEL` / `POLICY_LLM_TIMEOUT`

### v1.2.0 (2026-07-31)
- ✅ **triage 分类重构**:准确率 81% → 95%(37 用例验证)
  - 扩充 news 关键词:宣讲/分析会/签署/放假/座谈/论坛/贯彻/战略合作等
  - 移除歧义词"检查"(监管检查≠新闻),新增 `_REGULATE_KW_RE` 判定规范类
  - 调整逻辑顺序:`_NEWS_TITLE_RE` 不再全局抢先,仅在通知/公告分支内兜底
  - 通知分支三路细分:apply(申报词) > regulate(监管词/贯彻落实) > news(会议活动) > other
  - 扩充 apply 关键词:评价/评估/考核/推荐/备案/试点/示范(`_APPLY_KW_RE`)
- ✅ **`max_chars` 截断落地**:`parse_policy` / `parse_from_detail` 新增 `max_chars`
  参数(默认 `DEFAULT_MAX_CHARS=200000`),超长正文截断并标记 `stats.truncated`
- ✅ `parse_from_detail` 输入校验:空 detail / 空 content_text 返回带 `parse_status` 的结果
- ✅ **导入路径修复**:新增 `scripts/__init__.py`,相对导入 + 脚本回退双兼容
- ✅ `_company_age` 用 `datetime.date` 精确计算,`as_of` 缺省取当天;`PROFILE_SCHEMA` 补 `as_of`
- ✅ **"或"救援扩展**:条件新增 `any_mode` 字段,数值类"或"关系亦可救援
- ✅ 引入 `logging`(debug/warning),散落 magic number 提取为命名常量
- ✅ 删除死代码 `TRIAGE_DOC_TYPES` / `NON_TRIAGE_DOC_TYPES`
- ✅ 文档:修正 Verification 幽灵脚本引用、删除 `policy-monitor` 引用、
  如实标注真实精度、同步 `max_chars` 说明

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
