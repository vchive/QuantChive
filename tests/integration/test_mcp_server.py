"""MCP server tools 单测：对 temp 播种 DB 调 tool 函数，断言返 JSON dict + 错误透传 + 注册数。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from quantchive.core.settings import get_settings
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
from quantchive import mcp_server


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """临时 DB 文件 + 播种数据；把 settings.db_path 指过去，让 tool 的 _readonly_conn 读它。"""
    db = tmp_path / "mcp_test.db"
    from quantchive.core.db import connect
    conn = connect(db)
    init_db(conn)
    for d in ["2026-07-01", "2026-07-02", "2026-07-03"]:
        conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    sd = SubjectDao(conn)
    s1, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                      subject_kind=SubjectKind.STOCK, source_code="sina_flow",
                      source_symbol="600001", display_name="甲", exchange="SSE")
    s2, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                      subject_kind=SubjectKind.STOCK, source_code="sina_flow",
                      source_symbol="600002", display_name="乙", exchange="SSE")
    rid = RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                             caliber=Caliber.EASTMONEY, trade_date="2026-07-03", minute_slot="EOD",
                             adapter_version="t", subject_scope="x", asset_class_code="a_share")
    now = datetime.now(timezone.utc).isoformat()
    for sid, base in [(s1, 10000), (s2, 20000)]:
        for i, d in enumerate(["2026-07-01", "2026-07-02", "2026-07-03"]):
            ObservationDao(conn).upsert(
                subject_id=sid, source_code="sina_flow", trade_date=d, minute_slot="EOD",
                value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
                five_tier={"main_net_cents": base * (i + 1), "super_large_net_cents": base * (i + 1),
                           "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
                source_unit="yuan", ingestion_run_id=rid, created_at=now)
    conn.close()
    # 指向临时库 + 清 settings 缓存
    monkeypatch.setenv("QUANTCHIVE_DB_PATH", str(db))
    get_settings.cache_clear()
    yield {"s1": s1, "s2": s2}
    get_settings.cache_clear()


def test_tools_registered() -> None:
    """17 个工具全注册。"""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert len(tools) == 17
    names = {t.name for t in tools}
    assert "scan_market_stocks" in names and "get_series_range" in names
    assert "search_subject" in names and "signal_backtest" in names
    assert "describe_fundamentals" in names


def test_list_subjects_tool(seeded_db) -> None:
    res = mcp_server.list_subjects(asset_class="a_share", subject_kind="stock")
    assert isinstance(res, list) and len(res) == 2
    assert {r["display_name"] for r in res} == {"甲", "乙"}


def test_series_range_tool_json(seeded_db) -> None:
    """区间取数返 JSON dict，金额是字符串（不丢精度）。"""
    res = mcp_server.get_series_range(
        subject_id=seeded_db["s1"], start_date="2026-07-01", end_date="2026-07-03", metric="main_net")
    assert "error" not in res
    assert len(res["points"]) == 3
    assert res["points"][0]["value"] == "100.00"        # 10000分=100元，字符串
    assert res["provenance"]["trade_date"] == "2026-07-03"


def test_series_range_no_lookahead(seeded_db) -> None:
    """end_date 截断防前视。"""
    res = mcp_server.get_series_range(
        subject_id=seeded_db["s1"], start_date="2026-07-01", end_date="2026-07-02", metric="main_net")
    assert [p["ts"] for p in res["points"]] == ["2026-07-01", "2026-07-02"]


def test_series_batch_tool(seeded_db) -> None:
    res = mcp_server.get_series_batch(
        subject_ids=[seeded_db["s1"], seeded_db["s2"]],
        start_date="2026-07-01", end_date="2026-07-03", metric="main_net")
    assert "error" not in res and len(res["series"]) == 2


def test_error_passthrough(seeded_db) -> None:
    """不存在主体 → error 字段透传（不静默、不抛裸异常）。"""
    res = mcp_server.get_series_range(
        subject_id=999999, start_date="2026-07-01", end_date="2026-07-03", metric="main_net")
    assert "error" in res and res["error"]["code"]


def test_search_subject_tool(seeded_db) -> None:
    """search_subject 按名字找 subject_id。"""
    res = mcp_server.search_subject("甲")
    assert isinstance(res, list) and any(r["display_name"] == "甲" for r in res)
