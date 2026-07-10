"""全市场基本面信号聚合统计:多股聚合、pooled基准、读表、幂等重算。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone, date as _date

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass, Caliber, RunType, SubjectKind, SubjectLevel, ValueType,
)
from quantchive.service.market_signal_service import (
    compute_market_fundamental_stats,
    read_market_stats,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _setup_stock(conn, sym, name, yoy_series, price_days=120):
    """造一只股:基本面净利同比序列 + 2025-03起连续日价(单调涨,change_pct_bp=50)。"""
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SZSE")
    rid = RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                             caliber=Caliber.EASTMONEY, trade_date="2025-01-01",
                             minute_slot="EOD", adapter_version="t", subject_scope="x",
                             asset_class_code="a_share")
    now = datetime.now(timezone.utc).isoformat()
    for period, yoy in yoy_series:
        conn.execute(
            """INSERT INTO fundamental_item(subject_id,report_period,announce_date,statement,
               item,value_int,unit,source_code,ingestion_run_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, period, None, "performance", "net_profit_yoy", yoy, "bp",
             "eastmoney_fin", rid, now))
    obs = ObservationDao(conn)
    base = _date(2025, 3, 3)
    for i in range(price_days):
        d = (base + timedelta(days=i)).isoformat()
        obs.upsert(subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   five_tier={"main_net_cents": 100, "super_large_net_cents": 0,
                              "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                   price_micro=(100 + i) * 1_000_000, change_pct_bp=50, source_unit="yuan",
                   ingestion_run_id=rid, created_at=now)
    return sid


def test_aggregate_two_stocks(conn) -> None:
    """两股各1个转正事件(可见2025-04-30,价窗内) → 聚合 n=2。"""
    _setup_stock(conn, "600001", "甲", [("2024Q4", -1000), ("2025Q1", 5000)])
    _setup_stock(conn, "600002", "乙", [("2024Q4", -2000), ("2025Q1", 3000)])
    res = compute_market_fundamental_stats(conn, horizons=(20,))
    assert res["stats_written"] >= 1
    assert res["events"]["profit_turn_positive@20"] == 2
    out = read_market_stats(conn)
    st = next(s for s in out["stats"] if s["signal_kind"] == "profit_turn_positive")
    assert st["trigger_count"] == 2
    assert float(st["win_rate"]) == 100.0    # 单调涨全胜
    assert not st["reliable"]                # n=2 < 30 诚实
    assert "重述" in out["disclosure"]


def test_baseline_pooled(conn) -> None:
    """pooled 基准:单调涨 → 基准≈100%。"""
    _setup_stock(conn, "600001", "甲", [("2024Q4", -1000), ("2025Q1", 5000)])
    compute_market_fundamental_stats(conn, horizons=(20,))
    out = read_market_stats(conn)
    st = out["stats"][0]
    assert float(st["baseline_win_rate"]) == 100.0


def test_recompute_overwrites(conn) -> None:
    """重算覆盖(UNIQUE upsert),不叠加。"""
    _setup_stock(conn, "600001", "甲", [("2024Q4", -1000), ("2025Q1", 5000)])
    compute_market_fundamental_stats(conn, horizons=(20,))
    compute_market_fundamental_stats(conn, horizons=(20,))
    n = conn.execute("SELECT COUNT(*) FROM market_signal_stat WHERE horizon=20").fetchone()[0]
    assert n == len(read_market_stats(conn)["stats"])   # 每 kind×horizon 一行


def test_read_empty(conn) -> None:
    out = read_market_stats(conn)
    assert out["stats"] == [] and "尚未计算" in out["note"]
