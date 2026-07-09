"""S1-S4: 信号扫描预计算(scan_and_store 只落命中日 + list_hits 按强度排序)。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone, date as _date

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
from quantchive.service.scan_service import list_hits, scan_and_store


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SSE")
    return sid


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-06-01",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _series(conn, sid, rid, prices, mains):
    """按日写 sina 行:prices/mains 等长,日期连续。"""
    now = datetime.now(timezone.utc).isoformat()
    base = _date(2026, 6, 1)
    for i, (p, m) in enumerate(zip(prices, mains)):
        d = (base + timedelta(days=i)).isoformat()
        ObservationDao(conn).upsert(
            subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
            value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
            five_tier={"main_net_cents": m, "super_large_net_cents": 0,
                       "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
            price_micro=p * 1_000_000, change_pct_bp=0, source_unit="yuan",
            ingestion_run_id=rid, created_at=now)


def test_scan_stores_hit_on_target_day(conn) -> None:
    """末日价跌+主力累计净流入 → 吸筹命中,落 signal_hit。"""
    rid = _run(conn)
    s1 = _stock(conn, "600001", "吸筹股")
    # 6日价跌100→95,主力全程净流入 → 末日吸筹
    _series(conn, s1, rid, [100, 99, 98, 97, 96, 95], [10_00] * 6)
    last_day = (_date(2026, 6, 1) + timedelta(days=5)).isoformat()
    res = scan_and_store(conn, trade_date=last_day, window_days=30)
    assert res["hits"] >= 1
    row = conn.execute(
        "SELECT kind, strength FROM signal_hit WHERE subject_id=? AND kind='accumulation'",
        (s1,)).fetchone()
    assert row is not None and row["strength"] == 60_00   # 6点×10元累计


def test_scan_no_hit_when_not_triggered(conn) -> None:
    """价涨+主力流入(同向非背离) → 不落吸筹。"""
    rid = _run(conn)
    s1 = _stock(conn, "600002", "无信号股")
    _series(conn, s1, rid, [95, 96, 97, 98, 99, 100], [10_00] * 6)
    last_day = (_date(2026, 6, 1) + timedelta(days=5)).isoformat()
    scan_and_store(conn, trade_date=last_day)
    row = conn.execute(
        "SELECT 1 FROM signal_hit WHERE subject_id=? AND kind='accumulation'", (s1,)).fetchone()
    assert row is None


def test_list_hits_sorted_by_strength(conn) -> None:
    """list_hits 按强度降序 + top_n。"""
    rid = _run(conn)
    # 两只吸筹股,强度不同(main净额不同)
    s1 = _stock(conn, "600001", "强吸筹")
    s2 = _stock(conn, "600002", "弱吸筹")
    _series(conn, s1, rid, [100, 99, 98, 97, 96, 95], [50_00] * 6)  # 强
    _series(conn, s2, rid, [100, 99, 98, 97, 96, 95], [10_00] * 6)  # 弱
    last_day = (_date(2026, 6, 1) + timedelta(days=5)).isoformat()
    scan_and_store(conn, trade_date=last_day)
    res = list_hits(conn, kind="accumulation", trade_date=last_day, top_n=10)
    names = [r["display_name"] for r in res["rows"]]
    assert names[0] == "强吸筹"       # 强度高的在前
    assert res["rows"][0]["price"] is not None   # 带价展示
    assert last_day in res["available_dates"]


def test_scan_endpoint(conn) -> None:
    """/api/signals/scan 端点返命中股。"""
    rid = _run(conn)
    s1 = _stock(conn, "600001", "吸筹股")
    _series(conn, s1, rid, [100, 99, 98, 97, 96, 95], [10_00] * 6)
    last_day = (_date(2026, 6, 1) + timedelta(days=5)).isoformat()
    scan_and_store(conn, trade_date=last_day)

    from fastapi.testclient import TestClient
    from quantchive.app import create_app
    from quantchive.api.deps import get_conn
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    c = TestClient(app)
    r = c.get(f"/api/signals/scan?kind=accumulation&trade_date={last_day}")
    assert r.status_code == 200
    j = r.json()
    assert j["kind"] == "accumulation" and len(j["rows"]) >= 1
