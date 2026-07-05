"""T012: 限流降级识别接线——采集降级→ingestion_run.degraded=1、拒绝冒充完整（SC-001）。"""

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
from quantchive.datasource.base import FetchSpec
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)
from quantchive.service.ingest_service import collect_stock_observations_once
from quantchive.service.market_aggregate import MarketAggregator


def _stock(code, main) -> RawObservation:
    m = Decimal(main)
    return RawObservation(
        source_symbol=code, display_name=code, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
        main_net=m, super_large_net=m, large_net=Decimal("0"), medium_net=Decimal("0"),
        small_net=Decimal("0"), source_unit=AmountUnit.YUAN)


class _FakeStockSource:
    """last_total=报告全集；实际只返 obs（模拟限流降级：报5535实回3）。"""
    source_id = "fake:stock"
    adapter_version = "t"

    def __init__(self, obs, total) -> None:
        self._obs = obs
        self.last_total = total

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        return self._obs

    def capability(self, spec): ...
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


def test_degraded_run_marked_and_market_not_written(conn) -> None:
    """报告 5535 却只回 3（限流降级）→ ingestion_run.degraded=1 + 大盘不落（零静默降级）。"""
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    obs = [_stock(f"60000{i}", "100000000") for i in range(3)]
    result = collect_stock_observations_once(
        source=_FakeStockSource(obs, total=5535), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn),
        aggregator=MarketAggregator(conn), clock=clock)
    # 大盘不落（覆盖率 3/5535 远 <95%）
    assert result.aggregate is not None and result.aggregate.written is False
    # 运行审计标记 degraded=1（显式识别，非静默）
    row = conn.execute(
        "SELECT degraded, used_source_code FROM ingestion_run ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert row["degraded"] == 1
    assert row["used_source_code"] == "eastmoney"


def test_full_fetch_not_degraded(conn) -> None:
    """回全 total → 不判降级、degraded=0。"""
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    obs = [_stock(f"60000{i}", "100000000") for i in range(5)]
    collect_stock_observations_once(
        source=_FakeStockSource(obs, total=5), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn),
        aggregator=MarketAggregator(conn), clock=clock)
    row = conn.execute(
        "SELECT degraded FROM ingestion_run ORDER BY run_id DESC LIMIT 1").fetchone()
    assert row["degraded"] == 0
