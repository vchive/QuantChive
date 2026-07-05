"""T061: /api/health（最新交易日 + 最近运行 + 各源状态）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from quantchive.api import deps
from quantchive.app import create_app
from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass,
    Caliber,
    RunStatus,
    RunType,
    SubjectKind,
    SubjectLevel,
    ValueType,
)
from quantchive.service.query_service import QueryService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


def _client(conn):
    app = create_app()
    app.dependency_overrides[deps.get_query_service] = lambda: QueryService(
        conn, retention_trade_days=7,
        clock=FixedClock(datetime(2026, 7, 2, 16, 0, tzinfo=SHANGHAI_TZ)))
    return TestClient(app)


def test_health_no_data(conn) -> None:
    r = _client(conn).get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "no_data"
    assert data["latest_trade_date"] is None


def test_health_with_data(conn) -> None:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol="半导体", display_name="半导体")
    rid = RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-02", minute_slot="10:30", adapter_version="v2")
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date="2026-07-02", minute_slot="10:30",
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at="t",
        net_amount_cents=100,
        five_tier={"main_net_cents": 100, "super_large_net_cents": 0, "large_net_cents": 0,
                   "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at="t")
    RunDao(conn).finish(rid, status=RunStatus.SUCCESS, subjects_ok=1, subjects_failed=0)

    r = _client(conn).get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["latest_trade_date"] == "2026-07-02"
    assert data["last_run"]["latest"]["source_code"] == "eastmoney"
    assert "eastmoney" in data["last_run"]["per_source_last_ok"]
