# 国家部委网站配置列表

本文档列出所有已配置的国家部委网站及其站点标识符。

## 使用说明

站点标识符 (site_key) 用于调用采集器：

```python
from fetcher import GovDocFetcher
fetcher = GovDocFetcher()
items = fetcher.fetch_list('site_key', 'national')
```

## 国家部委列表

| 序号 | 部委名称 | 站点标识符 | 官网 | 状态 |
|------|---------|-----------|------|------|
| 1 | 中国政府网 | `gov_cn` | www.gov.cn | ✅ |
| 2 | 国家发展和改革委员会 | `ndrc` | www.ndrc.gov.cn | ✅ |
| 3 | 教育部 | `moe` | www.moe.gov.cn | ✅ |
| 4 | 科学技术部 | `most` | www.most.gov.cn | ✅ |
| 5 | 工业和信息化部 | `miit` | www.miit.gov.cn | ⚠️ |
| 6 | 公安部 | `mps` | www.mps.gov.cn | ❌ |
| 7 | 民政部 | `mca` | www.mca.gov.cn | ✅ |
| 8 | 司法部 | `moj` | www.moj.gov.cn | ✅ |
| 9 | 财政部 | `mof` | www.mof.gov.cn | ✅ |
| 10 | 人力资源和社会保障部 | `mohrss` | www.mohrss.gov.cn | ⚠️ |
| 11 | 自然资源部 | `mnr` | www.mnr.gov.cn | ✅ |
| 12 | 生态环境部 | `mee` | www.mee.gov.cn | ✅ |
| 13 | 住房和城乡建设部 | `mohurd` | www.mohurd.gov.cn | ✅ |
| 14 | 交通运输部 | `mot` | www.mot.gov.cn | ✅ |
| 15 | 水利部 | `mwr` | www.mwr.gov.cn | ⚠️ |
| 16 | 农业农村部 | `moa` | www.moa.gov.cn | ✅ |
| 17 | 商务部 | `mofcom` | www.mofcom.gov.cn | ✅ |
| 18 | 文化和旅游部 | `mct` | www.mct.gov.cn | ✅ |
| 19 | 国家卫生健康委员会 | `nhc` | www.nhc.gov.cn | ❌ |
| 20 | 退役军人事务部 | `mara` | www.mva.gov.cn | ✅ |
| 21 | 应急管理部 | `mem` | www.mem.gov.cn | ✅ |
| 22 | 中国人民银行 | `pbc` | www.pbc.gov.cn | ✅ |
| 23 | 审计署 | `cnao` | www.audit.gov.cn | ✅ |
| 24 | 国家市场监督管理总局 | `samr` | www.samr.gov.cn | ✅ |
| 25 | 国家体育总局 | `gsa` | www.sport.gov.cn | ✅ |
| 26 | 国家宗教事务局 | `nra` | www.sara.gov.cn | ❌ |
| 27 | 国家能源局 | `nea` | www.nea.gov.cn | ✅ |
| 28 | 国家统计局 | `nsa` | www.stats.gov.cn | ✅ |
| 29 | 国家金融监督管理总局 | `cbirc` | www.cbirc.gov.cn | ❌ |
| 30 | 中国证券监督管理委员会 | `csrc` | www.csrc.gov.cn | ✅ |

## 状态说明

- ✅ **可用**: 稳定返回数据
- ⚠️ **部分可用**: 站点可访问但返回0条记录，可能需要JS渲染或路径调整
- ❌ **不可用**: 连接失败或返回错误，可能有反爬虫限制

## 状态分布 (2026-06-11)

- ✅ 22 个部委可用 (73%)
- ⚠️ 3 个 (miit/mohrss/mwr 需 JS 渲染)
- ❌ 5 个 (mps/nhc/cbirc/cnao/nra 需更高级反爬虫)

## 示例

### 采集发改委政策

```python
from fetcher import GovDocFetcher
fetcher = GovDocFetcher()
items = fetcher.fetch_list('ndrc', 'national')
for item in items[:5]:
    print(f"[{item['date']}] {item['title']}")
```

### 采集多个部委

```python
from fetcher import GovDocFetcher
import json

fetcher = GovDocFetcher()

# 需要采集的部委
ministries = ['gov_cn', 'ndrc', 'mof', 'mee']

for site_key in ministries:
    print(f"\n采集 {site_key}...")
    items = fetcher.fetch_list(site_key, 'national')
    print(f"获取 {len(items)} 条记录")
```

## 配置文件

所有站点配置存储在 `configs/sites/national.json`

## 更新日期

2026-06-11 — v1.4.0 search_path 批量修复
