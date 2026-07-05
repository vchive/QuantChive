# Data Model: 通用市场观测平台

**Feature**: 002-market-observation-platform | 决策见 [research.md](./research.md)

## 全局约定

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

- 金额 INTEGER 分（×100）、价格 ×1e6 微元、百分比 ×1e4 基点、净值 ×1e6——全整数标度，禁 float 做数值精度。每指标标度由 `metric_def.scale_factor` 定义。
- 时间：`*_at` UTC ISO-8601；`trade_date`(YYYY-MM-DD)/`minute_slot`(HH:MM) 由 Asia/Shanghai 派生。

## 表定义

### 1. asset_class（品种，预留位）

```sql
CREATE TABLE asset_class (
    asset_class_code TEXT PRIMARY KEY,   -- 'a_share'|'fund_etf'|'bond'|'futures'|'commodity'|'fx'
    display_name     TEXT NOT NULL,
    is_populated     INTEGER NOT NULL DEFAULT 0 CHECK (is_populated IN (0,1)),
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
-- seed: a_share(1) fund_etf(1) bond(0) futures(0) commodity(0) fx(0)
```

### 2. data_source

```sql
CREATE TABLE data_source (
    source_code        TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    caliber            TEXT NOT NULL,            -- 校验上移 Pydantic(去硬枚举 CHECK)
    amount_unit        TEXT NOT NULL CHECK (amount_unit IN ('yuan','yi')),
    supports_five_tier INTEGER NOT NULL CHECK (supports_five_tier IN (0,1)),
    has_daily_final    INTEGER NOT NULL CHECK (has_daily_final IN (0,1)),
    adapter_impl       TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at         TEXT NOT NULL
);
-- seed: eastmoney(yuan, five_tier=1), em_fund(按需, five_tier=0)
```

### 3. subject（取代 instrument，统一大盘/板块/个股/ETF/基金）

```sql
CREATE TABLE subject (
    subject_id       INTEGER PRIMARY KEY,
    asset_class_code TEXT NOT NULL REFERENCES asset_class(asset_class_code),
    level            TEXT NOT NULL CHECK (level IN ('market','sector','instrument')),
    subject_kind     TEXT NOT NULL CHECK (subject_kind IN
                       ('market_total','industry','concept','region','stock','etf','open_fund')),
    source_code      TEXT NOT NULL REFERENCES data_source(source_code),
    source_symbol    TEXT NOT NULL,             -- '600519'/'BK0475'/'512880'/'__MARKET__'
    display_name     TEXT NOT NULL,
    caliber          TEXT NOT NULL,
    exchange         TEXT,                       -- 'SSE'|'SZSE'|'BSE'|NULL
    em_board_code    TEXT,
    collect_tier     TEXT NOT NULL DEFAULT 'daily'
                     CHECK (collect_tier IN ('minute','coarse','derived','on_demand')),
    is_hot           INTEGER NOT NULL DEFAULT 0 CHECK (is_hot IN (0,1)),
    list_date TEXT, delist_date TEXT,
    first_seen_at    TEXT NOT NULL, last_seen_at TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    UNIQUE (asset_class_code, subject_kind, source_code, source_symbol)
);
CREATE INDEX idx_subject_level_kind ON subject (asset_class_code, level, subject_kind) WHERE is_active=1;
CREATE INDEX idx_subject_symbol ON subject (source_symbol);
```
> spec001 留空的 instrument 并入 subject（level='instrument'）；个股/ETF/基金/大盘/板块同构一张表。无 parent_subject_id（归属走 subject_membership）。

### 4. subject_membership（as-of 无前视，泛化 sector_constituent）

```sql
CREATE TABLE subject_membership (
    membership_id     INTEGER PRIMARY KEY,
    parent_subject_id INTEGER NOT NULL REFERENCES subject(subject_id),   -- 板块
    child_subject_id  INTEGER NOT NULL REFERENCES subject(subject_id),   -- 个股
    relation_kind     TEXT NOT NULL DEFAULT 'sector_constituent'
                      CHECK (relation_kind IN ('sector_constituent','index_constituent','fund_holding')),
    effective_from    TEXT NOT NULL,            -- 生效(含)
    effective_to      TEXT,                     -- 失效(不含); NULL=当前
    source_code       TEXT NOT NULL REFERENCES data_source(source_code),
    ingestion_run_id  INTEGER REFERENCES ingestion_run(run_id),
    created_at        TEXT NOT NULL,
    UNIQUE (parent_subject_id, child_subject_id, relation_kind, effective_from)
);
CREATE INDEX idx_membership_asof ON subject_membership (parent_subject_id, effective_from, effective_to);
CREATE INDEX idx_membership_child ON subject_membership (child_subject_id, effective_from);
-- as-of: WHERE parent=? AND effective_from<=:asof AND (effective_to IS NULL OR effective_to>:asof)
-- 本期无真实数据(slist 未通)；成分退出必须闭合 effective_to
```

### 5. metric_def + metric_applicability（能力自描述）

```sql
CREATE TABLE metric_def (
    metric_name  TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    value_kind   TEXT NOT NULL CHECK (value_kind IN ('money_cents','price_micro','percent_bp','count','nav_micro')),
    unit_label   TEXT NOT NULL,
    scale_factor INTEGER NOT NULL,              -- 存储→真实除数: 100/1e6/1e4
    storage      TEXT NOT NULL CHECK (storage IN ('fixed_column','metric_kv')),
    fixed_column TEXT,
    is_sortable  INTEGER NOT NULL DEFAULT 0 CHECK (is_sortable IN (0,1)),
    created_at   TEXT NOT NULL
);
CREATE TABLE metric_applicability (
    metric_name      TEXT NOT NULL REFERENCES metric_def(metric_name),
    asset_class_code TEXT NOT NULL REFERENCES asset_class(asset_class_code),
    subject_kind     TEXT,                       -- NULL=该品种全体
    PRIMARY KEY (metric_name, asset_class_code, subject_kind)
);
```

**metric_def seed**：

| metric_name | value_kind | scale | storage | fixed_column | sortable |
|---|---|---|---|---|---|
| main_net | money_cents | 100 | fixed | main_net_cents | 1 |
| super_large/large/medium/small_net | money_cents | 100 | fixed | *_net_cents | 1 |
| price | price_micro | 1e6 | fixed | price_micro | 0 |
| change_pct | percent_bp | 100 | fixed | change_pct_bp | 1 |
| turnover(成交额) | money_cents | 100 | fixed | turnover_cents | 0 |
| turnover_pct(换手率) | percent_bp | 100 | fixed | turnover_pct_bp | 0 |
| volume | count | 1 | fixed | volume | 0 |
| circ_mktcap(ETF规模proxy) | money_cents | 100 | metric_kv | NULL | 0 |
| nav(基金净值) | nav_micro | 1e6 | metric_kv | NULL | 0 |

> 删除独立 `net_amount` metric（东财 net_amount=main_net 恒等）；SortField.NET_AMOUNT 别名 main_net 或移除。价格 ×1e6 给足小数余量（兼容基金 4 位）。

### 6. observation（核心，泛化 sector_money_flow）

```sql
CREATE TABLE observation (
    observation_id   INTEGER PRIMARY KEY,
    subject_id       INTEGER NOT NULL REFERENCES subject(subject_id),
    source_code      TEXT NOT NULL REFERENCES data_source(source_code),
    trade_date       TEXT NOT NULL,
    minute_slot      TEXT NOT NULL,             -- 'HH:MM' | 'LATEST'(个股覆盖) | 'EOD' | 'HH:00'(降采后小时点)
    value_type       TEXT NOT NULL CHECK (value_type IN ('intraday_snapshot','intraday_latest','daily_final','hourly_rollup')),
    granularity      TEXT NOT NULL CHECK (granularity IN ('1min','5min','hourly','daily')),
    observed_at      TEXT NOT NULL,
    -- 资金流五档固定列(全可空)
    net_amount_cents INTEGER,                    -- 从 spec001 NOT NULL 放宽为可空
    main_net_cents INTEGER, super_large_net_cents INTEGER, large_net_cents INTEGER,
    medium_net_cents INTEGER, small_net_cents INTEGER,
    inflow_cents INTEGER, outflow_cents INTEGER,
    -- 价格/量固定列
    price_micro INTEGER,                         -- 现价×1e6
    change_pct_bp INTEGER,                       -- 涨跌幅×1e4 基点(禁float)
    volume INTEGER,                              -- 成交量
    turnover_cents INTEGER,                      -- 成交额分
    turnover_pct_bp INTEGER,                     -- 换手率×1e4
    -- 大盘聚合审计(仅 derived 行非空)
    constituent_count INTEGER, expected_count INTEGER,
    source_unit TEXT NOT NULL CHECK (source_unit IN ('yuan','yi')),
    raw_value TEXT,                              -- 仅 daily_final 存(控存储)
    is_derived INTEGER NOT NULL DEFAULT 0 CHECK (is_derived IN (0,1)),
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(run_id),
    created_at TEXT NOT NULL,
    -- 五档 all-or-nothing
    CHECK ((main_net_cents IS NULL AND super_large_net_cents IS NULL AND large_net_cents IS NULL
            AND medium_net_cents IS NULL AND small_net_cents IS NULL)
        OR (main_net_cents IS NOT NULL AND super_large_net_cents IS NOT NULL AND large_net_cents IS NOT NULL
            AND medium_net_cents IS NOT NULL AND small_net_cents IS NOT NULL)),
    -- 至少一个指标列非 NULL(防空壳行冒充)
    CHECK (main_net_cents IS NOT NULL OR price_micro IS NOT NULL OR volume IS NOT NULL
           OR turnover_cents IS NOT NULL OR net_amount_cents IS NOT NULL)
);
CREATE UNIQUE INDEX uq_observation ON observation (subject_id, trade_date, value_type, minute_slot);
CREATE INDEX idx_obs_ranking ON observation (trade_date, value_type, minute_slot, subject_id);
CREATE INDEX idx_obs_trade_date ON observation (trade_date);
```
> 幂等键 (subject_id, trade_date, value_type, minute_slot)。intraday_latest+'LATEST' 使个股当日 1 行覆盖。排行 Service 先经 subject/subject_membership 缩小 subject_id 集再进 observation（避免跨 8048 主体全表扫）。

### 7. observation_metric（稀疏附表）

```sql
CREATE TABLE observation_metric (
    obs_metric_id  INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL REFERENCES observation(observation_id) ON DELETE CASCADE,
    metric_name    TEXT NOT NULL REFERENCES metric_def(metric_name),
    value_int      INTEGER NOT NULL,            -- 按 metric_def.scale_factor 标度整数(禁float)
    UNIQUE (observation_id, metric_name)
);
CREATE INDEX idx_obsmetric_name ON observation_metric (metric_name, observation_id);
-- 本期真实写入: ETF circ_mktcap(f21); nav/规模因 on_demand 不入库=预留
```

### 8. ingestion_run（泛化列名）

```sql
CREATE TABLE ingestion_run (
    run_id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL REFERENCES data_source(source_code),
    run_type TEXT NOT NULL CHECK (run_type IN
              ('intraday_snapshot','eod_backfill','market_aggregate','retention_cleanup','retention_downsample')),
    asset_class_code TEXT REFERENCES asset_class(asset_class_code),
    subject_scope TEXT,                          -- 'all_stocks'|'industry'|'etf'
    caliber TEXT NOT NULL,
    trade_date TEXT NOT NULL, minute_slot TEXT,
    started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('success','partial','failed','interrupted')),
    subjects_ok INTEGER NOT NULL DEFAULT 0, subjects_failed INTEGER NOT NULL DEFAULT 0,
    aggregate_coverage TEXT,                     -- 大盘求和成分哈希/覆盖率
    target_interval_sec INTEGER, achieved_interval_sec INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT, error_detail TEXT,
    adapter_version TEXT NOT NULL, code_version TEXT, created_at TEXT NOT NULL
);
CREATE INDEX idx_run_date_type ON ingestion_run (trade_date, run_type, source_code);
CREATE INDEX idx_run_status ON ingestion_run (status) WHERE status != 'success';
```

### 9. trade_calendar（原样沿用 spec001）

```sql
CREATE TABLE trade_calendar (trade_date TEXT PRIMARY KEY, is_open INTEGER NOT NULL CHECK (is_open IN (0,1)));
```

## 保留期分级降采（用户决策）

- **近 7 天**：保留原始 1min/5min 粒度。
- **8-30 天**：`retention_downsample` 任务每天把 7 天前的分钟数据聚合成 `granularity='hourly'` / `value_type='hourly_rollup'` / `minute_slot='HH:00'` 的小时点，删原细粒度行（CASCADE 带走 observation_metric）。降采口径：资金流取小时末累计值、价格取小时收盘、量取小时累计。
- **保留清理**：`retention_cleanup` 删 `trade_date < today - 30`（配置化）。
- **存储**：全 1min 30天 14.2GB → 采集分层 2.5-4GB → 叠加降采 ~1.5-2.5GB。

## 迁移映射（spec001 → 002）

| spec001 | 002 |
|---|---|
| sector | subject(level='sector') |
| sector_money_flow | observation(资金流五档固定列) |
| instrument(空) | subject(level='instrument') |
| sector_constituent(空) | subject_membership |
| ingestion_run | ingestion_run(泛化列名) |
| data_source / trade_calendar | 沿用 |

> 扩展路线：新表与旧 sector_* 表并存至 Step7 一次性收敛 drop。
