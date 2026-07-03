"""T028: 集成 —— 个股采集写 LATEST + 大盘求和，大盘 main_net == 个股 main_net 汇总。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Sequence

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import FetchSpec, SubjectCapability
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SortField,
    SubjectKind,
    SubjectLevel,
)
from quantchive.service.ingest_service import collect_stock_observations_once
from quantchive.service.market_aggregate import MARKET_SYMBOL, MarketAggregator


def _stock_obs(code, name, main) -> RawObservation:
    d = Decimal
    m = d(main)
    return RawObservation(
        source_symbol=code, display_name=name, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
        main_net=m, super_large_net=m, large_net=d("0"), medium_net=d("0"), small_net=d("0"),
        price=d("11.09"), change_pct=d("2.35"), volume=d("123456"), turnover=d("9990000"),
        exchange="SSE", source_unit=AmountUnit.YUAN,
    )


class _FakeStockSource:
    source_id = "fake:stock"
    adapter_version = "test"

    def __init__(self, obs: list[RawObservation], total: int) -> None:
        self._obs = obs
        self.last_total = total

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        return SubjectCapability(
            asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.STOCK, has_five_tier=True,
            supported_metrics=("main_net", "price"), available_sort_fields=(SortField.MAIN_NET,),
            series_granularity="5min")

    def fetch_observations(self, spec) -> Sequence[RawObservation]:
        return self._obs

    def fetch_subjects(self, spec): return []
    def fetch_members(self, parent): return []
    def fetch_daily_final(self, spec, trade_date): return self._obs


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


def test_market_sum_equals_stock_total(conn) -> None:
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    obs = [_stock_obs("600519", "贵州茅台", "500000000"),
           _stock_obs("000001", "平安银行", "-300000000"),
           _stock_obs("600036", "招商银行", "100000000")]
    src = _FakeStockSource(obs, total=3)
    agg_svc = MarketAggregator(conn)

    result = collect_stock_observations_once(
        source=src, observation_dao=ObservationDao(conn), run_dao=RunDao(conn),
        subject_dao=SubjectDao(conn), aggregator=agg_svc, source_code="eastmoney",
        adapter_version="test", clock=clock)

    assert result.rows_written == 3
    assert result.aggregate is not None and result.aggregate.written is True
    # 大盘 main_net_cents == 个股 main_net 汇总 (5亿-3亿+1亿=3亿 → 30000000000 分)
    assert result.aggregate.main_net_cents == 30000000000
    assert result.aggregate.constituent_count == 3
    assert result.aggregate.coverage == 1.0

    # 大盘 observation 已落库（is_derived=1）
    market_id = SubjectDao(conn).find_id(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.MARKET, source_symbol=MARKET_SYMBOL)
    assert market_id is not None
    row = ObservationDao(conn).latest_market_point(market_subject_id=market_id)
    assert row.main_net_cents == 30000000000
    assert row.constituent_count == 3 and row.expected_count == 3
    assert row.value_type == "intraday_snapshot"       # 大盘时序点
    assert row.minute_slot == "10:30"                   # 复用个股 batch_slot


def test_stocks_written_as_latest_overwrite(conn) -> None:
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    src = _FakeStockSource([_stock_obs("600519", "贵州茅台", "500000000")], total=1)
    collect_stock_observations_once(
        source=src, observation_dao=ObservationDao(conn), run_dao=RunDao(conn),
        subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn), clock=clock)
    # 再采一次（覆盖）——LATEST 行数不累积
    src2 = _FakeStockSource([_stock_obs("600519", "贵州茅台", "600000000")], total=1)
    collect_stock_observations_once(
        source=src2, observation_dao=ObservationDao(conn), run_dao=RunDao(conn),
        subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn), clock=clock)
    n = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE value_type='intraday_latest'").fetchone()[0]
    assert n == 1                                       # LATEST 覆盖不累积
    # 个股价格已按 ×1e6 存
    row = conn.execute(
        "SELECT price_micro, change_pct_bp FROM observation WHERE value_type='intraday_latest'"
    ).fetchone()
    assert row[0] == 11_090_000                          # 11.09 ×1e6
    assert row[1] == 235                                 # 2.35% → 235 bp


def test_stock_writes_snapshot_for_series(conn) -> None:
    """个股双写：LATEST 覆盖行（排行/大盘）+ intraday_snapshot 真实时点行（供时序，D4②）。

    series 只读 intraday_snapshot；若只写 LATEST 则时序永远空（比亚迪线上问题）。
    """
    for hhmm in [(10, 30), (10, 35)]:
        clock = FixedClock(datetime(2026, 7, 2, hhmm[0], hhmm[1], tzinfo=SHANGHAI_TZ))
        src = _FakeStockSource([_stock_obs("002594", "比亚迪", "500000000")], total=1)
        collect_stock_observations_once(
            source=src, observation_dao=ObservationDao(conn), run_dao=RunDao(conn),
            subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn), clock=clock)
    sid = SubjectDao(conn).find_id(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT, source_symbol="002594")
    # LATEST 仍只 1 行（覆盖）
    n_latest = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE subject_id=? AND value_type='intraday_latest'",
        (sid,)).fetchone()[0]
    assert n_latest == 1
    # intraday_snapshot 两个真实时点 → 时序 2 点
    snaps = conn.execute(
        "SELECT minute_slot FROM observation WHERE subject_id=? AND value_type='intraday_snapshot' "
        "ORDER BY minute_slot", (sid,)).fetchall()
    assert [r[0] for r in snaps] == ["10:30", "10:35"]
    series = ObservationDao(conn).series(subject_id=sid, trade_date="2026-07-02")
    assert [r.minute_slot for r in series] == ["10:30", "10:35"]   # 时序非空，无 LATEST


def test_low_coverage_market_not_written(conn) -> None:
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    # 采到 3 只但 total=10（翻页截断模拟）→ 30% 覆盖 → 大盘不落
    obs = [_stock_obs(f"60000{i}", f"股{i}", "100000000") for i in range(3)]
    src = _FakeStockSource(obs, total=10)
    result = collect_stock_observations_once(
        source=src, observation_dao=ObservationDao(conn), run_dao=RunDao(conn),
        subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn), clock=clock)
    assert result.aggregate.written is False
    market_id = SubjectDao(conn).find_id(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.MARKET, source_symbol=MARKET_SYMBOL)
    # 大盘主体可能已 upsert，但无 derived 点
    if market_id is not None:
        assert ObservationDao(conn).latest_market_point(market_subject_id=market_id) is None
