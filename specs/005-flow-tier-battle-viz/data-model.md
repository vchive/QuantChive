# Data Model: 资金档位博弈可视化 + 多源数据层

**Feature**: 005 | 决策见 [research.md](./research.md)

**范围**：observation 加 4 列 + CHECK；多源 seed；派生/校验为内存/审计结构。**观测三表核心结构不变**。

## 1. observation 加 4 档 gross 列（D1）

```sql
-- 幂等 ALTER（db_init）；新库 schema.sql 直接含：
ALTER TABLE observation ADD COLUMN super_large_gross_cents INTEGER;
ALTER TABLE observation ADD COLUMN large_gross_cents       INTEGER;
ALTER TABLE observation ADD COLUMN medium_gross_cents      INTEGER;
ALTER TABLE observation ADD COLUMN small_gross_cents       INTEGER;
```
- 语义：各档**成交额**（买+卖，整数分）。与已有 4 档 net 配对 → 推流入流出。
- **不加主力 gross**（=超大+大，可推）。
- 单位：整数分（`to_cents`，禁 float）。
- 归属：**个股**存（新浪源）；东财/baostock 行四档 gross 全 NULL；板块**派生**（见 §4）；ETF 全 NULL。

**CHECK（新库 schema.sql；旧库迁移不强加，靠入库校验）：**
```sql
CHECK (
  (super_large_gross_cents IS NULL AND large_gross_cents IS NULL
   AND medium_gross_cents IS NULL AND small_gross_cents IS NULL)
  OR
  (super_large_gross_cents IS NOT NULL AND large_gross_cents IS NOT NULL
   AND medium_gross_cents IS NOT NULL AND small_gross_cents IS NOT NULL)
)
```
四档 gross 全有（新浪行）或全无（东财/baostock 行）。

## 2. 派生量（不落库，查询时算，D2）

| 派生 | 公式 | 约束 |
|---|---|---|
| 某档流入 | `(tier_gross + tier_net) // 2` | 整除恒精确 |
| 某档流出 | `(tier_gross - tier_net) // 2` | 整除恒精确 |
| 主力 gross | `超大gross + 大gross` | — |
| 散户净额 | `中net + 小net` | — |
| 累计主力净额 | Σ(main_net) 从最早日起（全程绝对）| 后端整数求和一次 |
| 背离标记 | 窗口价净变 vs 累计主力净变 方向相反 → 吸筹/派发 | 确定性 |

`core/money.py` 加纯函数：`tier_inflow(gross, net)`、`tier_outflow(gross, net)`（int→int）。

## 3. data_source seed 扩展（D3）

```
新增 seed（幂等）：
  ('baidu_flow', '百度资金流', 'baidu', 1, 0, 'yi', 'baidu_flow_src.BaiduFlowSource', 0→验证后1)
  ('tushare_flow', 'Tushare资金流', 'tushare', 1, 1, 'yuan', 'tushare_flow_src.TushareFlowSource', 0)  # 预留 is_active=0
```
- 百度：supports_five_tier=1、当日快照（has_daily_final=0）、caliber='baidu'、单位亿(归一到分)。
- Tushare：预留，is_active=0，路由跳过，不实现取数。
- 复用 spec004 的 caliber 无枚举 CHECK（已放宽）。

## 4. 板块四档 = 成分求和派生（D1/US5）

- 不落新表，不抓源。查询/聚合时：`板块超大gross = Σ 成分股超大gross`（各档同理），仿 `market_aggregate` 大盘求和。
- ≥覆盖率门禁（成分覆盖不足不落假值，宪章"宁缺勿假"）。
- 可选：派生结果写 is_derived=1 的 observation（板块 subject），或查询时实时算。plan 阶段倾向**盘后派生落 is_derived**（避免每次查询重算全成分）。

## 5. 校验记录（D6，只读审计不改数）

- 复用 `ingestion_run.degraded` + 新增校验明细（轻量）：
```
flow_validation（可选新表，或记入 ingestion_run.error_detail JSON）:
  subject_id, trade_date, check_type（identity|cross_source_direction|cross_source_magnitude）,
  source_a, source_b, verdict（ok|divergence|bad_data）, detail, checked_at
```
- 恒等式失败 → 拒入库（数据不进表）。跨源分歧 → 数据照进（新浪主源），只记分歧、前端角标。

## 6. 时间分层（D5，granularity 已有枚举）

| granularity | 层 | 保留 | 降采来源 |
|---|---|---|---|
| `1min` | 分钟 | 7 天 | 盘中采集 |
| `hourly` | 小时 | 7天~3月 | 分钟降采（取小时末累计快照）|
| `daily` | 日线 | 放宽 >3月 | 新浪回填 |

复用 `retention_downsample`/`retention_cleanup`；降采扩展处理 4 档 net + 4 档 gross（取小时末行）。

## 实体关系
```
observation（加4 gross列）── 派生 → 流入/流出/累计/背离（不落库）
data_source ── 加 baidu_flow / tushare_flow（预留）
subject_membership ── 板块四档 = Σ成分（派生）
ingestion_run ── 校验分歧/恒等式 审计（只标记）
```

## 迁移安全
- 4 gross 列 `ADD COLUMN`（幂等）；新库 schema.sql 含列 + CHECK。全新项目重建走 spec004 流程（备份→重建→复制维表→回填）。
- 观测三表核心结构不变，只加列 + seed + 派生 + 审计。
