# Data Model: 数据层韧性重构

**Feature**: 003-data-source-resilience | 决策见 [research.md](./research.md)

**范围**：本期只新增 1 张表 + 扩展 1 张表 + 若干配置/内存结构。**观测数据模型（subject/observation/observation_metric）不动**（spec 边界）。

## 全局约定（沿用 spec002）

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
```
- 金额 INTEGER 分、价格 ×1e6 微元、百分比 ×100 基点——多源归一后必须落到同一标度（禁 float）。

## 1. coverage_range（新表 · 已存区间，D5/FR-007）

```sql
CREATE TABLE IF NOT EXISTS coverage_range (
    coverage_id   INTEGER PRIMARY KEY,
    subject_id    INTEGER NOT NULL REFERENCES subject(subject_id),
    metric_kind   TEXT NOT NULL,          -- 'money_flow' | 'price_hist'（回填的历史指标类别；realtime 实时快照不入本表，无需缺口追踪）
    granularity   TEXT NOT NULL,          -- 'daily' | '1min' | '5min'
    source_code   TEXT NOT NULL REFERENCES data_source(source_code),
    start_date    TEXT NOT NULL,          -- 已存最早交易日（含）
    end_date      TEXT NOT NULL,          -- 已存最晚交易日（含）
    updated_at    TEXT NOT NULL,
    UNIQUE (subject_id, metric_kind, granularity, source_code)
);
CREATE INDEX IF NOT EXISTS idx_coverage_lookup
    ON coverage_range (subject_id, metric_kind, granularity);
```
- 语义：某主体某指标类某粒度某源，已连续入库 `[start_date, end_date]`。
- **补缺逻辑**：欲回填 `[want_start, want_end]` → 只请求不在已存区间的**缺口子区间**；回填后把新区间与旧区间**合并**（相邻/重叠合成一段；不连续则本期简化为「扩展到 min/max 并记 gap 由重跑补」——见 Edge Case）。
- **幂等**：重复回填同区间→缺口为空→零请求、区间不变（SC-003）。
- **无前视**：`end_date` 不得 > 真实今日；区间只增不覆盖已存观测行。

> 简化决策（本期）：coverage_range 记「连续主区间」；若历史有洞（中间缺日），合并时按 min(start)/max(end) 记录，实际补缺以「observation 表该区间内缺的交易日」为准（查 trade_calendar 交集 - 已存 observation）。即 coverage_range 是**快速缺口预判**，observation+calendar 是**权威缺日判定**。避免 coverage_range 变成复杂区间集。

## 2. ingestion_run（扩展 · 记实际使用的源，FR-013/019）

```sql
-- 幂等 ALTER（db_init._alter_ingestion_run 追加）：
ALTER TABLE ingestion_run ADD COLUMN used_source_code TEXT;   -- 实际取数的源（主源限流切备源时≠请求源）
ALTER TABLE ingestion_run ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0;  -- 1=识别到限流降级
```
- `used_source_code`：主备切换后落实际用源（审计可追「这批数据来自东财还是同花顺」）。
- `degraded`：限流降级标记（配合 aggregate_coverage/error_detail 的降级原因）。
- 沿用现有 `_alter_ingestion_run` 幂等 ALTER（PRAGMA table_info 查列后加）。

## 3. data_source（seed 扩展 · 新源，D4/D6）

```sql
-- db_init._SEED_SOURCES 追加（ON CONFLICT DO UPDATE 幂等）：
-- ('baostock', 'baostock 历史行情', 'eastmoney'? → 'baostock', 0, 1, 'yuan', 'baostock_src.BaostockSource')
-- ('ths_flow', '同花顺资金流', 'ths', 1, 0, 'yuan', 'ths_flow_src.ThsFlowSource')
```
- caliber 语义放宽（spec002 已去硬枚举 CHECK）；baostock caliber 记 'baostock'，ths_flow 记 'ths'。
- `is_active`：ths_flow 初始可置 0，hexin-v 验证通过后置 1（D6 优雅降级）。

## 4. 源路由配置（内存/settings，非表，D7）

**metric_kind 权威取值（全仓统一，I1）**：`money_flow`（五档资金流）| `price_hist`（历史价/量/额）| `realtime`（实时快照）。coverage_range 只记前两者（回填的历史）；routing/CLI 用这同一套命名。

```python
# routing.py 的路由表（可由 settings 覆盖）：
METRIC_SOURCE_ROUTES = {
    "money_flow":   ["eastmoney", "ths_flow"],   # 主→备
    "price_hist":   ["baostock"],
    "realtime":     ["eastmoney"],
}
```
- 不落表——纯配置，改路由不迁移 schema。工厂据此选主源，主源 DataSourceError(rate_limited/timeout) → 切下一个。

## 5. 归一约定（normalize.py，D7/FR-015/SC-007）

| 源 | 原始单位/字段 | 归一到内部约定 |
|---|---|---|
| 东财 clist/fflow | 金额=元、量=手 | 金额 to_cents（×100 分）、量保持手（沿用现状）|
| baostock | 价=元(str)、量=股、成交额=元 | 价 to_micro（×1e6）、量 ÷100 转手或标注股、额 to_cents |
| 同花顺 10jqka | 金额=元(可能亿元显示)、净占比=% | 金额 to_cents、占比 to_basis_points |
- 归一层输出统一为现有 `RawObservation`（不新增观测 DTO），保证下游 DAO/查询零改动。
- **复权口径**：baostock 支持前/后复权——历史价须标注复权类型（记 raw_value 或 metric 附注），不与不复权混存。

## 实体关系

```
subject ──1:N── coverage_range      （某主体各指标类的已存区间）
data_source ──1:N── coverage_range  （区间归属哪个源）
data_source ──1:N── ingestion_run   （used_source_code 记实际用源）
```

## 迁移安全

- coverage_range `CREATE TABLE IF NOT EXISTS`；ingestion_run 幂等 ALTER——不破坏现有 144 测试。
- 新源 seed 幂等；ths_flow 初始 is_active=0（验证后启用）。
- 观测表/查询层零改动——本期只加「进来的元数据」，不动「观测本身」。
