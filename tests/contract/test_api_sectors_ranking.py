"""/api/sectors/ranking 端到端契约（通用模型 + 能力隔离 422）。"""

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
from quantchive.models.enums import Caliber, SectorType
from quantchive.service.ingest_service import (
    CollectRequest,
    collect_sector_observations_once,
)
from quantchive.service.query_service import QueryService
from tests._fakes import FakeSectorObservationSource


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


@pytest.fixture
def client(conn):
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    req = CollectRequest(caliber=Caliber.EASTMONEY, source_code="eastmoney",
                         sector_types=(SectorType.INDUSTRY,), adapter_version="test")
    collect_sector_observations_once(
        req, source=FakeSectorObservationSource(), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn), clock=clock)
    app = create_app()
    app.dependency_overrides[deps.get_query_service] = lambda: QueryService(
        conn, retention_trade_days=7, source_id="fake:eastmoney", clock=clock)
    return TestClient(app)


def test_observations_ranking_200_bipolar(client) -> None:
    r = client.get("/api/sectors/ranking",
                   params={"caliber": "eastmoney", "sector_type": "industry",
                           "sort_by": "main_net", "top_n": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "bipolar"
    assert data["total_subjects"] == 4
    assert [i["source_symbol"] for i in data["top_inflow"]] == ["半导体", "白酒"]
    assert data["top_outflow"][0]["source_symbol"] == "银行"      # 最负在前
    assert data["provenance"]["source_id"] == "fake:eastmoney"    # 真实来源不谎报


def test_observations_ranking_amount_is_string(client) -> None:
    r = client.get("/api/sectors/ranking",
                   params={"caliber": "eastmoney", "sector_type": "industry"})
    item = r.json()["top_inflow"][0]
    assert isinstance(item["main_net"], str)                      # SC-006 金额字符串
    assert item["main_net"] == "500000000.00"


def test_observations_ranking_price_sort_422(client) -> None:
    r = client.get("/api/sectors/ranking",
                   params={"caliber": "eastmoney", "sector_type": "industry", "sort_by": "price"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_METRIC"
