"""T009/T010/T011: 多档对齐时序端点 + 流入流出 + 全程累计 + 简单方向背离（spec005 US1）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass,
    Caliber,
    RunType,
    SubjectKind,
    SubjectLevel,
    ValueType,
)
from quantchive.service.flow_query_service import FlowQueryService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _stock(conn) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol="600150", display_name="中国船舶", exchange="SSE")
    return sid


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-04",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _write(conn, sid, rid, date, *, main, price, sl_net, sl_gross):
    """写一天四档（超大档给 net+gross，其余档给最小 net 满足五档全有）。"""
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date=date, minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": main, "super_large_net_cents": sl_net,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        four_gross={"super_large_gross_cents": sl_gross, "large_gross_cents": 0,
                    "medium_gross_cents": 0, "small_gross_cents": 0},
        price_micro=price, ingestion_run_id=rid, source_unit="yuan", created_at=now)


def test_tiers_series_returns_aligned_with_inflow_outflow(conn) -> None:
    sid, rid = _stock(conn), _run(conn)
    # 超大单：gross=2200、net=-400 → 流入900、流出1300
    _write(conn, sid, rid, "2026-07-01", main=-400, price=8_000_000, sl_net=-400, sl_gross=2200)
    svc = FlowQueryService(conn, retention_trade_days=90)
    res = svc.get_tiers_series(subject_id=sid, granularity="daily")
    assert res.has_gross is True and len(res.points) == 1
    p = res.points[0]
    assert p.super_large.net == "-4.00"           # -400分=-4元
    assert p.super_large.inflow == "9.00"          # (2200-400)/2=900分=9元
    assert p.super_large.outflow == "13.00"        # (2200+400)/2=1300分=13元
    assert p.main_net == "-4.00" and p.cum_main_net == "-4.00"


def test_cumulative_全程绝对累计(conn) -> None:
    """cum_main_net 从最早日累加。"""
    sid, rid = _stock(conn), _run(conn)
    _write(conn, sid, rid, "2026-07-01", main=100, price=8_000_000, sl_net=100, sl_gross=200)
    _write(conn, sid, rid, "2026-07-02", main=200, price=8_100_000, sl_net=200, sl_gross=400)
    _write(conn, sid, rid, "2026-07-03", main=-50, price=8_050_000, sl_net=-50, sl_gross=150)
    res = FlowQueryService(conn, retention_trade_days=90).get_tiers_series(subject_id=sid)
    cums = [p.cum_main_net for p in res.points]
    assert cums == ["1.00", "3.00", "2.50"]       # 100 / 100+200 / 300-50 分 → 元


def test_divergence_accumulation(conn) -> None:
    """价跌 + 主力累计净升 → 标记吸筹。"""
    sid, rid = _stock(conn), _run(conn)
    # 价从 8.0 跌到 7.5，主力天天净流入 → 累计升
    _write(conn, sid, rid, "2026-07-01", main=100, price=8_000_000, sl_net=100, sl_gross=200)
    _write(conn, sid, rid, "2026-07-02", main=100, price=7_800_000, sl_net=100, sl_gross=200)
    _write(conn, sid, rid, "2026-07-03", main=100, price=7_500_000, sl_net=100, sl_gross=200)
    res = FlowQueryService(conn, retention_trade_days=90).get_tiers_series(subject_id=sid)
    assert len(res.divergence) == 1
    assert res.divergence[0].kind == "accumulation"   # 吸筹


def test_no_data_raises(conn) -> None:
    sid = _stock(conn)
    from quantchive.service.errors import NoDataForDate
    with pytest.raises(NoDataForDate):
        FlowQueryService(conn, retention_trade_days=90).get_tiers_series(subject_id=sid)


def test_api_endpoint(conn) -> None:
    """端到端：GET /api/subjects/{id}/tiers_series。"""
    from fastapi.testclient import TestClient
    from quantchive.app import create_app
    from quantchive.api.deps import get_conn
    sid, rid = _stock(conn), _run(conn)
    _write(conn, sid, rid, "2026-07-01", main=-400, price=8_000_000, sl_net=-400, sl_gross=2200)
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    r = TestClient(app).get(f"/api/subjects/{sid}/tiers_series?granularity=daily")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["has_gross"] is True
    assert j["points"][0]["super_large"]["inflow"] == "9.00"
