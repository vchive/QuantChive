"""coverage_range DAO（spec003 D5，已存区间 → 增量回填/缺口检测）。

仿 vnpy BarOverview：记「主体×指标×粒度×源」已入库的连续主区间，回填前查缺口、
回填后合并区间。快速预判用本表；权威缺日以 observation×trade_calendar 交集为准
（data-model 简化决策）。无前视：区间只增不覆盖已存观测。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

_ONE_DAY = timedelta(days=1)


class CoverageDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_range(
        self, *, subject_id: int, metric_kind: str, granularity: str, source_code: str
    ) -> tuple[str, str] | None:
        """返回 (start_date, end_date) 或 None（未记录）。"""
        row = self._conn.execute(
            """SELECT start_date, end_date FROM coverage_range
               WHERE subject_id=? AND metric_kind=? AND granularity=? AND source_code=?""",
            (subject_id, metric_kind, granularity, source_code),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def upsert_range(
        self, *, subject_id: int, metric_kind: str, granularity: str, source_code: str,
        start_date: str, end_date: str,
    ) -> None:
        """合并写入已存区间：与旧区间取并（min start / max end），幂等。"""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_range(
            subject_id=subject_id, metric_kind=metric_kind,
            granularity=granularity, source_code=source_code)
        if existing is not None:
            start_date = min(start_date, existing[0])
            end_date = max(end_date, existing[1])
        self._conn.execute(
            """INSERT INTO coverage_range
               (subject_id, metric_kind, granularity, source_code, start_date, end_date, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(subject_id, metric_kind, granularity, source_code) DO UPDATE SET
                 start_date=excluded.start_date, end_date=excluded.end_date, updated_at=excluded.updated_at""",
            (subject_id, metric_kind, granularity, source_code, start_date, end_date, now),
        )

    def missing_gaps(
        self, *, subject_id: int, metric_kind: str, granularity: str, source_code: str,
        want_start: str, want_end: str,
    ) -> list[tuple[str, str]]:
        """欲回填 [want_start, want_end]，返回不在已存区间的缺口子区间列表。

        快速预判：仅按 coverage_range 主区间切分（已存区间前/后两侧的缺口）。
        - 无已存 → 整段是缺口。
        - 已存 [s,e]：缺口 = [want_start, s-1]（若 want_start<s）+ [e+1, want_end]（若 want_end>e）。
        权威缺日（区间内的洞）由调用方用 observation×calendar 兜底（data-model 简化决策）。
        """
        if want_start > want_end:
            return []
        existing = self.get_range(
            subject_id=subject_id, metric_kind=metric_kind,
            granularity=granularity, source_code=source_code)
        if existing is None:
            return [(want_start, want_end)]
        s, e = existing
        gaps: list[tuple[str, str]] = []
        if want_start < s:
            gaps.append((want_start, min(want_end, _prev_day(s))))
        if want_end > e:
            gaps.append((max(want_start, _next_day(e)), want_end))
        return [g for g in gaps if g[0] <= g[1]]


def _prev_day(d: str) -> str:
    return (date.fromisoformat(d) - _ONE_DAY).isoformat()


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + _ONE_DAY).isoformat()

