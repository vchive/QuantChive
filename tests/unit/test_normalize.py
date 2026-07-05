"""T025: 多源归一——跨源同实值→同分标度、禁 float、五档归一（SC-007）。"""

from __future__ import annotations

from decimal import Decimal

from quantchive.datasource.dto import RawObservation
from quantchive.datasource.normalize import (
    change_pct_bp,
    five_tier_cents,
    net_cents,
    price_micro,
)
from quantchive.datasource.ths_flow_src import _cn_amount_to_yuan
from quantchive.models.enums import AmountUnit, AssetClass, SubjectKind, SubjectLevel


def _obs(**kw) -> RawObservation:
    base = dict(source_symbol="600519", display_name="茅台", asset_class=AssetClass.A_SHARE,
                level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK)
    base.update(kw)
    return RawObservation(**base)


def test_net_cents_cross_source_scale_consistency() -> None:
    """东财(元) 与 亿元源 对同一实值(1亿元)→同分（SC-007 跨源标度一致）。"""
    em = _obs(main_net=Decimal("100000000"), source_unit=AmountUnit.YUAN)   # 1亿元(元表示)
    yi = _obs(main_net=Decimal("1"), source_unit=AmountUnit.YI)              # 1亿元(亿表示)
    assert net_cents(em) == net_cents(yi)          # 同实值→同分
    assert net_cents(em) == 100000000 * 100        # 1亿元 = 1e10 分


def test_net_cents_none_when_absent() -> None:
    assert net_cents(_obs(source_unit=AmountUnit.YUAN)) is None  # 无主力净额（如 baostock）


def test_price_and_pct_scaling() -> None:
    o = _obs(price=Decimal("8.38"), change_pct=Decimal("-7.91"), source_unit=AmountUnit.YUAN)
    assert price_micro(o) == 8_380_000            # ×1e6 微元
    assert change_pct_bp(o) == -791               # ×100 基点（-7.91% → -791bp）


def test_five_tier_cents() -> None:
    o = _obs(main_net=Decimal("100"), super_large_net=Decimal("60"), large_net=Decimal("40"),
             medium_net=Decimal("0"), small_net=Decimal("0"), source_unit=AmountUnit.YUAN)
    ft = five_tier_cents(o)
    assert ft["main_net_cents"] == 10000 and ft["super_large_net_cents"] == 6000


def test_ths_cn_amount_parsing() -> None:
    """同花顺中文单位串归一（万/亿 → 元）——多源字段归一（FR-015）。"""
    assert _cn_amount_to_yuan("2.07亿") == Decimal("207000000")
    assert _cn_amount_to_yuan("941.88万") == Decimal("9418800.00")
    assert _cn_amount_to_yuan("100") == Decimal("100")
    assert _cn_amount_to_yuan("--") is None
