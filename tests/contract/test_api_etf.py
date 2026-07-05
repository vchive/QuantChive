"""T040: /api/etf/ranking 端到端（单榜 unipolar + 能力自描述 + 五档排序 422）。"""

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
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import FetchSpec
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)
from quantchive.service.ingest_service import collect_etf_observations_once
from quantchive.service.query_service import QueryService


def _etf(code, name, change) -> RawObservation:
    d = Decimal
    return RawObservation(
        source_symbol=code, display_name=name, asset_class=AssetClass.FUND_ETF,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.ETF, caliber=Caliber.EASTMONEY,
        price=d("1.109"), change_pct=d(change), volume=d("123456"), turnover=d("9990000"),
        metrics={"circ_mktcap": d("8500000000")}, source_unit=AmountUnit.YUAN,
    )


class _FakeEtfSource:
    source_id = "fake:etf"
    adapter_version = "test"

    def __init__(self, etfs) -> None:
        self._etfs = etfs
        self.last_total = len(etfs)

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        return self._etfs

    def capability(self, spec): ...
    def fetch_subjects(self, spec): return []
    def fetch_members(self, parent): return []
    def fetch_daily_final(self, spec, trade_date): return self._etfs


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
        conn, retention_trade_days=7, source_id="fake:etf")
    return TestClient(app)


def _load(conn):
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    etfs = [_etf("512880", "证券ETF", "2.35"), _etf("510300", "沪深300ETF", "-1.20"),
            _etf("159915", "创业板ETF", "3.80")]
    collect_etf_observations_once(
        source=_FakeEtfSource(etfs), observation_dao=ObservationDao(conn),
        subject_dao=SubjectDao(conn), run_dao=RunDao(conn), clock=clock)


def test_etf_ranking_unipolar_200(conn) -> None:
    _load(conn)
    r = _client(conn).get("/api/etf/ranking", params={"sort_by": "change_pct", "top_n": 3})
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "unipolar"                 # 单榜，无双榜
    assert data["has_five_tier"] is False
    # 按涨跌幅降序：创业板(3.80) > 证券(2.35) > 沪深300(-1.20)
    names = [i["source_symbol"] for i in data["ranked_items"]]
    assert names == ["159915", "512880", "510300"]
    assert data["ranked_items"][0]["change_pct"] == "3.8"     # 380bp→3.8%（字符串）
    assert not data["top_inflow"] and not data["top_outflow"]  # 单榜无双榜字段


def test_etf_five_tier_sort_422(conn) -> None:
    _load(conn)
    r = _client(conn).get("/api/etf/ranking", params={"sort_by": "main_net"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_METRIC"


def test_etf_capability_endpoint(conn) -> None:
    r = _client(conn).get("/api/meta/capability",
                          params={"asset_class": "fund_etf", "subject_kind": "etf"})
    assert r.status_code == 200
    cap = r.json()
    assert cap["has_five_tier"] is False
    assert "circ_mktcap" in cap["supported_metrics"]
    assert "main_net" not in cap["supported_metrics"]         # 诚实：ETF 无五档
    assert "change_pct" in cap["available_sort_fields"]


def test_meta_subjects_endpoint(conn) -> None:
    _load(conn)
    r = _client(conn).get("/api/meta/subjects",
                          params={"asset_class": "fund_etf", "subject_kind": "etf"})
    assert r.status_code == 200
    subs = r.json()
    assert len(subs) == 3
    assert {s["source_symbol"] for s in subs} == {"512880", "510300", "159915"}
    assert all(s["asset_class"] == "fund_etf" for s in subs)
