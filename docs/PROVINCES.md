# 省级政府网站配置列表

本文档列出所有已配置的省级政府网站及其站点标识符。

## 使用说明

```python
from fetcher import GovDocFetcher
fetcher = GovDocFetcher()
items = fetcher.fetch_list('site_key', 'provincial')
```

## 直辖市 (4个)

| 序号 | 名称 | 站点标识符 | 官网 | 状态 | 记录数 |
|------|------|-----------|------|------|--------|
| 1 | 北京市 | `beijing` | www.beijing.gov.cn | ✅ | 998 |
| 2 | 天津市 | `tianjin` | www.tj.gov.cn | ❌ | 0 |
| 3 | 上海市 | `shanghai` | www.shanghai.gov.cn | ✅ | 20 |
| 4 | 重庆市 | `chongqing` | www.cq.gov.cn | ❌ | 0 |

## 省份 (27个)

| 序号 | 名称 | 站点标识符 | 官网 | 状态 | 记录数 |
|------|------|-----------|------|------|--------|
| 1 | 河北省 | `hebei` | www.hebei.gov.cn | ❌ | 0 |
| 2 | 山西省 | `shanxi` | www.shanxi.gov.cn | ❌ | 0 |
| 3 | 辽宁省 | `liaoning` | www.ln.gov.cn | ✅ | 10 |
| 4 | 吉林省 | `jilin` | www.jl.gov.cn | ❌ | 0 |
| 5 | 黑龙江省 | `heilongjiang` | www.hlj.gov.cn | ✅ | 40 |
| 6 | 江苏省 | `jiangsu` | www.jiangsu.gov.cn | ✅ | 54 |
| 7 | 浙江省 | `zhejiang` | www.zj.gov.cn | ❌ | 0 |
| 8 | 安徽省 | `anhui` | www.ah.gov.cn | ✅ | 71 |
| 9 | 福建省 | `fujian` | www.fujian.gov.cn | ✅ | 168 |
| 10 | 江西省 | `jiangxi` | www.jiangxi.gov.cn | ❌ | 0 |
| 11 | 山东省 | `shandong` | www.shandong.gov.cn | ✅ | 19 |
| 12 | 河南省 | `henan` | www.henan.gov.cn | ✅ | 61 |
| 13 | 湖北省 | `hubei` | www.hubei.gov.cn | ❌ | 0 |
| 14 | 湖南省 | `hunan` | www.hunan.gov.cn | ❌ | 0 |
| 15 | 广东省 | `guangdong` | www.gd.gov.cn | ✅ | 120 |
| 16 | 广西壮族自治区 | `guangxi` | www.gxzf.gov.cn | ❌ | 0 |
| 17 | 海南省 | `hainan` | www.hainan.gov.cn | ⚠️ | 0 |
| 18 | 四川省 | `sichuan` | www.sc.gov.cn | ✅ | 29 |
| 19 | 贵州省 | `guizhou` | www.guizhou.gov.cn | ✅ | 63 |
| 20 | 云南省 | `yunnan` | www.yn.gov.cn | ✅ | 50 |
| 21 | 陕西省 | `shaanxi` | www.shaanxi.gov.cn | ✅ | 39 |
| 22 | 甘肃省 | `gansu` | www.gansu.gov.cn | ❌ | 0 |
| 23 | 青海省 | `qinghai` | www.qh.gov.cn | ❌ | 0 |
| 24 | 宁夏回族自治区 | `ningxia` | www.nx.gov.cn | ✅ | 86 |
| 25 | 新疆维吾尔自治区 | `xinjiang` | www.xinjiang.gov.cn | ✅ | 53 |
| 26 | 内蒙古自治区 | `neimenggu` | www.nmg.gov.cn | ✅ | 49 |
| 27 | 西藏自治区 | `xizang` | www.xizang.gov.cn | ✅ | 45 |

## 汇总统计

- **总计**: 31个（4个直辖市 + 27个省/自治区）
- **可用**: 18个 (58.1%)
- **总记录数**: 1975条
- **测试日期**: 2026-06-10

## 状态说明

- ✅ **可用**: 稳定返回数据
- ⚠️ **部分可用**: 站点可访问但返回0条记录
- ❌ **不可用**: 连接失败或返回错误

## 示例

### 采集北京市政策

```python
from fetcher import GovDocFetcher
fetcher = GovDocFetcher()
items = fetcher.fetch_list('beijing', 'provincial')
for item in items[:5]:
    print(f"[{item.get('date', 'N/A')}] {item['title']}")
```

### 采集多个省份

```python
from fetcher import GovDocFetcher
fetcher = GovDocFetcher()

provinces = ['beijing', 'shanghai', 'guangdong', 'jiangsu']
for key in provinces:
    items = fetcher.fetch_list(key, 'provincial')
    print(f"{key}: {len(items)} 条")
```

## 配置文件

所有站点配置存储在 `configs/sites/provincial.json`

## 已知问题

- 天津、重庆等直辖市站点路径需要进一步调整
- 部分省份有反爬虫限制或访问策略
- 吉林、浙江等站点返回403 Forbidden

## 更新日期

2026-06-10
