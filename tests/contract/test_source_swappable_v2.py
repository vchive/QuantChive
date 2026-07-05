"""T064: 换源不改上层（SC-008）。fake ObservationSource 替换东财，排行/采集全跑通。"""

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
    SectorType,
    SortField,
    SubjectLevel,
)
from quantchive.service.ingest_service import (
    CollectRequest,
    collect_sector_observations_once,
)
from quantchive.service.query_service import QueryService


class AltSource:
    """完全不同的假源（非东财派生），只实现 ObservationSource Protocol。"""

    source_id = "alt:provider"
    adapter_version = "alt-1"

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        return SubjectCapability(
            asset_class=AssetClass.A_SHARE, subject_kind=spec.subject_kind, has_five_tier=True,
            supported_metrics=("main_net",), available_sort_fields=(SortField.MAIN_NET,),
            series_granularity="1min")

    def fetch_subjects(self, spec): return []

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        d = Decimal
        rows = [("能源", "800000000"), ("地产", "-400000000"), ("医药", "200000000")]
        return [RawObservation(
            source_symbol=n, display_name=n, asset_class=AssetClass.A_SHARE,
            level=SubjectLevel.SECTOR, subject_kind=spec.subject_kind, caliber=Caliber.EASTMONEY,
            main_net=d(m), super_large_net=d(m), large_net=d("0"), medium_net=d("0"),
            small_net=d("0"), source_unit=AmountUnit.YUAN) for n, m in rows]

    def fetch_members(self, parent): return []

    def fetch_daily_final(self, spec, trade_date):
        return self.fetch_observations(spec)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


def test_alt_source_drives_ranking_unchanged(conn) -> None:
    """替换数据源不改采集/查询上层：AltSource 灌数据后排行正常产出。"""
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    req = CollectRequest(caliber=Caliber.EASTMONEY, source_code="eastmoney",
                         sector_types=(SectorType.INDUSTRY,), adapter_version="alt-1")
    # 上层 collect 函数对 source 只依赖 Protocol，不感知具体实现
    result = collect_sector_observations_once(
        req, source=AltSource(), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn), clock=clock)
    assert result.rows_written == 3

    svc = QueryService(conn, retention_trade_days=7, source_id="alt:provider", clock=clock)
    r = svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.MAIN_NET, top_n=10)
    assert r.mode == "bipolar"
    assert [i.display_name for i in r.top_inflow] == ["能源", "医药"]   # +8亿 > +2亿
    assert r.top_outflow[0].display_name == "地产"                     # 最负
    assert r.provenance.source_id == "alt:provider"                    # 真实来源透传
