"""T048: /api/subjects/{id}/series（断点 null + 超保留窗 422 + 排除 LATEST）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from quantchive.api import deps
from quantchive.app import create_app
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
from quantchive.service.query_service import QueryService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    for td in ["2026-07-02"]:
        c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (td,))
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subject(conn, symbol="600519") -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol=symbol, display_name="贵州茅台")
    return sid


def _run(conn, td="2026-07-02") -> int:
    return RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date=td, minute_slot="10:00", adapter_version="test")


def _write(conn, sid, rid, td, slot, main, *, value_type=ValueType.INTRADAY_SNAPSHOT):
    kw = dict(
        subject_id=sid, source_code="eastmoney", trade_date=td, minute_slot=slot,
        value_type=value_type, granularity="1min", observed_at=_now(),
        source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    if main is None:
        # 无五档，仅价（该指标断点：main_net 为 null）
        kw["price_micro"] = 1_000_000
    else:
        kw["net_amount_cents"] = main
        kw["five_tier"] = {"main_net_cents": main, "super_large_net_cents": 0,
                           "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0}
    ObservationDao(conn).upsert(**kw)


def _client(conn):
    app = create_app()
    # 注入 FixedClock 使"今天"确定（宪章 I 可复现，不依赖运行时真实日期）
    from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
    clock = FixedClock(datetime(2026, 7, 2, 15, 30, tzinfo=SHANGHAI_TZ))
    app.dependency_overrides[deps.get_query_service] = lambda: QueryService(
        conn, retention_trade_days=30, source_id="fake:series", clock=clock)
    return TestClient(app)


def test_series_200_with_breakpoint(conn) -> None:
    sid, rid = _subject(conn), _run(conn)
    _write(conn, sid, rid, "2026-07-02", "09:31", 100)
    _write(conn, sid, rid, "2026-07-02", "09:32", None)   # main_net 缺 → 断点
    _write(conn, sid, rid, "2026-07-02", "09:33", 300)
    r = _client(conn).get(f"/api/subjects/{sid}/series", params={"metric": "main_net"})
    assert r.status_code == 200
    data = r.json()
    assert data["metric"] == "main_net"
    assert [p["ts"] for p in data["points"]] == ["09:31", "09:32", "09:33"]
    assert data["points"][0]["value"] == "1.00"          # 100 分 = 1.00 元
    assert data["points"][1]["value"] is None            # 断点 null（前端不连线）
    assert data["points"][2]["value"] == "3.00"          # 300 分 = 3.00 元
    assert data["gap_count"] == 1


def test_series_excludes_latest_row(conn) -> None:
    """intraday_latest / 'LATEST' 覆盖行不入序列（防字符串排序假点）。"""
    sid, rid = _subject(conn), _run(conn)
    _write(conn, sid, rid, "2026-07-02", "09:31", 100)
    _write(conn, sid, rid, "2026-07-02", "LATEST", 999, value_type=ValueType.INTRADAY_LATEST)
    r = _client(conn).get(f"/api/subjects/{sid}/series", params={"metric": "main_net"})
    slots = [p["ts"] for p in r.json()["points"]]
    assert slots == ["09:31"]                             # 无 'LATEST'


def test_series_out_of_window_422(conn) -> None:
    sid = _subject(conn)
    # 写一个超 30 天的历史日（trade_calendar 里也补上以便 today 解析）
    conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-05-01', 1)")
    rid = _run(conn, td="2026-05-01")
    _write(conn, sid, rid, "2026-05-01", "09:31", 100)
    # 最新交易日 07-02，05-01 距今 > 30 天 → OUT_OF_WINDOW
    r = _client(conn).get(f"/api/subjects/{sid}/series",
                          params={"metric": "main_net", "trade_date": "2026-05-01"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "OUT_OF_WINDOW"


def test_series_unknown_subject_404(conn) -> None:
    r = _client(conn).get("/api/subjects/99999/series")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SUBJECT_NOT_FOUND"
