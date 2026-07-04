"""T023: 选源工厂——注册返实例、缺失 NoOp 兜底不崩、换源不改上层（SC-005/SC-008）。"""

from __future__ import annotations

from quantchive.datasource.base import FetchSpec
from quantchive.datasource.registry import (
    NoOpSource,
    get_history_source,
    get_source,
    register_source,
)
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel


def _spec():
    return FetchSpec(fs="x", level=SubjectLevel.INSTRUMENT, asset_class=AssetClass.A_SHARE,
                     subject_kind=SubjectKind.STOCK)


def test_get_registered_source() -> None:
    src = get_source("eastmoney")
    assert not isinstance(src, NoOpSource)                # 真实源，非兜底
    assert src.source_id.startswith("eastmoney")


def test_get_history_source() -> None:
    src = get_history_source("baostock")
    assert src.source_id == "baostock"
    assert hasattr(src, "fetch_price_history")


def test_missing_source_returns_noop_not_crash() -> None:
    src = get_source("does_not_exist")
    assert isinstance(src, NoOpSource)
    assert src.fetch_observations(_spec()) == []          # 不崩、返回空（优雅降级）


def test_missing_history_source_noop() -> None:
    assert isinstance(get_history_source("nope"), NoOpSource)


def test_register_new_source_upper_unchanged() -> None:
    """全新源仅登记构造器 → 上层 get_source 零改动消费（SC-005 换源不改上层）。"""
    class _Brand:
        source_id = "brand_x"
        adapter_version = "t"

        def fetch_observations(self, spec):
            return ["obs1", "obs2"]

    register_source("brand_x", lambda: _Brand())
    src = get_source("brand_x")
    assert src.source_id == "brand_x"
    assert src.fetch_observations(_spec()) == ["obs1", "obs2"]
