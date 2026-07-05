"""T008: spec003 schema——coverage_range 表 + ingestion_run 新列 + 新源 seed。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.db_init import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_new_sources_seeded(conn) -> None:
    srcs = {r["source_code"]: r["is_active"] for r in
            conn.execute("SELECT source_code, is_active FROM data_source")}
    assert srcs["baostock"] == 1                    # 历史行情源启用
    assert srcs["ths_flow"] == 0                    # 同花顺备源初始停用（验 hexin-v 后启用）


def test_data_source_caliber_check_relaxed(conn) -> None:
    # 新库 caliber 无硬枚举 CHECK——可插入非 eastmoney/ths 的 caliber
    conn.execute(
        """INSERT INTO data_source (source_code, display_name, caliber, supports_five_tier,
           has_daily_final, amount_unit, adapter_impl, created_at)
           VALUES ('x','X','some_new_caliber',0,0,'yuan','x',?)""", (_now(),))
    assert conn.execute("SELECT caliber FROM data_source WHERE source_code='x'").fetchone()[0] \
        == "some_new_caliber"


def test_ingestion_run_new_cols(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ingestion_run)")}
    assert "used_source_code" in cols and "degraded" in cols


def test_coverage_range_unique(conn) -> None:
    sid = conn.execute(
        """INSERT INTO subject (asset_class_code, level, subject_kind, source_code,
           source_symbol, display_name, caliber, first_seen_at)
           VALUES ('a_share','instrument','stock','eastmoney','600519','茅台','eastmoney',?)""",
        (_now(),)).lastrowid
    ins = ("""INSERT INTO coverage_range (subject_id, metric_kind, granularity, source_code,
              start_date, end_date, updated_at) VALUES (?,?,?,?,?,?,?)""")
    conn.execute(ins, (sid, "price_hist", "daily", "baostock", "2026-06-01", "2026-07-03", _now()))
    # 同 (subject, metric_kind, granularity, source) 再插 → 违反 UNIQUE
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (sid, "price_hist", "daily", "baostock", "2026-05-01", "2026-06-30", _now()))
