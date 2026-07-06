"""agent 地基 4 能力单测：全市场横截面扫描 / 日线区间取数 / 批量 / 防前视。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quantchive.core.trading_calendar import FixedClock
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
from quantchive.service.query_service import QueryService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    # 交易日历（排行/时序依赖）
    for d in ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]:
        c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    return c


def _clock(day="2026-07-03"):
    return FixedClock(datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), 15, 30, tzinfo=timezone.utc))


def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SSE")
    return sid


def _run(conn, rt=RunType.EOD_BACKFILL, src="sina_flow"):
    return RunDao(conn).start(source_code=src, run_type=rt, caliber=Caliber.EASTMONEY,
                              trade_date="2026-07-03", minute_slot="EOD", adapter_version="t",
                              subject_scope="x", asset_class_code="a_share")


def _daily(conn, sid, rid, date, main, src="sina_flow"):
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code=src, trade_date=date, minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": main, "super_large_net_cents": main,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at=now)


def _latest(conn, sid, rid, main, src="eastmoney"):
    """当日 intraday_latest（scan 当日榜读此）。"""
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code=src, trade_date="2026-07-03", minute_slot="LATEST",
        value_type=ValueType.INTRADAY_LATEST, granularity="5min", observed_at=now,
        net_amount_cents=main,
        five_tier={"main_net_cents": main, "super_large_net_cents": main,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at=now)


# ---------- 缺口1：全市场横截面扫描 ----------

def test_scan_market_stocks_bipolar_cross_industry(conn) -> None:
    """全市场按 main_net 取 TopN，跨行业统一榜，双榜按符号切。"""
    rid = _run(conn, rt=RunType.INTRADAY_SNAPSHOT, src="eastmoney")
    s1, s2, s3 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙"), _stock(conn, "600003", "丙")
    _latest(conn, s1, rid, 50000)    # +5亿 流入
    _latest(conn, s2, rid, 30000)    # +3亿
    _latest(conn, s3, rid, -20000)   # -2亿 流出
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:x", clock=_clock())
    res = svc.scan_market_stocks(sort_by=SortField.MAIN_NET, top_n=10)
    inflow_names = [it.display_name for it in res.top_inflow]
    outflow_names = [it.display_name for it in res.top_outflow]
    assert inflow_names == ["甲", "乙"]      # 流入降序
    assert outflow_names == ["丙"]           # 流出
    assert res.total_subjects == 3


def test_scan_market_stocks_topn_limit(conn) -> None:
    """top_n 限制生效（不返超过 top_n）。"""
    rid = _run(conn, rt=RunType.INTRADAY_SNAPSHOT, src="eastmoney")
    for i in range(5):
        s = _stock(conn, f"60010{i}", f"股{i}")
        _latest(conn, s, rid, (i + 1) * 10000)   # 全正
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:x", clock=_clock())
    res = svc.scan_market_stocks(sort_by=SortField.MAIN_NET, top_n=2)
    assert len(res.top_inflow) == 2
    assert res.top_inflow[0].display_name == "股4"   # 最大在前


# ---------- 缺口2：日线区间取数（绕保留窗）----------

def test_series_range_beyond_retention(conn) -> None:
    """区间取数不受 retention 限制：retention=2 天，但取 5 天区间应全返。"""
    rid = _run(conn)
    sid = _stock(conn, "600150", "中国船舶")
    dates = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]
    for i, d in enumerate(dates):
        _daily(conn, sid, rid, d, (i + 1) * 10000)
    svc = QueryService(conn, retention_trade_days=2, source_id="fake:x", clock=_clock())
    res = svc.get_subject_series_range(
        subject_id=sid, metric=SortField.MAIN_NET,
        start_date="2026-06-29", end_date="2026-07-03")
    assert len(res.points) == 5      # 全5天（retention=2 未截断）
    assert [p.ts for p in res.points] == dates
    assert res.points[0].value == Decimal("100.00")   # 10000分 = 100元


def test_series_range_no_lookahead(conn) -> None:
    """end_date 截断防前视：给 end_date=07-01，不返 >07-01 的点。"""
    rid = _run(conn)
    sid = _stock(conn, "600150", "中国船舶")
    for i, d in enumerate(["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]):
        _daily(conn, sid, rid, d, (i + 1) * 10000)
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:x", clock=_clock())
    res = svc.get_subject_series_range(
        subject_id=sid, metric=SortField.MAIN_NET,
        start_date="2026-06-29", end_date="2026-07-01")
    assert [p.ts for p in res.points] == ["2026-06-29", "2026-06-30", "2026-07-01"]
    assert all(p.ts <= "2026-07-01" for p in res.points)   # 无未来点


# ---------- 缺口3：多主体批量 ----------

def test_series_batch_multi_subject(conn) -> None:
    """一次取多主体日线区间，按 subject_id 分组。"""
    rid = _run(conn)
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    for i, d in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"]):
        _daily(conn, s1, rid, d, (i + 1) * 10000)
        _daily(conn, s2, rid, d, (i + 1) * 20000)
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:x", clock=_clock())
    res = svc.get_subjects_series_batch(
        subject_ids=[s1, s2], metric=SortField.MAIN_NET,
        start_date="2026-07-01", end_date="2026-07-03")
    assert len(res.series) == 2
    by = {s.subject_id: s for s in res.series}
    assert len(by[s1].points) == 3 and len(by[s2].points) == 3
    assert by[s1].display_name == "甲" and by[s2].display_name == "乙"


def test_series_batch_empty_subject_skipped(conn) -> None:
    """区间内无数据的主体跳过，不造假空点。"""
    rid = _run(conn)
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    _daily(conn, s1, rid, "2026-07-02", 10000)   # 只 s1 有
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:x", clock=_clock())
    res = svc.get_subjects_series_batch(
        subject_ids=[s1, s2], metric=SortField.MAIN_NET,
        start_date="2026-07-01", end_date="2026-07-03")
    assert len(res.series) == 1 and res.series[0].subject_id == s1
