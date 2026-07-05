"""板块成分批量采集（collect_all_sector_members）：遍历板块、单板块失败隔离、归属落库。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Sequence

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.constituent_dao import ConstituentDao
from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import DataSourceError, SubjectRef
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)
from quantchive.service.ingest_service import collect_all_sector_members


def _member(code, main) -> RawObservation:
    m = Decimal(main)
    return RawObservation(
        source_symbol=code, display_name=code, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
        main_net=m, super_large_net=m, large_net=Decimal("0"), medium_net=Decimal("0"),
        small_net=Decimal("0"), source_unit=AmountUnit.YUAN)


class _FakeMemberSource:
    """按板块 em_board_code 返回成分；BK_FAIL 抛错（测隔离）。"""
    source_id = "fake:members"
    adapter_version = "t"

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        if parent.em_board_code == "BK_FAIL":
            raise DataSourceError("限流", error_type="rate_limited")
        return [_member(f"{parent.em_board_code}_S1", "100"),
                _member(f"{parent.em_board_code}_S2", "-50")]

    def capability(self, spec): ...
    def fetch_subjects(self, spec): return []
    def fetch_observations(self, spec): return []
    def fetch_daily_final(self, spec, trade_date): return []


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


def _board(conn, name, code) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol=name, display_name=name, em_board_code=code)
    return sid


def test_collect_all_members_records_memberships(conn) -> None:
    b1 = _board(conn, "半导体", "BK0475")
    _board(conn, "白酒", "BK0476")
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    summary = collect_all_sector_members(conn, source=_FakeMemberSource(),
                                         adapter_version="t", clock=clock)
    assert summary["boards_ok"] == 2 and summary["boards_failed"] == 0
    assert summary["members_written"] == 4              # 2 板块 × 2 成分
    # 归属落库：半导体成分 as-of 可查
    members = ConstituentDao(conn).members_asof(parent_subject_id=b1, as_of="2026-07-02")
    assert len(members) == 2


def test_collect_all_members_isolates_board_failure(conn) -> None:
    _board(conn, "半导体", "BK0475")
    _board(conn, "坏板块", "BK_FAIL")     # fetch_members 抛错
    _board(conn, "白酒", "BK0476")
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    summary = collect_all_sector_members(conn, source=_FakeMemberSource(),
                                         adapter_version="t", clock=clock)
    # 坏板块失败不阻塞其他两个
    assert summary["boards_ok"] == 2 and summary["boards_failed"] == 1
    assert summary["members_written"] == 4


def test_collect_all_members_skips_boards_without_code(conn) -> None:
    _board(conn, "半导体", "BK0475")
    # 无 em_board_code 的板块不参与
    SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.CONCEPT, source_code="eastmoney",
        source_symbol="无代码概念", display_name="无代码概念")
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    summary = collect_all_sector_members(conn, source=_FakeMemberSource(),
                                         adapter_version="t", clock=clock)
    assert summary["boards_total"] == 1                 # 仅有 em_board_code 的板块
