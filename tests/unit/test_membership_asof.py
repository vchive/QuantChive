"""T035: subject_membership as-of 查询无前视（ConstituentDao）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.constituent_dao import ConstituentDao
from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mk(conn, symbol, level, kind) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=level, subject_kind=kind,
        source_code="eastmoney", source_symbol=symbol, display_name=symbol)
    return sid


def test_members_asof_window(conn) -> None:
    dao = ConstituentDao(conn)
    board = _mk(conn, "半导体", SubjectLevel.SECTOR, SubjectKind.INDUSTRY)
    a = _mk(conn, "600519", SubjectLevel.INSTRUMENT, SubjectKind.STOCK)
    dao.record(parent_subject_id=board, child_subject_id=a, effective_from="2026-07-02",
               source_code="eastmoney", ingestion_run_id=None, created_at=_now())
    # 生效日当天及之后在册
    assert dao.members_asof(parent_subject_id=board, as_of="2026-07-02") == [a]
    assert dao.members_asof(parent_subject_id=board, as_of="2026-07-10") == [a]
    # 生效日之前不在册（无前视，不能提前看到未来成分）
    assert dao.members_asof(parent_subject_id=board, as_of="2026-07-01") == []


def test_has_membership_asof(conn) -> None:
    dao = ConstituentDao(conn)
    board = _mk(conn, "白酒", SubjectLevel.SECTOR, SubjectKind.INDUSTRY)
    a = _mk(conn, "000858", SubjectLevel.INSTRUMENT, SubjectKind.STOCK)
    dao.record(parent_subject_id=board, child_subject_id=a, effective_from="2026-07-02",
               source_code="eastmoney", ingestion_run_id=None, created_at=_now())
    assert dao.has_membership_asof(parent_subject_id=board, as_of="2026-07-02") is True
    assert dao.has_membership_asof(parent_subject_id=board, as_of="2026-06-01") is False


def test_close_absent_exits_membership(conn) -> None:
    """成分退出必须闭合 effective_to：闭合后历史 as-of 不再纳入。"""
    dao = ConstituentDao(conn)
    board = _mk(conn, "券商", SubjectLevel.SECTOR, SubjectKind.INDUSTRY)
    a = _mk(conn, "600030", SubjectLevel.INSTRUMENT, SubjectKind.STOCK)
    b = _mk(conn, "601688", SubjectLevel.INSTRUMENT, SubjectKind.STOCK)
    dao.record(parent_subject_id=board, child_subject_id=a, effective_from="2026-07-01",
               source_code="eastmoney", ingestion_run_id=None, created_at=_now())
    dao.record(parent_subject_id=board, child_subject_id=b, effective_from="2026-07-01",
               source_code="eastmoney", ingestion_run_id=None, created_at=_now())
    # 07-02 只剩 a（b 退出）→ 闭合 b 于 07-02
    closed = dao.close_absent(parent_subject_id=board, present_child_ids=[a],
                              effective_to="2026-07-02")
    assert closed == 1
    # 07-02 起 b 不在册；07-01 仍在册（区间 [from, to) 含左不含右）
    assert dao.members_asof(parent_subject_id=board, as_of="2026-07-02") == [a]
    assert set(dao.members_asof(parent_subject_id=board, as_of="2026-07-01")) == {a, b}


def test_record_idempotent(conn) -> None:
    dao = ConstituentDao(conn)
    board = _mk(conn, "银行", SubjectLevel.SECTOR, SubjectKind.INDUSTRY)
    a = _mk(conn, "601398", SubjectLevel.INSTRUMENT, SubjectKind.STOCK)
    for _ in range(3):
        dao.record(parent_subject_id=board, child_subject_id=a, effective_from="2026-07-02",
                   source_code="eastmoney", ingestion_run_id=None, created_at=_now())
    n = conn.execute("SELECT COUNT(*) FROM subject_membership").fetchone()[0]
    assert n == 1  # 幂等，不重复插
