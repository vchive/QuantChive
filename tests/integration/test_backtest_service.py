"""B3 回测编排端到端(内存库双源:sina流 + baostock_hfq价)。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone, date as _date

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
from quantchive.service.backtest_service import BacktestService
from quantchive.service.signal_lib import ACCUMULATION


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _run(conn, src):
    return RunDao(conn).start(source_code=src, run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-01-01",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def test_backtest_stock_end_to_end(conn) -> None:
    """造 40 天:前段价跌+主力流入(吸筹),后段价反弹 → 吸筹信号后收益为正。"""
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol="600519", display_name="测试股", exchange="SSE")
    obs = ObservationDao(conn)
    rid_flow = _run(conn, "sina_flow")
    rid_px = _run(conn, "baostock_hfq")
    now = datetime.now(timezone.utc).isoformat()

    # 40 交易日:价先跌(d0-d19: 100→80)后涨(d20-d39: 80→120),主力全程净流入
    base = _date(2026, 2, 2)
    for i in range(40):
        d = (base + timedelta(days=i)).isoformat()
        price = (100 - i) if i < 20 else (80 + (i - 20) * 2)
        # 流行(sina):主力净流入 + 价
        obs.upsert(subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   five_tier={"main_net_cents": 10_00, "super_large_net_cents": 5_00,
                              "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                   price_micro=price * 1_000_000, source_unit="yuan",
                   ingestion_run_id=rid_flow, created_at=now)
        # 价行(hfq):同 subject 同 date 不同 source_code
        obs.upsert(subject_id=sid, source_code="baostock_hfq", trade_date=d, minute_slot="EOD",
                   value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                   price_micro=price * 1_000_000, volume=1000, source_unit="yuan",
                   ingestion_run_id=rid_px, created_at=now)

    res = BacktestService(conn).backtest_stock(
        subject_id=sid, kinds=[ACCUMULATION], horizons=(5,), lookback_years=5)
    assert res.display_name == "测试股"
    stat = next(s for s in res.stats if s.signal_kind == ACCUMULATION and s.horizon == 5)
    # 吸筹信号在跌段触发,5日后多在反弹段 → 触发数>0
    assert stat.trigger_count > 0
    assert stat.note is not None       # 样本<30 诚实标注
    assert "." in stat.win_rate        # 百分比字符串


def test_backtest_no_price_raises(conn) -> None:
    """只有流没 hfq 价 → 明确报错(需回填 baostock_hfq)。"""
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol="600001", display_name="缺价股", exchange="SSE")
    obs = ObservationDao(conn)
    rid = _run(conn, "sina_flow")
    now = datetime.now(timezone.utc).isoformat()
    obs.upsert(subject_id=sid, source_code="sina_flow", trade_date="2026-02-01",
               minute_slot="EOD", value_type=ValueType.DAILY_FINAL, granularity="daily",
               observed_at=now, five_tier={"main_net_cents": 10_00, "super_large_net_cents": 0,
               "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
               price_micro=100_000_000, source_unit="yuan", ingestion_run_id=rid, created_at=now)
    from quantchive.service.errors import NoDataForDate
    with pytest.raises(NoDataForDate):
        BacktestService(conn).backtest_stock(subject_id=sid, kinds=[ACCUMULATION])
