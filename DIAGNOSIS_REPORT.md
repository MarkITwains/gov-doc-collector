# 政府网页打不开问题 — 根因诊断报告

**日期**: 2026-06-11
**版本**: v1.3.0 → 修复中
**项目**: gov-doc-collector (Hermes Agent Skill)

---

## 1. 现状

`diagnose_results.json` 显示 61 个站点中 17 个不可用（72% 可用率）。现场抽样探测证实大部分"失败"站点对裸 `requests` 请求返回的是 `Connection Reset / HTTP 412 / HTTP 521 / 404` 而不是政策内容。

## 2. 5 大根因（按影响面排序）

### 根因 A：配置 `search_path: "/"` 命中首页而非政策列表页

**影响**: 23 个国家部委 + 多个省份的配置都把 `search_path` 设为 `/`，代码会去请求政府网站首页。首页结构是新闻+领导活动+导航+友情链接，CSS 选择器 `ul li > a > span` 会匹配出几十条"看起来像列表"的内容（SAMR 84 条、CSRC 205 条、MEE 245 条），但**实际是新闻、领导活动、通知公告，不是政策法规**。

> 这不叫"打不开"，叫"打开的不是政策页"。SKILL 自报"数据质量 100%"是误导。

**证据**:
- `configs/sites/national.json` 中 30 个部委里有 23 个 `search_path: "/"`
- `diagnose_results.json` 里 `samr` 标记 OK 但 `count=84`，首页不可能有 84 条政策法规

### 根因 B：反爬虫能力标记缺失

**影响**: 17 个失败站点中至少 8 个可以通过 `curl_cffi` 浏览器指纹绕过 WAF。

- `unified_fetcher.py` 第 81 行检查 `config.get('use_cffi', False)`
- `configs/sites/*.json` 里**只有 jiangxi 一个**标了 `use_cffi: true`
- 其他 7 个 WAF/RST 站点（nhc/hubei/gansu/mps/cbirc/qinghai/xinjiang）**没有这个标记**，采集器完全不会走 curl_cffi 通道

**证据**:
- nhc/hubei/gansu: HTTP 412 — 阿里云 WAF
- mps: HTTP 521 + JS Challenge 字符串 — Cloudflare
- cbirc: TCP RST — 自建 WAF

### 根因 C：JS 渲染标记错误

- `mwr` 标了 `need_js: true` 但实际是 TCP 连接超时（CONN_ERROR），不是 JS 问题 → 标记无效
- `hunan` 标了 `NEED_JS?` 但现场探测是 TLS 握手失败（CONN_ERROR），跟 JS 无关 → 标记误导

### 根因 D：路径未维护（HTTP_404 站点）

6 个省 + 宗教局共 7 个站点的 `search_path` 已经在网站上失效：

| 站点 | 失败原因 |
|---|---|
| chongqing 重庆市 | 路径 `/zwgk/` 404 |
| shanxi 山西省 | 404 |
| hebei 河北省 | 404 |
| jilin 吉林省 | 404 |
| tianjin 天津市 | 404 |
| zhejiang 浙江省 | 404 |
| nra 国家宗教事务局 | 404 |

政府网站两三年改版一次，路径是手填的没人维护。

### 根因 E：skill.md 数字打架

同一份文档中：
- 第 28 行: "国家级 (25/30 可用)"
- 第 183 行: "成功率: 23/30 国家部委 (76.7%)"
- 第 47 行: "总计: 61 个站点, 可用: 44 个 (72%)"
- 第 263 行: "可用率提升: 65% → **72%**"

不同段落写不同的数字，是 `init` 模板生成时手填的，没和 `diagnose_results.json` 对齐。

## 3. 修复方案

| 优先级 | 任务 | 改动 | 预期收益 |
|---|---|---|---|
| P0 | 补全 `search_path: "/"` 为真实政策栏目 | `national.json` / `provincial.json` | 23 个部委数据从"首页新闻"变成"政策法规"，质量提升 |
| P0 | 给 WAF/JS Challenge 失败站点加 `use_cffi: true` | 一行配置 | 救回 5-7 个 412/521/RST 站点 |
| P1 | 修 hunan 错误标记 | 配置改 `use_cffi` | hunan 走对策略 |
| P1 | 修 mwr 错误标记 | 配置去掉 `need_js` | 后续调试不绕弯 |
| P2 | 修 6 个 404 省市的搜索路径 | 配置 + 验证 | 救回 6 个站点 |
| P2 | 修 skill.md 数字 | 文档 | 消除歧义 |
| P3 | 跑 `diagnose_sites.py` 验证 | 工具运行 | 数字对齐 |

## 4. 验收标准

- 跑 `python scripts/diagnose_sites.py`，可用率从 72% 提升
- `diagnose_results.json` 中 `HTTP_404` 数量从 7 降到 ≤ 2
- `HTTP_412 / CONN_RESET` 配合 `use_cffi` 后在 `unified_fetcher` 测试中能取到内容
- `skill.md` 全文数字统一，与 `diagnose_results.json` 一致

---

**待办**:
- [ ] 写报告
- [ ] 修 search_path
- [ ] 加 use_cffi 标记
- [ ] 修 hunan 标记
- [ ] 修 6 个 404 站点
- [ ] 修 skill.md 数字
- [ ] 跑诊断验证
