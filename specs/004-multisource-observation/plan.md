# 多源观测数据模型（source_code 进观测身份）— spec004

## Context

QuantChive 是通用市场观测平台（主体×指标×时序）。spec003 引入多源（baostock 价量、新浪资金流、同花顺、东财）后暴露一个**核心数据模型缺陷**：

`observation` 的唯一业务键是 `(subject_id, trade_date, value_type, minute_slot)`——**不含 `source_code`**（该列已存在于表中，只是不在唯一键里）。于是同股、同日、同 `daily_final/EOD`，baostock 价格行和新浪资金流行**撞同一键**：后写 upsert 覆盖前者、只留一个源。实测 140,799 行 baostock 数据会被同股同日的新浪资金流全部撞键覆盖。**同一主体同一天的多源观测无法共存。**

`source_code` 本应是观测身份的一部分（"哪个源在何时对哪个主体的哪个指标的观测"）。用户要求架构完备、不考虑向后兼容（全新项目）。本 spec 把 `source_code` 提升为观测的一等身份维度，并在查询层引入**按指标解析权威源**，让多源真正共存。

预期结果：一股一日可同时有 baostock 价 + 新浪五档流 + 东财实时；查某指标时按源优先级取权威值；中国船舶的 `main_net`（新浪）与 `price`（baostock）时序都正确、互不覆盖。

## 架构设计

### 决策 1：source_code 进观测业务键

唯一键 `(subject_id, trade_date, value_type, minute_slot)` → `(subject_id, source_code, trade_date, value_type, minute_slot)`。

- `dao/schema.sql:160` `uq_observation` 索引加 `source_code`。
- `dao/observation_dao.py` `upsert`(93 ON CONFLICT 目标 + 120-123 读回 SELECT) 加 `source_code`。
- `_OBS_COLS`(31-37) 加 `o.source_code`；`ObservationRow`(40-64) 加 `source_code` 字段（位置对齐）。
- **无迁移代码**：重建 schema、重跑回填（全新项目）。

### 决策 2：`metric_source.py`（新）— 按指标的源优先级

关键洞察：**series API 单指标**（`get_subject_series(metric=...)`），无需跨源列合并——每指标解析到权威源、逐日取值。

用**存储层实际 `observation.source_code` 值**（`eastmoney`/`baostock`/`sina_flow`/`ths_flow`，非 routing 键——三套命名不同，见 research）：
```python
METRIC_SOURCE_PRIORITY: dict[SortField, list[str]] = {
    MAIN_NET/NET_AMOUNT/SUPER_LARGE/LARGE/MEDIUM/SMALL: ["sina_flow","eastmoney","ths_flow"],
    PRICE / CHANGE_PCT: ["baostock","sina_flow","eastmoney"],
    VOLUME:             ["baostock","eastmoney"],
}
def resolve_metric_sources(metric: SortField) -> list[str]
REALTIME_SOURCE = "eastmoney"   # 排行/大盘的实时源
```
依据实测能力：新浪=五档资金流8年+价（不限流）；baostock=复权价量多年（无资金流）；东财=全但限流；同花顺=当日快照。资金流历史 sina 优先（深、不限流）。

### 决策 3：`series_daily` 指标感知 + 逐日源解析（核心查询改动）

`observation_dao.series_daily(subject_id, days, sources)`：按源优先级，逐 `trade_date` 取权威源中该指标**非空**的行（窗口函数）：
```sql
SELECT ... FROM (
  SELECT {_OBS_COLS}, ROW_NUMBER() OVER (
    PARTITION BY trade_date
    ORDER BY CASE source_code WHEN 'sina_flow' THEN 0 WHEN 'eastmoney' THEN 1 ... END) rn
  FROM observation o JOIN subject s ...
  WHERE subject_id=? AND value_type='daily_final' AND source_code IN (<sources>)
) WHERE rn=1 ORDER BY trade_date ASC LIMIT ?
```
`query_service._daily_series`(450) 传 `resolve_metric_sources(metric)`。main_net 取新浪行、price 取 baostock 行，各自逐日选权威源。

### 决策 4：排行/大盘/盘中 显式限定源（防重复行）

多源加入后，未过滤源的查询会一股多行。按 research 的「必须改」清单：
- `ranking_snapshot`(184)、`series`(203 盘中)、`stock_rows_for`(274 大盘求和)：加 `source_code` 参数，排行/盘中/求和限定 `REALTIME_SOURCE`（现仅东财写实时）。
- `latest_slot_for_subjects`(159)、`get_point`(286)、`latest_market_point`(299)：加源限定（realtime）。
- `service/market_aggregate.py:109` `stock_rows_for(...)` 传 `source_code=REALTIME_SOURCE`——防一股多源 LATEST 被重复求和、component_hash 重复。
- `query_service` 排行三处（`get_sector_ranking`/`get_stocks_in_sector`/`get_etf_ranking`）经改后 DAO 传实时源。

### 决策 5：保留 spec003 两个真实教训产物

- 采集互斥锁 `cli/collect_lock.py`（防并发写坏 SQLite 单写者）。
- 回填读回校验（`ingest_service` 写落库确认后才记 coverage，防孤儿区间→永久跳过）。

## 关键文件与触点

| 文件 | 改动（行号） |
|---|---|
| `dao/schema.sql` | uq_observation 加 source_code (160) |
| `dao/observation_dao.py` | upsert ON CONFLICT+读回(93,120)；_OBS_COLS+ObservationRow(31,40)；series_daily 源解析(209)；ranking_snapshot/series/stock_rows_for/latest_slot_for_subjects/get_point/latest_market_point 加源参(159,184,203,274,286,299) |
| `datasource/metric_source.py`（新） | METRIC_SOURCE_PRIORITY + resolve_metric_sources + REALTIME_SOURCE |
| `service/query_service.py` | _daily_series 传源(450)；排行三处传实时源(183,247,312) |
| `service/market_aggregate.py` | stock_rows_for 传 REALTIME_SOURCE(109) |
| （复用）`_SORT_COLUMN`(obs_dao 18) | 指标→列，源解析判非空复用 |

**安全触点（不改）**：`latest_slot`(已源过滤)、`upsert_metric`、`earliest_trade_date`、`delete_ids`、`delete_before_date`、各 `MAX(trade_date)` 标量。

## 验证

1. 单测 `upsert`：同键不同源 → 两行共存（不覆盖）。
2. 单测 `series_daily`：多源同日 → main_net 取 sina 行、price 取 baostock 行、无重复日点。
3. 单测排行/大盘：一股多源 → 不重复计数、component_hash 不含重复符号。
4. 集成：中国船舶回填 baostock 价 + 新浪流 → `get_subject_series(main_net)` 出新浪五档非空、`(price)` 出 baostock 价，无串味。
5. 全量 `uv run pytest` 全绿（现 206 + 新增；旧测按新键调整）。
6. 真跑：重建库 → baostock 全量价 + 新浪全量流 → 起服务，中国船舶 main_net 60 天真历史。

## 范围外

- 无向后兼容迁移（全新项目，重建库重跑回填）。
- 跨源列级合并（一行混多源列）——不做，单指标解析已足够。
- 新增数据源（tushare/代理池）——不接。

## 实施顺序（分阶段，每阶段 pytest 全绿）

1. Schema + upsert + _OBS_COLS/ObservationRow（+ 单测同键多源共存）。
2. metric_source.py + series_daily 源解析（+ 单测逐日选源）。
3. 排行/盘中/大盘限定实时源（+ 单测无重复）。
4. query_service 接线 + 旧测适配。
5. 重建库 + 真跑 baostock + 新浪全量 + UI 验证。
6. 提交（含 spec003 未提交的锁/guard/新浪源 + 本 spec004 全套 spec-kit 文档）。
