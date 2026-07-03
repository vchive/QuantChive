"""能力注册表（T021，主体×指标能力真源）。

available_sort_fields 按 (asset_class, subject_kind) 隔离——板块只暴露五档资金流排序，
拒价格排序（防非法字段打空板块双榜、破 SC-010）。底层查 metric_applicability + metric_def。
"""

from __future__ import annotations

import sqlite3

from quantchive.dao.metric_dao import MetricDao
from quantchive.models.enums import AssetClass, SortField, SubjectKind

# metric_name ↔ SortField（可排序指标才有映射）
_METRIC_TO_SORT = {
    "main_net": SortField.MAIN_NET,
    "super_large_net": SortField.SUPER_LARGE_NET,
    "large_net": SortField.LARGE_NET,
    "medium_net": SortField.MEDIUM_NET,
    "small_net": SortField.SMALL_NET,
    "change_pct": SortField.CHANGE_PCT,
    "price": SortField.PRICE,
    "volume": SortField.VOLUME,
}
# 恒正指标 → 单榜（unipolar）；有正负 → 双榜（bipolar）
_UNIPOLAR_SORT = {SortField.PRICE, SortField.VOLUME}


class CapabilityRegistry:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._metric = MetricDao(conn)

    def supported_metrics(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind
    ) -> list[str]:
        return self._metric.applicable_metrics(
            asset_class=asset_class, subject_kind=subject_kind)

    def available_sort_fields(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind
    ) -> list[SortField]:
        """按主体隔离的可排序字段。board 的 metric_applicability 无 price → 自然排除。"""
        names = self._metric.sortable_metrics(
            asset_class=asset_class, subject_kind=subject_kind)
        fields: list[SortField] = []
        for n in names:
            sf = _METRIC_TO_SORT.get(n)
            if sf is not None:
                fields.append(sf)
        # main_net 亦兼容其别名 net_amount（东财恒等），供 spec001 调用
        if SortField.MAIN_NET in fields and SortField.NET_AMOUNT not in fields:
            fields.append(SortField.NET_AMOUNT)
        return fields

    def has_five_tier(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind
    ) -> bool:
        return self._metric.supports(
            asset_class=asset_class, subject_kind=subject_kind, metric_name="main_net")

    def is_sort_allowed(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind, sort_by: SortField
    ) -> bool:
        return sort_by in self.available_sort_fields(
            asset_class=asset_class, subject_kind=subject_kind)

    @staticmethod
    def ranking_mode(sort_by: SortField) -> str:
        """恒正指标(price/volume)→单榜 unipolar；有正负→双榜 bipolar。"""
        return "unipolar" if sort_by in _UNIPOLAR_SORT else "bipolar"
