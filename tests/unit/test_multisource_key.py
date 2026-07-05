"""spec004 Phase 1: source_code 进观测业务键——同键不同源两行共存（不覆盖）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

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
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol="600150", display_name="中国船舶", exchange="SSE")
    return sid


def _run(conn, source_code: str) -> int:
    return RunDao(conn).start(
        source_code=source_code, run_type=RunType.EOD_BACKFILL, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-04", minute_slot="EOD", adapter_version="t",
        subject_scope="x", asset_class_code="a_share")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_same_key_different_source_coexist(conn) -> None:
    """同 subject/date/value_type/slot，baostock 价 + sina_flow 流 → 两行共存。"""
    sid = _stock(conn)
    now = _now()
    r_bao = _run(conn, "baostock")
    r_sina = _run(conn, "sina_flow")
    dao = ObservationDao(conn)
    # baostock: 价格行（无资金流）
    id_bao = dao.upsert(
        subject_id=sid, source_code="baostock", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        price_micro=8_380_000, source_unit="yuan", ingestion_run_id=r_bao, created_at=now)
    # sina_flow: 资金流五档行（同键，不同源）
    id_sina = dao.upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        net_amount_cents=-100, five_tier={
            "main_net_cents": -100, "super_large_net_cents": -60, "large_net_cents": -40,
            "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=r_sina, created_at=now)
    # 两个不同 observation_id
    assert id_bao != id_sina
    # 两行都在，各自数据完整（不覆盖）
    n = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE subject_id=? AND trade_date='2026-07-03'",
        (sid,)).fetchone()[0]
    assert n == 2
    bao = conn.execute("SELECT price_micro, main_net_cents FROM observation WHERE observation_id=?",
                       (id_bao,)).fetchone()
    sina = conn.execute("SELECT price_micro, main_net_cents FROM observation WHERE observation_id=?",
                        (id_sina,)).fetchone()
    assert bao["price_micro"] == 8_380_000 and bao["main_net_cents"] is None   # baostock 只有价
    assert sina["main_net_cents"] == -100 and sina["price_micro"] is None       # sina 只有流


def test_same_source_same_key_updates(conn) -> None:
    """同源同键重写 → 幂等 UPDATE（仍一行）。"""
    sid = _stock(conn)
    now = _now()
    rid = _run(conn, "sina_flow")
    dao = ObservationDao(conn)
    id1 = dao.upsert(subject_id=sid, source_code="sina_flow", trade_date="2026-07-03",
                     minute_slot="EOD", value_type=ValueType.DAILY_FINAL, granularity="daily",
                     observed_at=now, net_amount_cents=-100, source_unit="yuan",
                     ingestion_run_id=rid, created_at=now)
    id2 = dao.upsert(subject_id=sid, source_code="sina_flow", trade_date="2026-07-03",
                     minute_slot="EOD", value_type=ValueType.DAILY_FINAL, granularity="daily",
                     observed_at=now, net_amount_cents=-200, source_unit="yuan",
                     ingestion_run_id=rid, created_at=now)
    assert id1 == id2                              # 同源同键 → 同行
    v = conn.execute("SELECT net_amount_cents FROM observation WHERE observation_id=?",
                     (id1,)).fetchone()[0]
    assert v == -200                               # 幂等覆盖为最新
