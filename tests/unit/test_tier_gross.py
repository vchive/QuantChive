"""T007: spec005 gross 列 + 流入流出派生 + CHECK。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.core.money import tier_inflow, tier_outflow
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass,
    Caliber,
    RunType,
    SubjectKind,
    SubjectLevel,
    ValueType,
)


def test_tier_inflow_outflow_integer() -> None:
    """流入=(gross+net)//2、流出=(gross-net)//2，恒整数、满足恒等式。"""
    # 真实约束：gross=流入+流出、net=流入-流出 → gross+net=2×流入必偶
    inflow_true, outflow_true = 9000531900, 13105591200
    gross = inflow_true + outflow_true
    net = inflow_true - outflow_true
    inflow, outflow = tier_inflow(gross, net), tier_outflow(gross, net)
    assert inflow == inflow_true and outflow == outflow_true
    assert inflow - outflow == net           # 流入-流出==净额
    assert inflow + outflow == gross         # 流入+流出==成交额
    assert isinstance(inflow, int) and isinstance(outflow, int)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _stock(conn) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol="600150", display_name="中国船舶", exchange="SSE")
    return sid


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-04",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def test_gross_columns_stored(conn) -> None:
    """写四档 gross → 存储、读回、算流入流出。"""
    sid = _stock(conn)
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": -4940620551, "super_large_net_cents": -4155059421,
                   "large_net_cents": -785561130, "medium_net_cents": -4913246261,
                   "small_net_cents": -952832953},
        four_gross={"super_large_gross_cents": 22156123257, "large_gross_cents": 19870805682,
                    "medium_gross_cents": 13655552713, "small_gross_cents": 4403315787},
        source_unit="yuan", ingestion_run_id=_run(conn), created_at=now)
    row = conn.execute(
        """SELECT super_large_gross_cents, super_large_net_cents FROM observation
           WHERE subject_id=? AND source_code='sina_flow'""", (sid,)).fetchone()
    g, n = row["super_large_gross_cents"], row["super_large_net_cents"]
    assert g == 22156123257
    assert tier_inflow(g, n) == (g + n) // 2      # 超大单流入
    assert tier_outflow(g, n) == (g - n) // 2     # 超大单流出


def test_four_gross_all_or_none_check(conn) -> None:
    """四档 gross 全有或全无 CHECK：部分 gross 拒绝。"""
    sid = _stock(conn)
    now = datetime.now(timezone.utc).isoformat()
    rid = _run(conn)
    # 直接 INSERT 只给 1 档 gross（绕 upsert 的 dict）→ 违反 CHECK
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO observation (subject_id, source_code, trade_date, minute_slot,
               value_type, granularity, observed_at, super_large_gross_cents, main_net_cents,
               super_large_net_cents, large_net_cents, medium_net_cents, small_net_cents,
               source_unit, ingestion_run_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, "sina_flow", "2026-07-02", "EOD", "daily_final", "daily", now,
             100, -1, -1, -1, -1, -1, "yuan", rid, now))


def test_no_gross_source_null(conn) -> None:
    """无 gross 源（不传 four_gross）→ 四档 gross 全 NULL，流入流出不可推。"""
    sid = _stock(conn)
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-01", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        price_micro=8_000_000, source_unit="yuan", ingestion_run_id=_run(conn), created_at=now)
    row = conn.execute(
        "SELECT super_large_gross_cents FROM observation WHERE subject_id=? AND trade_date='2026-07-01'",
        (sid,)).fetchone()
    assert row["super_large_gross_cents"] is None
