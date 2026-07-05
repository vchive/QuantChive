"""spec004 Phase 2: series_daily 逐日源解析——按指标取权威源、无重复日点。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.metric_source import (
    metric_nonnull_column,
    resolve_metric_sources,
)
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


def _stock(conn) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol="600150", display_name="中国船舶", exchange="SSE")
    return sid


def _run(conn, sc):
    return RunDao(conn).start(source_code=sc, run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-04",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _seed(conn, sid) -> None:
    """同 3 天，baostock 写价、sina_flow 写流。"""
    now = datetime.now(timezone.utc).isoformat()
    dao = ObservationDao(conn)
    r_bao, r_sina = _run(conn, "baostock"), _run(conn, "sina_flow")
    for i, d in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"]):
        dao.upsert(subject_id=sid, source_code="baostock", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   price_micro=8_000_000 + i * 100_000, source_unit="yuan",
                   ingestion_run_id=r_bao, created_at=now)
        dao.upsert(subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   net_amount_cents=-(100 + i), five_tier={
                       "main_net_cents": -(100 + i), "super_large_net_cents": -60,
                       "large_net_cents": -40, "medium_net_cents": 0, "small_net_cents": 0},
                   source_unit="yuan", ingestion_run_id=r_sina, created_at=now)


def test_main_net_resolves_to_sina(conn) -> None:
    """main_net 指标 → 逐日取 sina 行（价的 baostock 行被跳过），无重复日点。"""
    sid = _stock(conn)
    _seed(conn, sid)
    rows = ObservationDao(conn).series_daily(
        subject_id=sid, days=30, sources=resolve_metric_sources(SortField.MAIN_NET),
        nonnull_column=metric_nonnull_column(SortField.MAIN_NET))
    assert len(rows) == 3                          # 3 天，非 6（不重复）
    assert [r.trade_date for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    for r in rows:
        assert r.source_code == "sina_flow"        # main_net 权威源
        assert r.main_net_cents is not None


def test_price_resolves_to_baostock(conn) -> None:
    """price 指标 → 逐日取 baostock 行（流的 sina 行被跳过）。"""
    sid = _stock(conn)
    _seed(conn, sid)
    rows = ObservationDao(conn).series_daily(
        subject_id=sid, days=30, sources=resolve_metric_sources(SortField.PRICE),
        nonnull_column=metric_nonnull_column(SortField.PRICE))
    assert len(rows) == 3
    for r in rows:
        assert r.source_code == "baostock"         # price 权威源
        assert r.price_micro is not None


def test_fallback_when_primary_missing(conn) -> None:
    """price 首选 baostock 缺某日 → 回落次优源（sina 有价）。"""
    sid = _stock(conn)
    now = datetime.now(timezone.utc).isoformat()
    dao = ObservationDao(conn)
    r_sina = _run(conn, "sina_flow")
    # 只有 sina 有价（无 baostock）
    dao.upsert(subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
               value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
               price_micro=8_500_000, net_amount_cents=-100,
               five_tier={"main_net_cents": -100, "super_large_net_cents": -60,
                          "large_net_cents": -40, "medium_net_cents": 0, "small_net_cents": 0},
               source_unit="yuan", ingestion_run_id=r_sina, created_at=now)
    rows = dao.series_daily(subject_id=sid, days=30,
                            sources=resolve_metric_sources(SortField.PRICE),
                            nonnull_column=metric_nonnull_column(SortField.PRICE))
    assert len(rows) == 1 and rows[0].source_code == "sina_flow"   # 回落到 sina 的价
    assert rows[0].price_micro == 8_500_000
