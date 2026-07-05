"""多源归一层（spec003 D7 · FR-015/SC-007）。

各源适配器已把原始字段映射为 RawObservation（价/量/额 Decimal）；本层负责**跨源单位归一
到内部整数最小单位**——金额按 source_unit（元/亿元）统一到「分」，保证东财(元) 与
其它源(亿元) 对同一实值产出相同的分值，跨源拼接数值不错乱。禁 float 参与货币。
"""

from __future__ import annotations

from quantchive.core.money import to_basis_points, to_cents, to_micro
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import AmountUnit


def _unit(obs: RawObservation) -> AmountUnit:
    u = obs.source_unit
    return u if isinstance(u, AmountUnit) else AmountUnit(u)


def net_cents(obs: RawObservation) -> int | None:
    """主力净额 → 内部整数分（按各源 source_unit 归一）。跨源同实值 → 同分（SC-007）。"""
    return None if obs.main_net is None else to_cents(obs.main_net, _unit(obs))


def price_micro(obs: RawObservation) -> int | None:
    """现价 → 微元（×1e6）。价格标度与源单位无关（均为元）。"""
    return None if obs.price is None else to_micro(obs.price)


def change_pct_bp(obs: RawObservation) -> int | None:
    """涨跌幅 → 基点（×100，2.35% → 235）。"""
    return None if obs.change_pct is None else to_basis_points(obs.change_pct)


def five_tier_cents(obs: RawObservation) -> dict[str, int] | None:
    """五档净额 → 内部分（按 source_unit 归一）。无五档（如 baostock）返回 None。"""
    if obs.main_net is None:
        return None
    unit = _unit(obs)
    out: dict[str, int] = {}
    for key, val in (
        ("main_net_cents", obs.main_net), ("super_large_net_cents", obs.super_large_net),
        ("large_net_cents", obs.large_net), ("medium_net_cents", obs.medium_net),
        ("small_net_cents", obs.small_net),
    ):
        if val is not None:
            out[key] = to_cents(val, unit)
    return out or None
