"""金额换算 —— 宪章 III 核心（research.md D1）。

金额一律存整数分（有符号，净流入为正）。禁 float 做金额运算/比较/排序。
换算强制 Decimal(str(raw))，绝不 Decimal(float)。
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from quantchive.models.enums import AmountUnit

# 单位 → 「1 单位 = 多少分」的标度
_SCALE: dict[AmountUnit, Decimal] = {
    AmountUnit.YUAN: Decimal(100),  # 1 元 = 100 分
    AmountUnit.YI: Decimal(10_000_000_000),  # 1 亿元 = 1e8 元 = 1e10 分
}


class AmountParseError(ValueError):
    """原始金额无法解析为有效数值（如空串、'-'、NaN）。"""


def _to_decimal(raw: object) -> Decimal:
    """把原始值安全解析为有限 Decimal（先 str() 再进，禁 Decimal(float)）。"""
    if raw is None:
        raise AmountParseError("value is None")
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        raise AmountParseError(f"non-numeric value: {text!r}")
    try:
        dec = Decimal(text)
    except InvalidOperation as exc:
        raise AmountParseError(f"cannot parse value: {text!r}") from exc
    if not dec.is_finite():
        raise AmountParseError(f"non-finite value: {text!r}")
    return dec


def _scale_to_int(raw: object, scale: Decimal) -> int:
    dec = _to_decimal(raw)
    return int((dec * scale).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def to_cents(raw: object, unit: AmountUnit) -> int:
    """按单位标度换算为整数分（宪章 III）。缺失值抛 AmountParseError，不冒充 0。"""
    return _scale_to_int(raw, _SCALE[unit])


# spec002 多标度换算（data-model.md metric_def.scale_factor）
_MICRO = Decimal(1_000_000)      # ×1e6 价格/净值
# 基点：1 bp = 0.01%。数据源涨跌幅是百分数（2.35 表示 2.35%），
# 2.35% = 235 bp，故百分数 → 基点 = ×100。
# （data-model 的 "×1e4" 指对比率 0.0235 而言；我们拿到的是百分数 2.35，用 ×100 等价。）
_PCT_TO_BP = Decimal(100)


def to_micro(raw: object) -> int:
    """价格/净值 → ×1e6 微元整数（余量足以承载基金 4 位小数，禁 float）。"""
    return _scale_to_int(raw, _MICRO)


def to_basis_points(raw: object) -> int:
    """百分数（如涨跌幅 2.35 表示 2.35%）→ 基点整数（2.35% = 235 bp，禁 float）。"""
    return _scale_to_int(raw, _PCT_TO_BP)


def cents_to_yuan_str(cents: int) -> str:
    """整数分 → 元的精确字符串（展示/序列化用，不进 float）。"""
    q = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
    return str(q)


def micro_to_str(micro: int, places: int = 4) -> str:
    """×1e6 微元 → 真实值精确字符串（价格/净值展示）。"""
    q = (Decimal(micro) / _MICRO).quantize(Decimal(10) ** -places)
    return str(q)


def basis_points_to_str(bp: int) -> str:
    """基点 → 百分数精确字符串（如 235 → '2.35'）。"""
    q = (Decimal(bp) / _PCT_TO_BP).quantize(Decimal("0.01"))
    return str(q)
