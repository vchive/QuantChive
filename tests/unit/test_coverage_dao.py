"""T010: coverage_dao——区间合并幂等、缺口计算、含左不含右无前视。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.coverage_dao import CoverageDao
from quantchive.dao.db_init import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _subject(conn) -> int:
    return conn.execute(
        """INSERT INTO subject (asset_class_code, level, subject_kind, source_code,
           source_symbol, display_name, caliber, first_seen_at)
           VALUES ('a_share','instrument','stock','eastmoney','600519','茅台','eastmoney',?)""",
        (datetime.now(timezone.utc).isoformat(),)).lastrowid


_KEY = dict(metric_kind="price_hist", granularity="daily", source_code="baostock")


def test_get_range_none_when_absent(conn) -> None:
    dao = CoverageDao(conn)
    assert dao.get_range(subject_id=_subject(conn), **_KEY) is None


def test_upsert_and_get(conn) -> None:
    dao = CoverageDao(conn)
    sid = _subject(conn)
    dao.upsert_range(subject_id=sid, start_date="2026-06-01", end_date="2026-07-03", **_KEY)
    assert dao.get_range(subject_id=sid, **_KEY) == ("2026-06-01", "2026-07-03")


def test_upsert_merges_union(conn) -> None:
    """二次写入取并集（min start / max end），幂等。"""
    dao = CoverageDao(conn)
    sid = _subject(conn)
    dao.upsert_range(subject_id=sid, start_date="2026-06-10", end_date="2026-06-20", **_KEY)
    dao.upsert_range(subject_id=sid, start_date="2026-06-01", end_date="2026-06-15", **_KEY)  # 向前扩
    assert dao.get_range(subject_id=sid, **_KEY) == ("2026-06-01", "2026-06-20")
    dao.upsert_range(subject_id=sid, start_date="2026-06-15", end_date="2026-07-03", **_KEY)  # 向后扩
    assert dao.get_range(subject_id=sid, **_KEY) == ("2026-06-01", "2026-07-03")
    # 只一行（幂等，不重复）
    n = conn.execute("SELECT COUNT(*) FROM coverage_range").fetchone()[0]
    assert n == 1


def test_missing_gaps_no_existing(conn) -> None:
    dao = CoverageDao(conn)
    sid = _subject(conn)
    assert dao.missing_gaps(subject_id=sid, want_start="2026-06-01", want_end="2026-07-03", **_KEY) \
        == [("2026-06-01", "2026-07-03")]


def test_missing_gaps_both_sides(conn) -> None:
    """已存 [06-10,06-20]，欲 [06-01,07-03] → 缺口前段 [06-01,06-09] + 后段 [06-21,07-03]。"""
    dao = CoverageDao(conn)
    sid = _subject(conn)
    dao.upsert_range(subject_id=sid, start_date="2026-06-10", end_date="2026-06-20", **_KEY)
    gaps = dao.missing_gaps(subject_id=sid, want_start="2026-06-01", want_end="2026-07-03", **_KEY)
    assert gaps == [("2026-06-01", "2026-06-09"), ("2026-06-21", "2026-07-03")]  # 含左不含右无重叠


def test_missing_gaps_fully_covered(conn) -> None:
    """欲区间被已存完全覆盖 → 零缺口（SC-003 二次回填零请求）。"""
    dao = CoverageDao(conn)
    sid = _subject(conn)
    dao.upsert_range(subject_id=sid, start_date="2026-06-01", end_date="2026-07-03", **_KEY)
    assert dao.missing_gaps(subject_id=sid, want_start="2026-06-10", want_end="2026-06-20", **_KEY) == []


def test_missing_gaps_source_isolation(conn) -> None:
    """不同源的区间互不影响（同主体 baostock vs eastmoney 分别记）。"""
    dao = CoverageDao(conn)
    sid = _subject(conn)
    dao.upsert_range(subject_id=sid, metric_kind="price_hist", granularity="daily",
                     source_code="baostock", start_date="2026-06-01", end_date="2026-07-03")
    # 另一源无区间 → 整段缺口
    assert dao.missing_gaps(subject_id=sid, metric_kind="money_flow", granularity="daily",
                            source_code="eastmoney", want_start="2026-06-01",
                            want_end="2026-07-03") == [("2026-06-01", "2026-07-03")]
