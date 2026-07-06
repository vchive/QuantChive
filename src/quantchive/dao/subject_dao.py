"""subject 维表 DAO（spec002 D1/D7，泛化自 sector_dao）。

统一大盘/板块/个股/ETF/基金。upsert 得稳定 subject_id；find_id 带 asset_class+level
防跨主体误命中（如个股和板块可能重名）。children_of 走 subject_membership as-of。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel


class SubjectDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        *,
        asset_class: AssetClass,
        level: SubjectLevel,
        subject_kind: SubjectKind,
        source_code: str,
        source_symbol: str,
        display_name: str | None = None,
        caliber: str = "eastmoney",
        collect_tier: str = "daily",
        exchange: str | None = None,
        em_board_code: str | None = None,
        is_hot: bool = False,
    ) -> tuple[int, bool]:
        """返回 (subject_id, is_new)。业务键 (asset_class, subject_kind, source, symbol)。"""
        now = datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            """SELECT subject_id FROM subject
               WHERE asset_class_code=? AND subject_kind=? AND source_code=? AND source_symbol=?""",
            (asset_class.value, subject_kind.value, source_code, source_symbol),
        ).fetchone()
        if row is not None:
            self._conn.execute(
                "UPDATE subject SET last_seen_at=?, is_active=1 WHERE subject_id=?",
                (now, row[0]),
            )
            return int(row[0]), False
        cur = self._conn.execute(
            """INSERT INTO subject
               (asset_class_code, level, subject_kind, source_code, source_symbol,
                display_name, caliber, collect_tier, exchange, em_board_code, is_hot,
                first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (asset_class.value, level.value, subject_kind.value, source_code, source_symbol,
             display_name or source_symbol, caliber, collect_tier, exchange, em_board_code,
             1 if is_hot else 0, now, now),
        )
        return int(cur.lastrowid), True

    def find_id(
        self, *, asset_class: AssetClass, level: SubjectLevel, source_symbol: str
    ) -> int | None:
        """带 asset_class+level 防跨主体误命中（宪章 I 可复现）。"""
        row = self._conn.execute(
            """SELECT subject_id FROM subject
               WHERE asset_class_code=? AND level=? AND source_symbol=? AND is_active=1""",
            (asset_class.value, level.value, source_symbol),
        ).fetchone()
        return int(row[0]) if row else None

    def search_by_name(self, *, query: str, limit: int = 10) -> list[dict]:
        """按名称/代码模糊搜主体（agent 名→ID 解析）。名称精确>前缀>包含,代码精确匹配。"""
        q = query.strip()
        rows = self._conn.execute(
            """SELECT subject_id, source_symbol, display_name, asset_class_code, level, subject_kind
               FROM subject
               WHERE is_active=1 AND (display_name LIKE ? OR source_symbol LIKE ?)
               ORDER BY
                 CASE WHEN display_name=? OR source_symbol=? THEN 0
                      WHEN display_name LIKE ? THEN 1 ELSE 2 END,
                 length(display_name)
               LIMIT ?""",
            (f"%{q}%", f"%{q}%", q, q, f"{q}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, subject_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM subject WHERE subject_id=?", (subject_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_by(
        self, *, asset_class: AssetClass, level: SubjectLevel | None = None,
        subject_kind: SubjectKind | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM subject WHERE asset_class_code=? AND is_active=1"
        params: list = [asset_class.value]
        if level is not None:
            sql += " AND level=?"; params.append(level.value)
        if subject_kind is not None:
            sql += " AND subject_kind=?"; params.append(subject_kind.value)
        sql += " ORDER BY source_symbol"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def children_of(self, parent_subject_id: int, as_of: str) -> list[int]:
        """as-of 查询板块成分个股 subject_id（无前视，宪章 II）。"""
        rows = self._conn.execute(
            """SELECT child_subject_id FROM subject_membership
               WHERE parent_subject_id=? AND effective_from<=?
                 AND (effective_to IS NULL OR effective_to>?)""",
            (parent_subject_id, as_of, as_of),
        ).fetchall()
        return [int(r[0]) for r in rows]
