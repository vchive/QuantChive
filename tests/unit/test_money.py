"""T016: 金额换算（宪章 III / research.md D1）。"""

from __future__ import annotations

import pytest

from quantchive.core.money import AmountParseError, cents_to_yuan_str, to_cents
from quantchive.models.enums import AmountUnit


def test_eastmoney_yuan_to_cents() -> None:
    # 东财单位=元，×100 → 分。spec 预期值。
    assert to_cents("-1.411231e+08", AmountUnit.YUAN) == -14112310000


def test_ths_yi_to_cents() -> None:
    # 同花顺单位=亿元，×1e10 → 分。spec 预期值。
    assert to_cents("1.23", AmountUnit.YI) == 12300000000


def test_zero() -> None:
    assert to_cents("0", AmountUnit.YUAN) == 0
    assert to_cents(0, AmountUnit.YI) == 0


def test_negative_yuan() -> None:
    assert to_cents("-100", AmountUnit.YUAN) == -10000


@pytest.mark.parametrize("bad", ["", "-", "--", "None", None, "nan", "NaN", "inf"])
def test_non_numeric_raises(bad: object) -> None:
    with pytest.raises(AmountParseError):
        to_cents(bad, AmountUnit.YUAN)


def test_no_float_precision_loss() -> None:
    # 直接传 float 也须先 str() 再进 Decimal，不得引入二进制误差
    # 0.07 元 = 7 分（若 Decimal(float) 会得到 7.0000000...1 类误差）
    assert to_cents(0.07, AmountUnit.YUAN) == 7


def test_round_half_even() -> None:
    # 0.005 元 = 0.5 分 → 银行家舍入到 0（偶）
    assert to_cents("0.005", AmountUnit.YUAN) == 0
    # 0.015 元 = 1.5 分 → 舍入到 2（偶）
    assert to_cents("0.015", AmountUnit.YUAN) == 2


def test_cents_to_yuan_str() -> None:
    assert cents_to_yuan_str(-14112310000) == "-141123100.00"
    assert cents_to_yuan_str(0) == "0.00"
