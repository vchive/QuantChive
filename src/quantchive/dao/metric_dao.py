"""metric_def / metric_applicability DAO（spec002 D5，能力自描述真源）。

指标标度、可排序性、以及"某品种/主体支持哪些指标"由此表回答——平台按能力
诚实自描述（不支持时明确拒绝，不伪造，spec002 FR）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from quantchive.models.enums import AssetClass, SubjectKind


@dataclass(frozen=True)
class MetricDef:
    metric_name: str
    display_name: str
    value_kind: str
    unit_label: str
    scale_factor: int
    storage: str          # 'fixed_column' | 'metric_kv'
    fixed_column: str | None
    is_sortable: bool


class MetricDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, metric_name: str) -> MetricDef | None:
        row = self._conn.execute(
            """SELECT metric_name, display_name, value_kind, unit_label, scale_factor,
                      storage, fixed_column, is_sortable
               FROM metric_def WHERE metric_name=?""",
            (metric_name,),
        ).fetchone()
        return self._row_to_def(row) if row else None

    def all(self) -> list[MetricDef]:
        rows = self._conn.execute(
            """SELECT metric_name, display_name, value_kind, unit_label, scale_factor,
                      storage, fixed_column, is_sortable FROM metric_def
               ORDER BY metric_name"""
        ).fetchall()
        return [self._row_to_def(r) for r in rows]

    def applicable_metrics(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind
    ) -> list[str]:
        """该 (品种, 主体细类) 支持的指标名。subject_kind IS NULL 行=品种全体适用。

        能力自描述真源：查询/排序层据此拒绝不支持的指标（spec002 诚实原则）。
        """
        rows = self._conn.execute(
            """SELECT DISTINCT metric_name FROM metric_applicability
               WHERE asset_class_code=? AND (subject_kind IS NULL OR subject_kind=?)
               ORDER BY metric_name""",
            (asset_class.value, subject_kind.value),
        ).fetchall()
        return [r[0] for r in rows]

    def sortable_metrics(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind
    ) -> list[str]:
        """能力内且 is_sortable 的指标——排行可用排序字段的真源（板块拒价格排序）。"""
        rows = self._conn.execute(
            """SELECT DISTINCT a.metric_name FROM metric_applicability a
               JOIN metric_def d ON d.metric_name = a.metric_name
               WHERE a.asset_class_code=? AND (a.subject_kind IS NULL OR a.subject_kind=?)
                 AND d.is_sortable=1
               ORDER BY a.metric_name""",
            (asset_class.value, subject_kind.value),
        ).fetchall()
        return [r[0] for r in rows]

    def supports(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind, metric_name: str
    ) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM metric_applicability
               WHERE asset_class_code=? AND metric_name=?
                 AND (subject_kind IS NULL OR subject_kind=?) LIMIT 1""",
            (asset_class.value, metric_name, subject_kind.value),
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_def(row: sqlite3.Row | tuple) -> MetricDef:
        return MetricDef(
            metric_name=row[0], display_name=row[1], value_kind=row[2], unit_label=row[3],
            scale_factor=int(row[4]), storage=row[5], fixed_column=row[6],
            is_sortable=bool(row[7]),
        )
