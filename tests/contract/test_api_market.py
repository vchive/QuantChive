"""T026: 契约 —— /api/market/overview 端到端（大盘求和 + 覆盖率 + 金额字符串 + 404）。"""

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
from quantchive.service.ingest_service import collect_stock_observations_once
from quantchive.service.market_aggregate import MarketAggregator
from quantchive.service.query_service import QueryService
from tests.integration.test_market_sum import _FakeStockSource, _stock_obs


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


def _client(conn):
    app = create_app()
    app.dependency_overrides[deps.get_query_service] = lambda: QueryService(
        conn, retention_trade_days=7, source_id="fake:stock")
    return TestClient(app)


def test_market_overview_200(conn) -> None:
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    obs = [_stock_obs("600519", "贵州茅台", "500000000"),
           _stock_obs("000001", "平安银行", "-300000000")]
    collect_stock_observations_once(
        source=_FakeStockSource(obs, total=2), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn),
        clock=clock)

    r = _client(conn).get("/api/market/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["trade_date"] == "2026-07-02"
    assert data["constituent_count"] == 2 and data["expected_count"] == 2
    assert data["coverage_pct"] == "100.00"
    # 大盘 main_net = 5亿-3亿 = 2亿 → 字符串（SC-006 前端零 float）
    assert isinstance(data["main_net"], str)
    assert data["main_net"] == "200000000.00"
    assert data["is_derived"] is True
    assert data["provenance"]["source_id"] == "fake:stock"


def test_market_overview_404_when_no_data(conn) -> None:
    r = _client(conn).get("/api/market/overview")
    assert r.status_code == 404
    # 空库/门禁未落 → NO_DATA_FOR_DATE，message 含真实原因（非裸 SUBJECT_NOT_FOUND）
    assert r.json()["error"]["code"] == "NO_DATA_FOR_DATE"
