"""T010: spec002 通用 schema 校验（observation 幂等/CHECK、metric_def、subject）。"""

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


def _mk_subject(conn: sqlite3.Connection, kind="industry", level="sector") -> int:
    cur = conn.execute(
        """INSERT INTO subject (asset_class_code, level, subject_kind, source_code,
           source_symbol, display_name, caliber, first_seen_at)
           VALUES ('a_share',?,?, 'eastmoney','X','X','eastmoney',?)""",
        (level, kind, _now()),
    )
    return cur.lastrowid


def _mk_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """INSERT INTO ingestion_run (source_code, run_type, caliber, trade_date,
           started_at, status, adapter_version, created_at)
           VALUES ('eastmoney','intraday_snapshot','eastmoney','2026-07-03',?,'success','v2',?)""",
        (_now(), _now()),
    )
    return cur.lastrowid


def test_asset_class_seeded_with_reserved() -> None:
    c = sqlite3.connect(":memory:", isolation_level=None)
    init_db(c)
    rows = dict(c.execute("SELECT asset_class_code, is_populated FROM asset_class").fetchall())
    assert rows["a_share"] == 1 and rows["fund_etf"] == 1
    assert rows["bond"] == 0 and rows["futures"] == 0  # 预留未填


def test_metric_def_scale_factors(conn: sqlite3.Connection) -> None:
    m = dict(conn.execute("SELECT metric_name, scale_factor FROM metric_def").fetchall())
    assert m["main_net"] == 100          # 元→分
    assert m["price"] == 1_000_000       # ×1e6 微元
    assert m["change_pct"] == 100        # 百分数→基点(2.35%→235)
    assert m["nav"] == 1_000_000


def test_observation_idempotent(conn: sqlite3.Connection) -> None:
    sid, rid = _mk_subject(conn), _mk_run(conn)
    ins = """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
        value_type, granularity, observed_at, main_net_cents, super_large_net_cents,
        large_net_cents, medium_net_cents, small_net_cents, source_unit, ingestion_run_id, created_at)
        VALUES (?, 'eastmoney','2026-07-03','10:30','intraday_snapshot','1min',?, 100,1,1,1,1,'yuan',?,?)"""
    conn.execute(ins, (sid, _now(), rid, _now()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (sid, _now(), rid, _now()))  # uq_observation


def test_five_tier_all_or_nothing(conn: sqlite3.Connection) -> None:
    sid, rid = _mk_subject(conn), _mk_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
               value_type, granularity, observed_at, main_net_cents, source_unit, ingestion_run_id, created_at)
               VALUES (?, 'eastmoney','2026-07-03','10:31','intraday_snapshot','1min',?, 5, 'yuan',?,?)""",
            (sid, _now(), rid, _now()),
        )


def test_at_least_one_metric_non_null(conn: sqlite3.Connection) -> None:
    # 空壳行(所有指标 NULL)被 CHECK 拒绝
    sid, rid = _mk_subject(conn), _mk_run(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
               value_type, granularity, observed_at, source_unit, ingestion_run_id, created_at)
               VALUES (?, 'eastmoney','2026-07-03','10:32','intraday_snapshot','1min',?, 'yuan',?,?)""",
            (sid, _now(), rid, _now()),
        )


def test_price_only_observation_ok(conn: sqlite3.Connection) -> None:
    # ETF: 只有价/量无五档 → 合法
    sid, rid = _mk_subject(conn, kind="etf", level="instrument"), _mk_run(conn)
    conn.execute(
        """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
           value_type, granularity, observed_at, price_micro, volume, source_unit, ingestion_run_id, created_at)
           VALUES (?, 'eastmoney','2026-07-03','LATEST','intraday_latest','5min',?, 1109000, 12345, 'yuan',?,?)""",
        (sid, _now(), rid, _now()),
    )
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 1


def test_observation_metric_cascade(conn: sqlite3.Connection) -> None:
    sid, rid = _mk_subject(conn, kind="etf", level="instrument"), _mk_run(conn)
    cur = conn.execute(
        """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
           value_type, granularity, observed_at, price_micro, source_unit, ingestion_run_id, created_at)
           VALUES (?, 'eastmoney','2026-07-03','LATEST','intraday_latest','5min',?, 1109000, 'yuan',?,?)""",
        (sid, _now(), rid, _now()),
    )
    oid = cur.lastrowid
    conn.execute(
        "INSERT INTO observation_metric (observation_id, metric_name, value_int) VALUES (?, 'circ_mktcap', 999)",
        (oid,),
    )
    conn.execute("DELETE FROM observation WHERE observation_id=?", (oid,))
    # ON DELETE CASCADE 带走稀疏指标
    assert conn.execute("SELECT COUNT(*) FROM observation_metric").fetchone()[0] == 0
