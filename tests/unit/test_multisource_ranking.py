"""spec004 Phase 3: 多源不重复——大盘求和/排行按源限定，一股多源不双计。"""

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
    SortField,
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


def _stock(conn, code, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol=code, display_name=name, exchange="SSE")
    return sid


def _run(conn, sc):
    return RunDao(conn).start(source_code=sc, run_type=RunType.INTRADAY_SNAPSHOT,
                              caliber=Caliber.EASTMONEY,
                              trade_date="2026-07-03", minute_slot="LATEST", adapter_version="t",
                              subject_scope="x", asset_class_code="a_share")


def _latest(conn, sid, sc, main, run):
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code=sc, trade_date="2026-07-03", minute_slot="LATEST",
        value_type=ValueType.INTRADAY_LATEST, granularity="5min", observed_at=now,
        net_amount_cents=main, five_tier={
            "main_net_cents": main, "super_large_net_cents": main, "large_net_cents": 0,
            "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=run, created_at=now)


def test_stock_rows_for_scoped_excludes_other_source(conn) -> None:
    """一股同 LATEST 有 eastmoney + 另一源两行 → stock_rows_for(eastmoney) 只返 1 行。"""
    sid = _stock(conn, "600000", "浦发")
    r_em, r_other = _run(conn, "eastmoney"), _run(conn, "sina_flow")
    _latest(conn, sid, "eastmoney", 100, r_em)
    _latest(conn, sid, "sina_flow", 999, r_other)     # 另一源同键
    dao = ObservationDao(conn)
    rows_all = dao.stock_rows_for(trade_date="2026-07-03",
                                  value_type=ValueType.INTRADAY_LATEST, minute_slot="LATEST")
    rows_em = dao.stock_rows_for(trade_date="2026-07-03", value_type=ValueType.INTRADAY_LATEST,
                                 minute_slot="LATEST", source_code="eastmoney")
    assert len(rows_all) == 2                          # 无源过滤 → 两行（会双计）
    assert len(rows_em) == 1 and rows_em[0].source_code == "eastmoney"   # 限定 → 一行


def test_ranking_scoped_no_duplicate(conn) -> None:
    """排行按源限定 → 一股多源不重复排入榜。"""
    s1 = _stock(conn, "600000", "浦发")
    s2 = _stock(conn, "600519", "茅台")
    r_em, r_other = _run(conn, "eastmoney"), _run(conn, "sina_flow")
    _latest(conn, s1, "eastmoney", 100, r_em)
    _latest(conn, s1, "sina_flow", 999, r_other)      # s1 双源
    _latest(conn, s2, "eastmoney", 200, r_em)
    dao = ObservationDao(conn)
    ranked = dao.ranking_snapshot(
        subject_ids=[s1, s2], trade_date="2026-07-03", value_type=ValueType.INTRADAY_LATEST,
        minute_slot="LATEST", sort_by=SortField.MAIN_NET, top_n=10, source_code="eastmoney")
    ids = [r.subject_id for r in ranked]
    assert len(ids) == 2 and len(set(ids)) == 2       # 每股一次，无重复
    assert all(r.source_code == "eastmoney" for r in ranked)
