"""板块 Source 契约（ObservationSource 路径 + 翻页拿全 + 能力隔离 + 成分下钻）。"""

from __future__ import annotations

import pytest

from quantchive.datasource.base import DataSourceError, FetchSpec
from quantchive.datasource.em_sector_src import EastMoneySource
from quantchive.models.enums import (
    AssetClass,
    SectorType,
    SortField,
    SubjectKind,
    SubjectLevel,
)


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._p = payload
        self.status_code = status

    def json(self) -> dict:
        return self._p


def _industry_spec() -> FetchSpec:
    return FetchSpec(
        fs="m:90 t:2", level=SubjectLevel.SECTOR, asset_class=AssetClass.A_SHARE,
        subject_kind=SubjectKind.INDUSTRY, sector_type=SectorType.INDUSTRY,
    )


def test_fetch_observations_returns_raw_observation() -> None:
    rows = [{"f12": "BK0475", "f14": "半导体", "f62": 8.5e8, "f66": 5e8,
             "f72": 3.5e8, "f78": 1e8, "f84": -1e8}]
    src = EastMoneySource(http_get=lambda *a, **k: _Resp({"data": {"diff": rows}}))
    obs = src.fetch_observations(_industry_spec())
    assert len(obs) == 1
    o = obs[0]
    assert o.source_symbol == "半导体"
    assert o.asset_class == AssetClass.A_SHARE
    assert o.level == SubjectLevel.SECTOR
    assert o.subject_kind == SubjectKind.INDUSTRY
    assert str(o.main_net) == "850000000.0"
    assert str(o.small_net) == "-100000000.0"
    assert o.em_board_code == "BK0475"


def test_pagination_fetches_all_991_dynamic() -> None:
    """动态翻页 ceil(total/100)：total=991 须翻 10 页拿全（PCB/液冷等靠后板块不截断）。"""
    total = 991
    all_rows = [{"f12": f"BK{i:04d}", "f14": f"板块{i}", "f62": float(total - i) * 1e6,
                 "f66": 1e6, "f72": 1e6, "f78": 1e6, "f84": 1e6} for i in range(total)]

    def get(url, params=None, **k):
        pn, pz = int(params["pn"]), int(params["pz"])
        start = (pn - 1) * pz
        return _Resp({"data": {"total": total, "diff": all_rows[start:start + pz]}})

    src = EastMoneySource(http_get=get, page_size=100, sleep=lambda s: None)
    obs = src.fetch_observations(_industry_spec())
    assert len(obs) == 991
    names = {o.source_symbol for o in obs}
    assert "板块0" in names and "板块990" in names


def test_capability_board_rejects_price_sort() -> None:
    """板块能力自描述：可排序仅五档，不含 price（板块无价格排序，按主体隔离）。"""
    src = EastMoneySource(http_get=lambda *a, **k: _Resp({"data": {"diff": []}}))
    cap = src.capability(_industry_spec())
    assert cap.has_five_tier is True
    assert SortField.MAIN_NET in cap.available_sort_fields
    assert SortField.PRICE not in cap.available_sort_fields
    assert cap.series_granularity == "1min"


def test_fetch_members_requires_board_code() -> None:
    """US3：无 em_board_code 无法下钻成员 → unsupported。"""
    src = EastMoneySource(http_get=lambda *a, **k: _Resp({"data": {"diff": []}}))
    from quantchive.datasource.base import SubjectRef
    parent = SubjectRef(source_symbol="半导体", display_name="半导体",
                        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                        subject_kind=SubjectKind.INDUSTRY, em_board_code=None)
    with pytest.raises(DataSourceError) as exc:
        src.fetch_members(parent)
    assert exc.value.error_type == "unsupported"


def test_fetch_members_returns_stock_observations() -> None:
    """US3：有 em_board_code → fs=b:BK{code} 拉成分个股五档+价量。"""
    rows = [{"f12": "600519", "f14": "贵州茅台", "f2": 1709.0, "f3": 2.35, "f5": 12345,
             "f6": 9990000, "f62": 8.5e8, "f66": 5e8, "f72": 3.5e8, "f78": 1e8, "f84": -1e8}]
    captured = {}

    def get(url, params=None, **k):
        captured["fs"] = params["fs"]
        return _Resp({"data": {"diff": rows}})

    src = EastMoneySource(http_get=get)
    from quantchive.datasource.base import SubjectRef
    parent = SubjectRef(source_symbol="白酒", display_name="白酒",
                        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                        subject_kind=SubjectKind.INDUSTRY, em_board_code="BK0475")
    members = src.fetch_members(parent)
    assert captured["fs"] == "b:BK0475"            # 板块成员 fs
    assert len(members) == 1
    m = members[0]
    assert m.source_symbol == "600519"
    assert m.level == SubjectLevel.INSTRUMENT and m.subject_kind == SubjectKind.STOCK
    assert str(m.main_net) == "850000000.0"
    assert str(m.price) == "1709.0"
    assert m.exchange == "SSE"
