"""T034: /api/sectors/{id}/stocks 端到端契约（当日成分五档 + as_of 历史无成分 422）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Sequence

import pytest
from fastapi.testclient import TestClient

from quantchive.api import deps
from quantchive.app import create_app
from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.constituent_dao import ConstituentDao
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import SubjectRef
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)
from quantchive.service.ingest_service import collect_sector_members_once
from quantchive.service.query_service import QueryService


def _member(code, name, main) -> RawObservation:
    d = Decimal
    m = d(main)
    return RawObservation(
        source_symbol=code, display_name=name, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
        main_net=m, super_large_net=m, large_net=d("0"), medium_net=d("0"), small_net=d("0"),
        price=d("11.09"), change_pct=d("2.35"), volume=d("123456"), exchange="SSE",
        source_unit=AmountUnit.YUAN,
    )


class _FakeMemberSource:
    source_id = "fake:members"
    adapter_version = "test"

    def __init__(self, members: list[RawObservation]) -> None:
        self._members = members

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        return self._members

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


def _seed_board(conn) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol="半导体", display_name="半导体", em_board_code="BK0475")
    return sid


def _client(conn):
    app = create_app()
    # FixedClock 使"今天"=2026-07-02 确定（宪章 I 可复现；as_of 历史/当日分类不依赖运行日）
    from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
    clock = FixedClock(datetime(2026, 7, 2, 15, 30, tzinfo=SHANGHAI_TZ))
    app.dependency_overrides[deps.get_query_service] = lambda: QueryService(
        conn, retention_trade_days=7, source_id="fake:members", clock=clock)
    return TestClient(app)


def test_stocks_in_sector_200(conn) -> None:
    board = _seed_board(conn)
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    members = [_member("600519", "贵州茅台", "500000000"),
               _member("000001", "平安银行", "-300000000")]
    collect_sector_members_once(
        sector_subject_id=board, source=_FakeMemberSource(members),
        observation_dao=ObservationDao(conn), subject_dao=SubjectDao(conn),
        constituent_dao=ConstituentDao(conn), run_dao=RunDao(conn), clock=clock)

    r = _client(conn).get(f"/api/sectors/{board}/stocks", params={"sort_by": "main_net"})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "bipolar"
    assert data["total_subjects"] == 2
    assert data["top_inflow"][0]["source_symbol"] == "600519"
    assert data["top_outflow"][0]["source_symbol"] == "000001"
    assert data["top_inflow"][0]["main_net"] == "500000000.00"   # 五档金额字符串


def test_stocks_as_of_history_no_membership_422(conn) -> None:
    board = _seed_board(conn)
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    collect_sector_members_once(
        sector_subject_id=board, source=_FakeMemberSource([_member("600519", "茅台", "1")]),
        observation_dao=ObservationDao(conn), subject_dao=SubjectDao(conn),
        constituent_dao=ConstituentDao(conn), run_dao=RunDao(conn), clock=clock)
    # 查历史日（成分只在 07-02 生效）→ 无成分 → 422 OUT_OF_WINDOW（无前视）
    r = _client(conn).get(f"/api/sectors/{board}/stocks", params={"as_of": "2026-06-01"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "OUT_OF_WINDOW"


def test_stocks_unknown_sector_404(conn) -> None:
    r = _client(conn).get("/api/sectors/99999/stocks")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SUBJECT_NOT_FOUND"
