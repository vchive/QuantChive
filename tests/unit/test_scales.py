"""T006: 多标度换算（宪章 III / spec002 data-model metric_def）。"""

from __future__ import annotations

import pytest

from quantchive.core.money import (
    AmountParseError,
    basis_points_to_str,
    micro_to_str,
    to_basis_points,
    to_cents,
    to_micro,
)
from quantchive.models.enums import AmountUnit


def test_price_to_micro() -> None:
    # 价格 12.34 元 → ×1e6 微元
    assert to_micro("12.34") == 12_340_000
    # 基金净值 4 位小数无损
    assert to_micro("2.6820") == 2_682_000


def test_change_pct_to_basis_points() -> None:
    # 涨跌幅 2.35% → ×1e4 基点 = 235
    assert to_basis_points("2.35") == 235
    # 负涨跌幅
    assert to_basis_points("-1.08") == -108
    # 换手率 0.01% 精度
    assert to_basis_points("0.01") == 1


def test_zero_and_negative() -> None:
    assert to_micro("0") == 0
    assert to_basis_points("0") == 0
    assert to_micro("-5.5") == -5_500_000


@pytest.mark.parametrize("bad", ["", "-", "None", None, "nan", "inf"])
def test_non_numeric_raises(bad: object) -> None:
    with pytest.raises(AmountParseError):
        to_micro(bad)
    with pytest.raises(AmountParseError):
        to_basis_points(bad)


def test_no_float_precision_loss() -> None:
    # 直接传 float 也须先 str() 再进 Decimal
    assert to_micro(12.34) == 12_340_000
    assert to_basis_points(2.35) == 235


def test_back_conversion_strings() -> None:
    assert micro_to_str(2_682_000) == "2.6820"
    assert micro_to_str(12_340_000) == "12.3400"
    assert basis_points_to_str(235) == "2.35"
    assert basis_points_to_str(-108) == "-1.08"


def test_existing_to_cents_unchanged() -> None:
    # 回归：spec001 金额换算不变
    assert to_cents("-1.411231e+08", AmountUnit.YUAN) == -14112310000
    assert to_cents("1.23", AmountUnit.YI) == 12300000000
