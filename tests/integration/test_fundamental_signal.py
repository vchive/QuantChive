"""基本面信号回测:法定截止日、信号检测、可见日入场、端到端。"""

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
from quantchive.service.backtest_core import forward_return_from_visible
from quantchive.service.fundamental_backtest_service import FundamentalBacktestService
from quantchive.service.fundamental_signal import (
    PROFIT_TURN_POSITIVE,
    detect_fundamental_signals,
    report_period_deadline,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


# ---------- 纯函数 ----------

def test_deadline_mapping() -> None:
    assert report_period_deadline("2025Q1") == "2025-04-30"
    assert report_period_deadline("2025Q2") == "2025-08-31"
    assert report_period_deadline("2025Q3") == "2025-10-31"
    assert report_period_deadline("2024Q4") == "2025-04-30"   # 年报→次年


def test_detect_profit_turn_positive() -> None:
    """净利同比 负→正 触发,可见日=法定截止日。"""
    series = [("2024Q3", -1500), ("2024Q4", -1000), ("2025Q1", 5000)]  # bp:-15%,-10%,+50%
    hits = detect_fundamental_signals(series, kind=PROFIT_TURN_POSITIVE)
    assert len(hits) == 1
    assert hits[0].report_period == "2025Q1" and hits[0].visible_date == "2025-04-30"
    assert hits[0].strength == 6000   # +50% - (-10%) = 60pp = 6000bp


def test_detect_no_turn_when_stays_negative() -> None:
    series = [("2024Q3", -1500), ("2024Q4", -1000)]  # 一直负
    assert detect_fundamental_signals(series, kind=PROFIT_TURN_POSITIVE) == []


def test_forward_return_from_visible_snaps() -> None:
    """可见日非交易日 → snap 到首个≥它的交易日入场。"""
    dates = ["2025-04-28", "2025-05-06", "2025-06-03"]  # 04-30是假期,snap到05-06
    px = {"2025-04-28": 100_000_000, "2025-05-06": 100_000_000, "2025-06-03": 110_000_000}
    # 可见日 2025-04-30 → 入场 05-06,horizon=1 → 06-03,收益 +10%
    r = forward_return_from_visible(px, dates, "2025-04-30", 1)
    assert r == 1000


def test_forward_return_no_leak_before_visible() -> None:
    """可见日之后无足够交易日 → None(不看不到的未来)。"""
    dates = ["2025-04-28", "2025-05-06"]
    px = {"2025-04-28": 100_000_000, "2025-05-06": 100_000_000}
    assert forward_return_from_visible(px, dates, "2025-04-30", 5) is None


# ---------- 端到端 ----------

def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SZSE")
    return sid


def test_fundamental_backtest_end_to_end(conn) -> None:
    """造:净利增速2025Q1转正(可见2025-04-30)+ 之后价涨 → 回测出触发。"""
    sid = _stock(conn, "002384", "东山精密")
    obs = ObservationDao(conn)
    frun = RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2025-01-01",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")
    now = datetime.now(timezone.utc).isoformat()
    # 基本面:净利同比 2024Q4 -10% → 2025Q1 +50%(转正)
    for period, yoy in [("2024Q4", -1000), ("2025Q1", 5000)]:
        conn.execute(
            """INSERT INTO fundamental_item(subject_id,report_period,announce_date,statement,
               item,value_int,unit,source_code,ingestion_run_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, period, None, "performance", "net_profit_yoy", yoy, "bp",
             "eastmoney_fin", frun, now))
    # 价:2025-03 ~ 2025-07 每日,可见日2025-04-30后持续涨
    base = _date(2025, 3, 3)
    for i in range(90):
        d = (base + timedelta(days=i)).isoformat()
        price = 100 + i  # 单调涨
        obs.upsert(subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   five_tier={"main_net_cents": 100, "super_large_net_cents": 0,
                              "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                   price_micro=price * 1_000_000, change_pct_bp=50, source_unit="yuan",
                   ingestion_run_id=frun, created_at=now)

    res = FundamentalBacktestService(conn).backtest_stock(
        subject_id=sid, kinds=[PROFIT_TURN_POSITIVE], horizons=(20,))
    st = next(s for s in res.stats if s.signal_kind == PROFIT_TURN_POSITIVE)
    assert st.trigger_count == 1          # 2025Q1 转正事件命中,入场≥04-30,前向20日有数据
    assert st.note is not None            # 样本<30 诚实标注
    assert "法定披露截止日" in res.price_source
