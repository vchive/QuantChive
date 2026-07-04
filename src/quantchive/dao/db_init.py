"""Schema 初始化（幂等建表）—— T012 + spec002 扩展。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")

# 数据源静态声明（research.md D1/D5 + spec002 D8 + spec003 D4/D6）
_SEED_SOURCES = [
    # source_code, display_name, caliber, supports_five_tier, has_daily_final, amount_unit, adapter_impl, is_active
    ("eastmoney", "东方财富", "eastmoney", 1, 1, "yuan", "em_sector_src.EastMoneySource", 1),
    ("ths", "同花顺", "ths", 0, 0, "yi", "ths_src.ThsSource", 1),
    ("em_fund", "东财基金", "eastmoney", 0, 0, "yuan", "fund_src.FundSource", 1),
    # spec003：baostock 历史行情源（无资金流）；ths_flow 同花顺资金流备源（初始停用，验 hexin-v 后启用）
    ("baostock", "baostock 历史行情", "baostock", 0, 1, "yuan", "baostock_src.BaostockSource", 1),
    ("ths_flow", "同花顺资金流", "ths", 1, 0, "yuan", "ths_flow_src.ThsFlowSource", 0),
    # 新浪个股历史资金流（独立于东财，五档齐全，约8年深，实测可用）
    ("sina_flow", "新浪资金流历史", "sina", 1, 1, "yuan", "sina_flow_src.SinaFlowSource", 1),
]

# spec002 品种（预留位）
_SEED_ASSET_CLASSES = [
    # code, display, is_populated, sort_order
    ("a_share", "A股", 1, 10),
    ("fund_etf", "基金/ETF", 1, 20),
    ("bond", "债券", 0, 30),
    ("futures", "期货", 0, 40),
    ("commodity", "商品", 0, 50),
    ("fx", "外汇", 0, 60),
]

# spec002 指标定义（data-model metric_def seed；scale 与 core/money 一致）
_SEED_METRICS = [
    # metric_name, display, value_kind, unit_label, scale_factor, storage, fixed_column, sortable
    ("main_net", "主力净额", "money_cents", "元", 100, "fixed_column", "main_net_cents", 1),
    ("super_large_net", "超大单净额", "money_cents", "元", 100, "fixed_column", "super_large_net_cents", 1),
    ("large_net", "大单净额", "money_cents", "元", 100, "fixed_column", "large_net_cents", 1),
    ("medium_net", "中单净额", "money_cents", "元", 100, "fixed_column", "medium_net_cents", 1),
    ("small_net", "小单净额", "money_cents", "元", 100, "fixed_column", "small_net_cents", 1),
    ("price", "现价", "price_micro", "元", 1_000_000, "fixed_column", "price_micro", 0),
    ("change_pct", "涨跌幅", "percent_bp", "%", 100, "fixed_column", "change_pct_bp", 1),
    ("turnover", "成交额", "money_cents", "元", 100, "fixed_column", "turnover_cents", 0),
    ("turnover_pct", "换手率", "percent_bp", "%", 100, "fixed_column", "turnover_pct_bp", 0),
    ("volume", "成交量", "count", "手", 1, "fixed_column", "volume", 0),
    ("circ_mktcap", "流通市值", "money_cents", "元", 100, "metric_kv", None, 0),
    ("nav", "基金净值", "nav_micro", "元", 1_000_000, "metric_kv", None, 0),
]

# spec002 指标适用性（metric_name, asset_class, subject_kind=NULL 表该品种全体）
_SEED_APPLICABILITY = [
    # A股五档资金流：板块 + 个股 + 大盘(求和) 都有 → subject_kind=None（全体）
    ("main_net", "a_share", None), ("super_large_net", "a_share", None),
    ("large_net", "a_share", None), ("medium_net", "a_share", None),
    ("small_net", "a_share", None),
    # A股价/量/涨跌/换手：**仅个股**采集（em_stock_src f2/f3/f5/f6）——板块源(em_sector_src)
    # 与大盘(求和)都不采。必须限定 subject_kind='stock'，否则板块"继承"这些指标 →
    # 按 change_pct 排序返回空榜而非诚实拒绝(422)，违反 FR-004 能力自描述。
    ("price", "a_share", "stock"), ("change_pct", "a_share", "stock"),
    ("turnover", "a_share", "stock"), ("turnover_pct", "a_share", "stock"),
    ("volume", "a_share", "stock"),
    # 基金/ETF 只有价量 + 规模/净值(无五档)
    ("price", "fund_etf", None), ("change_pct", "fund_etf", None),
    ("volume", "fund_etf", None), ("turnover", "fund_etf", None),
    ("circ_mktcap", "fund_etf", "etf"), ("nav", "fund_etf", "open_fund"),
]

# spec002/003 扩展 ingestion_run 的列（幂等 ALTER；旧表 spec001 已有基础列）
_RUN_ALTER_COLS = [
    ("subjects_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("subjects_failed", "INTEGER NOT NULL DEFAULT 0"),
    ("asset_class_code", "TEXT"),
    ("aggregate_coverage", "TEXT"),
    # spec003：主备切换后实际取数的源 + 限流降级标记（审计，FR-019/SC-001）
    ("used_source_code", "TEXT"),
    ("degraded", "INTEGER NOT NULL DEFAULT 0"),
]


def apply_schema(conn: sqlite3.Connection) -> None:
    """执行 schema.sql（IF NOT EXISTS，可重复调用）。"""
    conn.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))


def _alter_ingestion_run(conn: sqlite3.Connection) -> None:
    """幂等给 ingestion_run 加 spec002 列（SQLite ALTER 不幂等，先查列）。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(ingestion_run)").fetchall()}
    for col, decl in _RUN_ALTER_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE ingestion_run ADD COLUMN {col} {decl}")


_INGESTION_RUN_RELAXED_DDL = """
CREATE TABLE ingestion_run (
    run_id            INTEGER PRIMARY KEY,
    source_code       TEXT NOT NULL REFERENCES data_source(source_code),
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
)
"""


def _relax_ingestion_run_check(conn: sqlite3.Connection) -> None:
    """迁移旧库 ingestion_run 的窄 run_type CHECK → 无表级枚举约束（校验上移 enum）。

    SQLite 无法 ALTER CHECK；仅当探测到旧窄约束时做保数据的表重建（12 步官方流程），
    保留全部旧行与列。新库 schema.sql 已不含该 CHECK，此函数对其无操作。迁移铁律：
    不丢数据、约束放宽为超集、绝不 drop 有效行。重建后 _alter_ingestion_run 会补回
    spec002 列。用直写 CREATE TABLE（非 executescript）确保异常时 ROLLBACK 真正回滚。
    """
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ingestion_run'"
    ).fetchone()
    if not ddl_row or not ddl_row[0]:
        return
    ddl = ddl_row[0]
    if "CHECK (run_type IN" not in ddl and "CHECK(run_type IN" not in ddl:
        return  # 已是宽松版（新库或已迁移），无操作

    # 前次崩溃可能残留旧表——存在即中止，交人工核对，绝不覆盖有效数据
    leftover = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_ingestion_run_old'"
    ).fetchone()
    if leftover:
        raise RuntimeError(
            "_ingestion_run_old 残留（疑似前次迁移中断），拒绝自动重建以防覆盖；请人工核对"
        )

    cols = [r[1] for r in conn.execute("PRAGMA table_info(ingestion_run)").fetchall()]
    fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    # PRAGMA foreign_keys 在事务内被忽略——先落地任何隐式事务，再于自动提交态切换
    conn.commit()
    prev_isolation = conn.isolation_level
    conn.isolation_level = None  # 自动提交，使 PRAGMA 生效
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON：RENAME 纯改名，不重写子表 FK 引用（防子表 FK 指向被删旧表）
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE ingestion_run RENAME TO _ingestion_run_old")
        conn.execute(_INGESTION_RUN_RELAXED_DDL)  # 直写，不用 executescript（保 ROLLBACK 有效）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_date_type "
                     "ON ingestion_run (trade_date, run_type, source_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_run_status "
                     "ON ingestion_run (status) WHERE status != 'success'")
        new_cols = {r[1] for r in conn.execute("PRAGMA table_info(ingestion_run)").fetchall()}
        shared = [c for c in cols if c in new_cols]  # 取交集避免列不匹配
        col_list = ", ".join(shared)
        conn.execute(
            f"INSERT INTO ingestion_run ({col_list}) SELECT {col_list} FROM _ingestion_run_old"
        )
        conn.execute("DROP TABLE _ingestion_run_old")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
        conn.isolation_level = prev_isolation


_DATA_SOURCE_RELAXED_DDL = """
CREATE TABLE data_source (
    source_code        TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    caliber            TEXT NOT NULL,
    supports_five_tier INTEGER NOT NULL CHECK (supports_five_tier IN (0,1)),
    has_daily_final    INTEGER NOT NULL CHECK (has_daily_final IN (0,1)),
    amount_unit        TEXT NOT NULL CHECK (amount_unit IN ('yuan','yi')),
    adapter_impl       TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at         TEXT NOT NULL
)
"""


def _relax_data_source_check(conn: sqlite3.Connection) -> None:
    """迁移旧库 data_source 的窄 caliber CHECK → 无枚举约束（spec003 新增 baostock 等源）。

    与 _relax_ingestion_run_check 同法（12 步保数据重建）。data_source 被多表 source_code FK
    引用——PK 不变、行全保留，FK 完整性不破。新库 schema.sql 已无该 CHECK，对其无操作。
    """
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='data_source'"
    ).fetchone()
    if not ddl_row or not ddl_row[0]:
        return
    if "CHECK (caliber IN" not in ddl_row[0] and "CHECK(caliber IN" not in ddl_row[0]:
        return  # 已宽松（新库/已迁移）
    leftover = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_data_source_old'"
    ).fetchone()
    if leftover:
        raise RuntimeError("_data_source_old 残留（疑似前次迁移中断），拒绝自动重建；请人工核对")

    cols = [r[1] for r in conn.execute("PRAGMA table_info(data_source)").fetchall()]
    fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.commit()
    prev_isolation = conn.isolation_level
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = OFF")
    # legacy_alter_table=ON：RENAME 纯改名，不重写子表 FK 引用（否则子表 FK 会指向被删的旧表）
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE data_source RENAME TO _data_source_old")
        conn.execute(_DATA_SOURCE_RELAXED_DDL)
        col_list = ", ".join(cols)
        conn.execute(
            f"INSERT INTO data_source ({col_list}) SELECT {col_list} FROM _data_source_old")
        conn.execute("DROP TABLE _data_source_old")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
        conn.isolation_level = prev_isolation


def seed_data_sources(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT INTO data_source
            (source_code, display_name, caliber, supports_five_tier,
             has_daily_final, amount_unit, adapter_impl, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_code) DO UPDATE SET
            display_name=excluded.display_name, supports_five_tier=excluded.supports_five_tier,
            has_daily_final=excluded.has_daily_final, amount_unit=excluded.amount_unit,
            adapter_impl=excluded.adapter_impl""",
        # 注：is_active 不进 DO UPDATE——保留运行期切换（如 ths_flow 验证 hexin-v 后启用不被 seed 重置）
        [(*row, now) for row in _SEED_SOURCES],
    )


def seed_spec002(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT INTO asset_class (asset_class_code, display_name, is_populated, sort_order, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(asset_class_code) DO UPDATE SET
             display_name=excluded.display_name, is_populated=excluded.is_populated,
             sort_order=excluded.sort_order""",
        [(*row, now) for row in _SEED_ASSET_CLASSES],
    )
    conn.executemany(
        """INSERT INTO metric_def
            (metric_name, display_name, value_kind, unit_label, scale_factor, storage, fixed_column, is_sortable, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(metric_name) DO UPDATE SET
             display_name=excluded.display_name, value_kind=excluded.value_kind,
             scale_factor=excluded.scale_factor, storage=excluded.storage,
             fixed_column=excluded.fixed_column, is_sortable=excluded.is_sortable""",
        [(*row, now) for row in _SEED_METRICS],
    )
    # metric_applicability 是能力真源（诚实自描述）——设为权威表：每次 init_db 全量重置，
    # 使旧库随代码修正自愈（如 stock-only 指标误挂板块的旧行会被清掉）。此表纯静态无外部写入。
    conn.execute("DELETE FROM metric_applicability")
    conn.executemany(
        """INSERT INTO metric_applicability (metric_name, asset_class_code, subject_kind)
           VALUES (?,?,?)""",
        _SEED_APPLICABILITY,
    )


def init_db(conn: sqlite3.Connection) -> None:
    apply_schema(conn)
    _relax_ingestion_run_check(conn)  # 旧库放宽 run_type CHECK（保数据表重建）
    _relax_data_source_check(conn)    # 旧库放宽 caliber CHECK（spec003 新源 baostock）
    _alter_ingestion_run(conn)
    seed_data_sources(conn)
    seed_spec002(conn)
