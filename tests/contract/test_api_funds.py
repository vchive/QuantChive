"""T041: /api/funds/{code}/nav 按需查（旁路不入库，note='not persisted'）。"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from quantchive.api import deps
from quantchive.app import create_app
from quantchive.dao.db_init import init_db
from quantchive.datasource.fund_src import FundSource
from quantchive.service.fund_lookup_service import FundLookupService


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._p = payload
        self.status_code = status

    def json(self) -> dict:
        return self._p


def _lsjz_payload(rows: list[dict]) -> dict:
    return {"ErrCode": 0, "Data": {"LSJZList": rows}}


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


def _client(conn, http_get):
    app = create_app()
    app.dependency_overrides[deps.get_fund_lookup_service] = lambda: FundLookupService(
        FundSource(http_get=http_get))
    return TestClient(app)


def test_fund_nav_bypass_not_persisted(conn) -> None:
    rows = [{"FSRQ": "2026-07-02", "DWJZ": "1.5230", "JZZZL": "0.85"},
            {"FSRQ": "2026-07-01", "DWJZ": "1.5100", "JZZZL": "-0.32"}]
    captured = {}

    def get(url, params=None, headers=None, **k):
        captured["referer"] = (headers or {}).get("Referer")
        return _Resp(_lsjz_payload(rows))

    r = _client(conn, get).get("/api/funds/110022/nav", params={"days": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["note"] == "not persisted"          # 旁路，明示不入库
    assert data["fund_code"] == "110022"
    assert len(data["points"]) == 2
    # 净值 Decimal 字符串（不丢精度，禁 float）
    assert data["points"][0]["unit_nav"] == "1.5230"
    assert data["points"][0]["nav_date"] == "2026-07-02"
    # 必带 Referer: fund.eastmoney.com（否则 lsjz -999）
    assert "fund.eastmoney.com" in captured["referer"]


def test_fund_nav_not_written_to_db(conn) -> None:
    """旁路查询不写 observation（物理隔离）。"""
    rows = [{"FSRQ": "2026-07-02", "DWJZ": "1.5230", "JZZZL": "0.85"}]
    _client(conn, lambda *a, **k: _Resp(_lsjz_payload(rows))).get("/api/funds/110022/nav")
    # observation 表无任何基金净值行（旁路不落库）
    n = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    assert n == 0
