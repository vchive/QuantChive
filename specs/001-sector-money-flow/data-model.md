# Data Model: 板块资金流

**Feature**: 001-sector-money-flow | **Date**: 2026-07-02 | 决策依据见 [research.md](./research.md)

## 全局约定

```sql
PRAGMA foreign_keys = ON;    -- SQLite 默认关闭外键, 不开则 FK 是死约束(宪章 II/V 引用完整性失效)
PRAGMA journal_mode = WAL;   -- 盘中"1-3分钟写 + 页面并发读", WAL 让读不阻塞写
PRAGMA busy_timeout = 5000;  -- 采集器与查询争锁时等待而非立即 SQLITE_BUSY
```

- **时间列**：观测墙钟 `*_at` 存 **UTC ISO-8601 TEXT**；业务时间 `trade_date`(YYYY-MM-DD) / `minute_slot`(HH:MM) 一律由 **Asia/Shanghai（+08:00，A股无夏令时）** 派生，禁用机器本地时区（宪章 I）。
- **金额列**：统一 `*_cents` 后缀，类型 `INTEGER`（有符号整数分，净流入为正）。存储/换算见 D1。
- **SQLite 写入**：单一 writer 连接串行化（ingest 专用连接），读连接分离；调度器 akshare 同步调用走 `asyncio.to_thread`。

## 表定义

### 1. `data_source` — 数据来源抽象（可选落库）

```sql
CREATE TABLE data_source (
    source_code        TEXT PRIMARY KEY,              -- 'eastmoney' | 'ths'
    display_name       TEXT NOT NULL,
    caliber            TEXT NOT NULL CHECK (caliber IN ('eastmoney','ths')),
    supports_five_tier INTEGER NOT NULL CHECK (supports_five_tier IN (0,1)),
    has_daily_final    INTEGER NOT NULL CHECK (has_daily_final IN (0,1)),  -- 东财=1, 同花顺=0
    amount_unit        TEXT NOT NULL CHECK (amount_unit IN ('yuan','yi')), -- 单位标度声明(D1)
    adapter_impl       TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at         TEXT NOT NULL
);
```

### 2. `sector` — 板块维表（代理键 D7）

```sql
CREATE TABLE sector (
    sector_id     INTEGER PRIMARY KEY,               -- 稳定代理键
    source_code   TEXT NOT NULL REFERENCES data_source(source_code),
    caliber       TEXT NOT NULL CHECK (caliber IN ('eastmoney','ths')),
    sector_type   TEXT NOT NULL CHECK (sector_type IN ('industry','concept','region')),
    source_symbol TEXT NOT NULL,                     -- akshare 板块名称(业务键)
    display_name  TEXT NOT NULL,
    em_board_code TEXT,                              -- 可选加固, 待实测补
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    UNIQUE (source_code, sector_type, source_symbol)
);
CREATE INDEX idx_sector_caliber_type ON sector (caliber, sector_type) WHERE is_active = 1;
```

### 3. `sector_money_flow` — 资金流记录（核心表）

```sql
CREATE TABLE sector_money_flow (
    flow_id          INTEGER PRIMARY KEY,
    sector_id        INTEGER NOT NULL REFERENCES sector(sector_id),
    source_code      TEXT NOT NULL REFERENCES data_source(source_code),

    trade_date       TEXT NOT NULL,        -- 'YYYY-MM-DD' (Asia/Shanghai)
    observed_at      TEXT NOT NULL,        -- UTC ISO-8601 (实际抓取时刻, 审计)
    minute_slot      TEXT NOT NULL,        -- 'HH:MM'(盘中); 'EOD'(日终 sentinel, D6/D8)
    value_type       TEXT NOT NULL CHECK (value_type IN ('intraday_snapshot','daily_final')),

    -- 规范化总净额(可排序, 非空): 东财=主力净额值; 同花顺=源净额 (D1)
    net_amount_cents      INTEGER NOT NULL,
    -- 五档: 仅东财; 同花顺全 NULL(=不提供, 非 0)
    main_net_cents        INTEGER,
    super_large_net_cents INTEGER,
    large_net_cents       INTEGER,
    medium_net_cents      INTEGER,
    small_net_cents       INTEGER,
    -- 同花顺辅助(可选): 流入/流出, 东财 NULL
    inflow_cents          INTEGER,
    outflow_cents         INTEGER,

    source_unit      TEXT NOT NULL CHECK (source_unit IN ('yuan','yi')),  -- 审计
    raw_value        TEXT,                 -- 原始值 JSON 字符串, 审计回溯
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(run_id),
    created_at       TEXT NOT NULL,

    -- 五档 all-or-nothing(东财全有 / 同花顺全无), 杜绝半档脏数据
    CHECK (
      (main_net_cents IS NULL AND super_large_net_cents IS NULL AND large_net_cents IS NULL
       AND medium_net_cents IS NULL AND small_net_cents IS NULL)
      OR
      (main_net_cents IS NOT NULL AND super_large_net_cents IS NOT NULL AND large_net_cents IS NOT NULL
       AND medium_net_cents IS NOT NULL AND small_net_cents IS NOT NULL)
    )
);

-- 幂等去重(D6): 盘中同分钟重试 upsert 同行; 日终值 'EOD' slot 物理隔离
CREATE UNIQUE INDEX uq_flow ON sector_money_flow (sector_id, trade_date, value_type, minute_slot);
CREATE INDEX idx_flow_lookback ON sector_money_flow (sector_id, trade_date, value_type, minute_slot);  -- 回看序列
CREATE INDEX idx_flow_ranking  ON sector_money_flow (trade_date, value_type, source_code);              -- 排行
CREATE INDEX idx_flow_trade_date ON sector_money_flow (trade_date);                                     -- 保留窗口清理
```

> 东财档位缺失（个别档 `-`/NaN，五档不全）→ 该行按采集失败处理，不写半档也不写 0，计入 `ingestion_run.sectors_failed`。

### 4. `ingestion_run` — 采集运行审计（宪章 V）

```sql
CREATE TABLE ingestion_run (
    run_id            INTEGER PRIMARY KEY,
    source_code       TEXT NOT NULL REFERENCES data_source(source_code),
    run_type          TEXT NOT NULL CHECK (run_type IN ('intraday_snapshot','eod_backfill','retention_cleanup')),
    caliber           TEXT NOT NULL CHECK (caliber IN ('eastmoney','ths')),
    sector_scope      TEXT,
    trade_date        TEXT NOT NULL,
    minute_slot       TEXT,
    started_at        TEXT NOT NULL,     -- UTC
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('success','partial','failed','interrupted')),
    sectors_ok        INTEGER NOT NULL DEFAULT 0,
    sectors_failed    INTEGER NOT NULL DEFAULT 0,
    target_interval_sec   INTEGER,
    achieved_interval_sec INTEGER,        -- 实际达成间隔(FR-005/015/SC-003)
    retry_count       INTEGER NOT NULL DEFAULT 0,
    error_type        TEXT,               -- 'rate_limited'|'empty_or_dash'|'schema_drift'|'timeout'|'anomaly_row_count'
    error_detail      TEXT,               -- JSON
    adapter_version   TEXT NOT NULL,      -- 数据版本固化到每次 run, 每行经 FK 可回溯产出世代
    code_version      TEXT,               -- git sha, 可选
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_run_date_type ON ingestion_run (trade_date, run_type, source_code);
CREATE INDEX idx_run_status ON ingestion_run (status) WHERE status != 'success';
```

### 5. `trade_calendar` — 交易日历（宪章 I 确定性）

```sql
CREATE TABLE trade_calendar (
    trade_date TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'
    is_open    INTEGER NOT NULL CHECK (is_open IN (0,1))
);
```

> 由 akshare `tool_trade_date_hist_sina()` 同步、缓存落库（确定性、可查）。保留窗口「N 交易日」cutoff、EOD 触发、交易时段判断全部以它为准。

### 6. `instrument` — 个股（本期建表留空，FR-018）

```sql
CREATE TABLE instrument (
    instrument_id INTEGER PRIMARY KEY,
    ts_code    TEXT NOT NULL UNIQUE,   -- '000001.SZ'
    symbol     TEXT NOT NULL,
    name       TEXT NOT NULL,
    exchange   TEXT NOT NULL CHECK (exchange IN ('SSE','SZSE','BSE')),
    list_date  TEXT, delist_date TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TEXT NOT NULL
);
```

### 7. `sector_constituent` — 板块-个股归属（本期建表留空，生效区间，宪章 II）

```sql
CREATE TABLE sector_constituent (
    constituent_id INTEGER PRIMARY KEY,
    sector_id      INTEGER NOT NULL REFERENCES sector(sector_id),
    instrument_id  INTEGER NOT NULL REFERENCES instrument(instrument_id),
    effective_from TEXT NOT NULL,    -- 'YYYY-MM-DD' 含
    effective_to   TEXT,             -- NULL=当前有效
    source_code    TEXT NOT NULL REFERENCES data_source(source_code),
    created_at     TEXT NOT NULL,
    UNIQUE (sector_id, instrument_id, effective_from)
);
CREATE INDEX idx_constituent_asof ON sector_constituent (sector_id, effective_from, effective_to);
```

> **As-of 查询（无前视，宪章 II）**：`WHERE sector_id=? AND effective_from<=:t AND (effective_to IS NULL OR effective_to>=:t)`。成分调整=关旧行开新行，不物理删除。锚定 `sector_id`（代理键），不用名称。

## 保留窗口

`RETENTION_TRADE_DAYS` 配置常量（本期 7，改 30 不动 schema）。清理 `DELETE FROM sector_money_flow WHERE trade_date < :cutoff`（cutoff 由 `trade_calendar` 按交易日算），作为 `run_type='retention_cleanup'` 落审计，随后周期 `wal_checkpoint(TRUNCATE)`。单表 165 万行/415MB SQLite 无需分区。

## 实体 → spec Key Entities 映射

| spec 实体 | 对应表 |
|---|---|
| 板块 (Sector) | `sector` |
| 板块资金流记录 (Sector Money Flow Record) | `sector_money_flow` |
| 采集运行记录 (Ingestion Run) | `ingestion_run` |
| 数据来源 (Data Source) | `data_source` |
| 个股 (Instrument) | `instrument`（留空） |
| 板块-个股归属关系 (Sector Constituent) | `sector_constituent`（留空） |
| （新增基础设施） | `trade_calendar` |
