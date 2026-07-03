"""subject_membership DAO（T037，US3 板块→个股归属，as-of 无前视，宪章 II）。

- record：记成分归属（effective_from=当日），幂等。
- members_asof：某时点在册成分（effective_from<=asof AND (effective_to NULL OR >asof)）。
- close_absent：当日已不在成分表的旧成分闭合 effective_to（成分退出，data-model 要求）。
- has_membership_asof：某时点是否有任何成分记录（供 OutOfWindow 判定：历史无成分不静默用当前）。
"""

from __future__ import annotations

import sqlite3


class ConstituentDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(
        self, *, parent_subject_id: int, child_subject_id: int, effective_from: str,
        source_code: str, ingestion_run_id: int | None, created_at: str,
        relation_kind: str = "sector_constituent",
    ) -> None:
        """幂等记归属。业务键 (parent, child, relation_kind, effective_from)。"""
        self._conn.execute(
            """INSERT INTO subject_membership
               (parent_subject_id, child_subject_id, relation_kind, effective_from,
                source_code, ingestion_run_id, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(parent_subject_id, child_subject_id, relation_kind, effective_from)
               DO NOTHING""",
            (parent_subject_id, child_subject_id, relation_kind, effective_from,
             source_code, ingestion_run_id, created_at),
        )

    def members_asof(
        self, *, parent_subject_id: int, as_of: str,
        relation_kind: str = "sector_constituent",
    ) -> list[int]:
        """某时点在册成分 child_subject_id（无前视：区间 [from, to) 含 as_of）。"""
        rows = self._conn.execute(
            """SELECT child_subject_id FROM subject_membership
               WHERE parent_subject_id=? AND relation_kind=?
                 AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
               ORDER BY child_subject_id""",
            (parent_subject_id, relation_kind, as_of, as_of),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def has_membership_asof(
        self, *, parent_subject_id: int, as_of: str,
        relation_kind: str = "sector_constituent",
    ) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM subject_membership
               WHERE parent_subject_id=? AND relation_kind=?
                 AND effective_from<=? AND (effective_to IS NULL OR effective_to>?)
               LIMIT 1""",
            (parent_subject_id, relation_kind, as_of, as_of),
        ).fetchone()
        return row is not None

    def close_absent(
        self, *, parent_subject_id: int, present_child_ids: list[int], effective_to: str,
        relation_kind: str = "sector_constituent",
    ) -> int:
        """把当前在册但本次未出现的成分闭合 effective_to（成分退出）。返回闭合行数。

        无前视要求：退出必须闭合，否则历史 as-of 查询会误纳已退出成分。
        """
        open_rows = self._conn.execute(
            """SELECT membership_id, child_subject_id FROM subject_membership
               WHERE parent_subject_id=? AND relation_kind=? AND effective_to IS NULL""",
            (parent_subject_id, relation_kind),
        ).fetchall()
        present = set(present_child_ids)
        closed = 0
        for mid, child_id in open_rows:
            if int(child_id) not in present:
                self._conn.execute(
                    "UPDATE subject_membership SET effective_to=? WHERE membership_id=?",
                    (effective_to, mid),
                )
                closed += 1
        return closed
