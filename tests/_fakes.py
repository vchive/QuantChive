"""测试共用假数据源（spec002 ObservationSource Protocol，纯新路径）。

替代已删除的 conftest.FakeSource（旧 SectorFlowSource）。板块/个股/ETF 假源，供
契约/集成测试灌数据，不联网。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

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

# 板块 mock：主力净额 半导体+5亿 / 银行-3亿 / 白酒+1亿 / 券商-0.5亿（含正负 + tiebreaker）
SECTOR_MOCK = [
    ("半导体", "500000000", "300000000", "150000000", "30000000", "20000000"),
    ("银行", "-300000000", "-200000000", "-60000000", "-25000000", "-15000000"),
    ("白酒", "100000000", "60000000", "25000000", "10000000", "5000000"),
    ("券商", "-50000000", "-30000000", "-12000000", "-5000000", "-3000000"),
]


def sector_observation(name, main, xl, lg, md, sm) -> RawObservation:
    d = Decimal
    return RawObservation(
        source_symbol=name, display_name=name, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.SECTOR, subject_kind=SubjectKind.INDUSTRY, caliber=Caliber.EASTMONEY,
        main_net=d(main), super_large_net=d(xl), large_net=d(lg), medium_net=d(md),
        small_net=d(sm), source_unit=AmountUnit.YUAN,
    )


class FakeSectorObservationSource:
    """板块 ObservationSource 假源（新 Protocol）。"""

    source_id = "fake:eastmoney"
    adapter_version = "test"

    def __init__(self, mock=SECTOR_MOCK) -> None:
        self._mock = mock

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        return SubjectCapability(
            asset_class=AssetClass.A_SHARE, subject_kind=spec.subject_kind, has_five_tier=True,
            supported_metrics=("main_net",), available_sort_fields=(SortField.MAIN_NET,),
            series_granularity="1min")

    def fetch_subjects(self, spec): return []

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        return [sector_observation(*m) for m in self._mock]

    def fetch_members(self, parent): return []

    def fetch_daily_final(self, spec, trade_date):
        return self.fetch_observations(spec)
