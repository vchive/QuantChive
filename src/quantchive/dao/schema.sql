-- QuantChive 通用市场观测平台 schema (data-model.md)
-- 金额 INTEGER 分、价格 ×1e6 微元、百分比 ×100 基点、净值 ×1e6；时间见各列注释。

PRAGMA foreign_keys = ON;

-- 1. 数据来源抽象
CREATE TABLE IF NOT EXISTS data_source (
    source_code        TEXT PRIMARY KEY,              -- 'eastmoney'|'ths'|'baostock'|'ths_flow'...
    display_name       TEXT NOT NULL,
    -- caliber 校验上移 enum（spec003：新增 baostock 等源，去硬枚举 CHECK 与 ingestion_run 一致）
    caliber            TEXT NOT NULL,
    supports_five_tier INTEGER NOT NULL CHECK (supports_five_tier IN (0,1)),
    has_daily_final    INTEGER NOT NULL CHECK (has_daily_final IN (0,1)),
    amount_unit        TEXT NOT NULL CHECK (amount_unit IN ('yuan','yi')),
    adapter_impl       TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at         TEXT NOT NULL
);

-- 采集运行审计（宪章 V）—— 先于 observation 建（FK 依赖）
CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id            INTEGER PRIMARY KEY,
    source_code       TEXT NOT NULL REFERENCES data_source(source_code),
    -- run_type/caliber 校验上移 Pydantic/enum（data-model.md：去硬枚举 CHECK，
    -- spec002 扩展 market_aggregate/retention_downsample 等新类型不再受表约束限制）
    run_type          TEXT NOT NULL,
    caliber           TEXT NOT NULL,
    sector_scope      TEXT,
    trade_date        TEXT NOT NULL,
    minute_slot       TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('success','partial','failed','interrupted')),
    sectors_ok        INTEGER NOT NULL DEFAULT 0,
    sectors_failed    INTEGER NOT NULL DEFAULT 0,
    target_interval_sec   INTEGER,
    achieved_interval_sec INTEGER,
    retry_count       INTEGER NOT NULL DEFAULT 0,
    error_type        TEXT,
    error_detail      TEXT,
    adapter_version   TEXT NOT NULL,
    code_version      TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_date_type ON ingestion_run (trade_date, run_type, source_code);
CREATE INDEX IF NOT EXISTS idx_run_status ON ingestion_run (status) WHERE status != 'success';

-- 交易日历（宪章 I 确定性）
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date TEXT PRIMARY KEY,
    is_open    INTEGER NOT NULL CHECK (is_open IN (0,1))
);

-- ============================================================================
-- 通用市场观测平台核心表（主体 × 指标 × 时序，data-model.md）
-- ============================================================================

-- v2.1 品种（预留位）
CREATE TABLE IF NOT EXISTS asset_class (
    asset_class_code TEXT PRIMARY KEY,   -- 'a_share'|'fund_etf'|'bond'|'futures'|'commodity'|'fx'
    display_name     TEXT NOT NULL,
    is_populated     INTEGER NOT NULL DEFAULT 0 CHECK (is_populated IN (0,1)),
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

-- v2.3 主体（取代 instrument，统一大盘/板块/个股/ETF/基金）
CREATE TABLE IF NOT EXISTS subject (
    subject_id       INTEGER PRIMARY KEY,
    asset_class_code TEXT NOT NULL REFERENCES asset_class(asset_class_code),
    level            TEXT NOT NULL CHECK (level IN ('market','sector','instrument')),
    subject_kind     TEXT NOT NULL CHECK (subject_kind IN
                       ('market_total','industry','concept','region','stock','etf','open_fund')),
    source_code      TEXT NOT NULL REFERENCES data_source(source_code),
    source_symbol    TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    caliber          TEXT NOT NULL,
    exchange         TEXT,
    em_board_code    TEXT,
    collect_tier     TEXT NOT NULL DEFAULT 'daily'
                     CHECK (collect_tier IN ('minute','coarse','derived','on_demand','daily')),
    is_hot           INTEGER NOT NULL DEFAULT 0 CHECK (is_hot IN (0,1)),
    list_date TEXT, delist_date TEXT,
    first_seen_at    TEXT NOT NULL, last_seen_at TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    UNIQUE (asset_class_code, subject_kind, source_code, source_symbol)
);
CREATE INDEX IF NOT EXISTS idx_subject_level_kind ON subject (asset_class_code, level, subject_kind) WHERE is_active=1;
CREATE INDEX IF NOT EXISTS idx_subject_symbol ON subject (source_symbol);

-- v2.4 主体归属（as-of 无前视，泛化 sector_constituent）
CREATE TABLE IF NOT EXISTS subject_membership (
    membership_id     INTEGER PRIMARY KEY,
    parent_subject_id INTEGER NOT NULL REFERENCES subject(subject_id),
    child_subject_id  INTEGER NOT NULL REFERENCES subject(subject_id),
    relation_kind     TEXT NOT NULL DEFAULT 'sector_constituent'
                      CHECK (relation_kind IN ('sector_constituent','index_constituent','fund_holding')),
    effective_from    TEXT NOT NULL,
    effective_to      TEXT,
    source_code       TEXT NOT NULL REFERENCES data_source(source_code),
    ingestion_run_id  INTEGER REFERENCES ingestion_run(run_id),
    created_at        TEXT NOT NULL,
    UNIQUE (parent_subject_id, child_subject_id, relation_kind, effective_from)
);
CREATE INDEX IF NOT EXISTS idx_membership_asof ON subject_membership (parent_subject_id, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_membership_child ON subject_membership (child_subject_id, effective_from);

-- v2.5 指标定义 + 适用性（能力自描述）
CREATE TABLE IF NOT EXISTS metric_def (
    metric_name  TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    value_kind   TEXT NOT NULL CHECK (value_kind IN ('money_cents','price_micro','percent_bp','count','nav_micro')),
    unit_label   TEXT NOT NULL,
    scale_factor INTEGER NOT NULL,
    storage      TEXT NOT NULL CHECK (storage IN ('fixed_column','metric_kv')),
    fixed_column TEXT,
    is_sortable  INTEGER NOT NULL DEFAULT 0 CHECK (is_sortable IN (0,1)),
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_applicability (
    metric_name      TEXT NOT NULL REFERENCES metric_def(metric_name),
    asset_class_code TEXT NOT NULL REFERENCES asset_class(asset_class_code),
    subject_kind     TEXT,
    PRIMARY KEY (metric_name, asset_class_code, subject_kind)
);

-- v2.6 观测记录（核心，泛化 sector_money_flow）
CREATE TABLE IF NOT EXISTS observation (
    observation_id   INTEGER PRIMARY KEY,
    subject_id       INTEGER NOT NULL REFERENCES subject(subject_id),
    source_code      TEXT NOT NULL REFERENCES data_source(source_code),
    trade_date       TEXT NOT NULL,
    minute_slot      TEXT NOT NULL,             -- 'HH:MM' | 'LATEST' | 'EOD' | 'HH:00'
    value_type       TEXT NOT NULL CHECK (value_type IN
                       ('intraday_snapshot','intraday_latest','daily_final','hourly_rollup')),
    granularity      TEXT NOT NULL CHECK (granularity IN ('1min','5min','hourly','daily')),
    observed_at      TEXT NOT NULL,
    net_amount_cents INTEGER,
    main_net_cents INTEGER, super_large_net_cents INTEGER, large_net_cents INTEGER,
    medium_net_cents INTEGER, small_net_cents INTEGER,
    inflow_cents INTEGER, outflow_cents INTEGER,
    price_micro INTEGER,
    change_pct_bp INTEGER,
    volume INTEGER,
    turnover_cents INTEGER,
    turnover_pct_bp INTEGER,
    constituent_count INTEGER, expected_count INTEGER,
    source_unit TEXT NOT NULL CHECK (source_unit IN ('yuan','yi')),
    raw_value TEXT,
    is_derived INTEGER NOT NULL DEFAULT 0 CHECK (is_derived IN (0,1)),
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_run(run_id),
    created_at TEXT NOT NULL,
    CHECK ((main_net_cents IS NULL AND super_large_net_cents IS NULL AND large_net_cents IS NULL
            AND medium_net_cents IS NULL AND small_net_cents IS NULL)
        OR (main_net_cents IS NOT NULL AND super_large_net_cents IS NOT NULL AND large_net_cents IS NOT NULL
            AND medium_net_cents IS NOT NULL AND small_net_cents IS NOT NULL)),
    CHECK (main_net_cents IS NOT NULL OR price_micro IS NOT NULL OR volume IS NOT NULL
           OR turnover_cents IS NOT NULL OR net_amount_cents IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_observation ON observation (subject_id, trade_date, value_type, minute_slot);
CREATE INDEX IF NOT EXISTS idx_obs_ranking ON observation (trade_date, value_type, minute_slot, subject_id);
CREATE INDEX IF NOT EXISTS idx_obs_trade_date ON observation (trade_date);

-- v2.7 稀疏指标附表
CREATE TABLE IF NOT EXISTS observation_metric (
    obs_metric_id  INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL REFERENCES observation(observation_id) ON DELETE CASCADE,
    metric_name    TEXT NOT NULL REFERENCES metric_def(metric_name),
    value_int      INTEGER NOT NULL,
    UNIQUE (observation_id, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_obsmetric_name ON observation_metric (metric_name, observation_id);

-- ============================================================================
-- spec003 数据层韧性：已存区间表（增量回填/缺口检测，仿 vnpy BarOverview，data-model D5）
-- ============================================================================

-- v3.1 主体×指标×粒度×源 的已存连续区间（回填前查缺口、回填后合并区间）
CREATE TABLE IF NOT EXISTS coverage_range (
    coverage_id   INTEGER PRIMARY KEY,
    subject_id    INTEGER NOT NULL REFERENCES subject(subject_id),
    metric_kind   TEXT NOT NULL,          -- 'money_flow' | 'price_hist'（回填历史类别；realtime 不入本表）
    granularity   TEXT NOT NULL,          -- 'daily' | '1min' | '5min'
    source_code   TEXT NOT NULL REFERENCES data_source(source_code),
    start_date    TEXT NOT NULL,          -- 已存最早交易日（含）
    end_date      TEXT NOT NULL,          -- 已存最晚交易日（含）
    updated_at    TEXT NOT NULL,
    UNIQUE (subject_id, metric_kind, granularity, source_code)
);
CREATE INDEX IF NOT EXISTS idx_coverage_lookup
    ON coverage_range (subject_id, metric_kind, granularity);
