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
    compute_market_combo_stats,
    compute_market_flow_stats,
    compute_market_fundamental_stats,
    read_market_stats,
)
from quantchive.service.signal_lib import SignalHit, dedupe_hits_non_overlapping


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


# ---------- 资金流全市场聚合(H) ----------

def test_dedupe_non_overlapping() -> None:
    """同股连日触发 → horizon 内只留第1个;间隔>horizon 都留。"""
    dates = [f"d{i:02d}" for i in range(20)]
    hits = [SignalHit("d01", "accumulation"), SignalHit("d02", "accumulation"),
            SignalHit("d03", "accumulation"), SignalHit("d10", "accumulation")]
    out = dedupe_hits_non_overlapping(hits, dates, horizon=5)
    assert [h.trade_date for h in out] == ["d01", "d10"]   # d02/d03 在 d01+5 内被去重


def test_dedupe_keeps_sparse() -> None:
    dates = [f"d{i:02d}" for i in range(20)]
    hits = [SignalHit("d01", "x"), SignalHit("d08", "x"), SignalHit("d15", "x")]
    out = dedupe_hits_non_overlapping(hits, dates, horizon=5)
    assert len(out) == 3                                    # 间隔>horizon 全保留


def test_flow_stats_aggregate(conn) -> None:
    """造一只价跌+主力净流入的股 → 吸筹事件聚合进 market_signal_stat。"""
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol="600009", display_name="流股", exchange="SSE")
    rid = RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                             caliber=Caliber.EASTMONEY, trade_date="2025-01-01",
                             minute_slot="EOD", adapter_version="t", subject_scope="x",
                             asset_class_code="a_share")
    now = datetime.now(timezone.utc).isoformat()
    obs = ObservationDao(conn)
    base = _date(2025, 3, 3)
    # 60日:前30日价跌(吸筹窗)后30日横盘,主力全程净流入
    for i in range(60):
        d = (base + timedelta(days=i)).isoformat()
        price = (100 - i) if i < 30 else 70
        chg = -100 if 0 < i < 30 else 0
        obs.upsert(subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   five_tier={"main_net_cents": 10_00, "super_large_net_cents": 100,
                              "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                   price_micro=price * 1_000_000, change_pct_bp=chg, source_unit="yuan",
                   ingestion_run_id=rid, created_at=now)
    res = compute_market_flow_stats(conn, horizons=(5,))
    assert res["events"]["accumulation@5"] >= 1
    # 去重生效:连日吸筹触发但非重叠 → 事件数远小于触发日数(30日跌段最多6个非重叠)
    assert res["events"]["accumulation@5"] <= 6
    out = read_market_stats(conn, family="flow")
    assert any(s["signal_kind"] == "accumulation" for s in out["stats"])
    assert "去重" in out["disclosure"]


def test_family_filter(conn) -> None:
    """family 过滤:基本面/资金流各自只出自家 kind + 对应披露。"""
    _setup_stock(conn, "600001", "甲", [("2024Q4", -1000), ("2025Q1", 5000)])
    compute_market_fundamental_stats(conn, horizons=(20,))
    fund = read_market_stats(conn, family="fundamental")
    flow = read_market_stats(conn, family="flow")
    assert all(s["signal_kind"].startswith(("profit", "revenue", "roe")) for s in fund["stats"])
    assert flow["stats"] == [] and "去重" in flow["disclosure"]
    assert "重述" in fund["disclosure"]


# ---------- 组合条件统计(阶段E-E1) ----------

def _setup_declining_stock(conn, sym, name, yoy_series, price_days=120):
    """价一路下跌+主力净流入(吸筹信号持续闪) → 组合确认方。"""
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
                   five_tier={"main_net_cents": 10_00, "super_large_net_cents": 100,
                              "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                   price_micro=(200 - i) * 1_000_000, change_pct_bp=-50, source_unit="yuan",
                   ingestion_run_id=rid, created_at=now)
    return sid


def test_combo_confirmation_gating(conn) -> None:
    """有资金流确认的基本面事件进组合;无确认的不进;baseline=基本面单独胜率。"""
    yoy = [("2024Q4", -1000), ("2025Q1", 5000)]     # 净利转正,可见2025-04-30
    _setup_stock(conn, "600001", "无确认股", yoy)    # 价升(无吸筹) → 不进组合
    _setup_declining_stock(conn, "600002", "有确认股", yoy)  # 价跌+主力流入(吸筹) → 进组合
    res = compute_market_combo_stats(conn, horizons=(20,))
    assert res["events"].get("profit_turn_positive+accumulation@20") == 1   # 只有确认股
    out = read_market_stats(conn, family="combo")
    row = next(s for s in out["stats"]
               if s["signal_kind"] == "profit_turn_positive+accumulation" and s["horizon"] == 20)
    assert row["trigger_count"] == 1
    # 基本面单独:2事件(升股赢/跌股输)→ 单独胜率50% = 组合的对照
    assert row["baseline_win_rate"] == "50.0"
    # 组合(只含跌股)胜率0 → 未跑赢单独
    assert row["win_rate"] == "0.0" and not row["beats_baseline"]
    assert "多horizon同向" in out["disclosure"] or "单独" in out["disclosure"]


def test_market_stats_endpoint_family_combo(conn) -> None:
    """端点 family=combo 只返组合行(回归:曾漏 combo 白名单致全表泄漏)。"""
    yoy = [("2024Q4", -1000), ("2025Q1", 5000)]
    _setup_stock(conn, "600001", "甲", yoy)
    _setup_declining_stock(conn, "600002", "乙", yoy)
    compute_market_combo_stats(conn, horizons=(20,))
    from fastapi.testclient import TestClient
    from quantchive.app import create_app
    from quantchive.api.deps import get_conn
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    c = TestClient(app)
    j = c.get("/api/signals/market_stats?family=combo").json()
    assert j["stats"] and all("+" in s["signal_kind"] for s in j["stats"])  # 全是组合
