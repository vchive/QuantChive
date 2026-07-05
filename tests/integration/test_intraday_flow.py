"""T021: 盘中逐分钟净额归档 + tiers_series intraday 分支（spec005 US3，无 gross）。"""

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
from quantchive.service.flow_query_service import FlowQueryService


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


def _run(conn):
    return RunDao(conn).start(source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-06",
                              minute_slot="09:31", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _minute(conn, sid, rid, slot, main):
    """盘中一分钟：只有净额（无 gross，东财实时不发 gross）。"""
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date="2026-07-06", minute_slot=slot,
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=now,
        five_tier={"main_net_cents": main, "super_large_net_cents": main,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        price_micro=8_000_000, ingestion_run_id=rid, source_unit="yuan", created_at=now)


def test_intraday_series_net_only(conn) -> None:
    """盘中分钟序列 → has_gross=False、只有净额、按 minute_slot 升序。"""
    sid, rid = _stock(conn), _run(conn)
    _minute(conn, sid, rid, "09:31", 100)
    _minute(conn, sid, rid, "09:32", 200)
    _minute(conn, sid, rid, "09:33", -50)
    res = FlowQueryService(conn, retention_trade_days=90).get_tiers_series(
        subject_id=sid, granularity="intraday")
    assert res.has_gross is False                  # 盘中无 gross
    assert res.granularity == "intraday"
    assert len(res.points) == 3
    assert [p.ts for p in res.points] == ["09:31", "09:32", "09:33"]
    # 只有净额、流入流出为空（诚实缺失）
    p = res.points[0]
    assert p.super_large.net == "1.00" and p.super_large.inflow is None
    # 累计逐分钟推进
    assert [p.cum_main_net for p in res.points] == ["1.00", "3.00", "2.50"]


def test_intraday_no_data_raises(conn) -> None:
    sid = _stock(conn)
    from quantchive.service.errors import NoDataForDate
    with pytest.raises(NoDataForDate):
        FlowQueryService(conn, retention_trade_days=90).get_tiers_series(
            subject_id=sid, granularity="intraday")
