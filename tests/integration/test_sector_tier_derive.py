"""T033-T035: 板块四档派生（成分股求和,net+gross,覆盖率门禁,宁缺勿假,无前视）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.constituent_dao import ConstituentDao
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
from quantchive.service.ingest_service import derive_sector_tiers
from quantchive.service.market_aggregate import aggregate_sector_tiers


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


def _sector(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol=sym, display_name=name)
    return sid


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-03",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _write_stock(conn, sid, rid, main, sl_net, sl_gross, with_gross=True):
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": main, "super_large_net_cents": sl_net,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        four_gross=({"super_large_gross_cents": sl_gross, "large_gross_cents": 0,
                     "medium_gross_cents": 0, "small_gross_cents": 0} if with_gross else None),
        source_unit="yuan", ingestion_run_id=rid, created_at=now)


def _member(conn, parent, child):
    ConstituentDao(conn).record(
        parent_subject_id=parent, child_subject_id=child,
        relation_kind="sector_constituent", effective_from="2020-01-01",
        source_code="eastmoney", ingestion_run_id=_run(conn),
        created_at=datetime.now(timezone.utc).isoformat())


# ---------- 求和引擎单测 ----------

def test_aggregate_sums_net_and_gross(conn) -> None:
    rid = _run(conn)
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    _write_stock(conn, s1, rid, 100, 100, 2200)
    _write_stock(conn, s2, rid, 200, 200, 400)
    rows = [ObservationDao(conn).get_point(subject_id=s, trade_date="2026-07-03",
            value_type=ValueType.DAILY_FINAL, minute_slot="EOD", source_code="sina_flow")
            for s in (s1, s2)]
    agg = aggregate_sector_tiers(rows, expected_count=2, threshold=0.95)
    assert agg.written
    assert agg.five_tier_cents["main_net_cents"] == 300          # 100+200
    assert agg.five_tier_cents["super_large_net_cents"] == 300
    assert agg.four_gross_cents["super_large_gross_cents"] == 2600  # 2200+400


def test_aggregate_coverage_gate(conn) -> None:
    """覆盖率不足 → 不落（宁缺勿假）。"""
    rid = _run(conn)
    s1 = _stock(conn, "600001", "甲")
    _write_stock(conn, s1, rid, 100, 100, 200)
    rows = [ObservationDao(conn).get_point(subject_id=s1, trade_date="2026-07-03",
            value_type=ValueType.DAILY_FINAL, minute_slot="EOD", source_code="sina_flow")]
    agg = aggregate_sector_tiers(rows, expected_count=10, threshold=0.95)  # 1/10=10%
    assert not agg.written and "覆盖率" in agg.reason


def test_aggregate_gross_none_when_member_lacks(conn) -> None:
    """任一成分缺 gross → four_gross=None（不落部分假 gross）。"""
    rid = _run(conn)
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    _write_stock(conn, s1, rid, 100, 100, 2200, with_gross=True)
    _write_stock(conn, s2, rid, 200, 200, 0, with_gross=False)   # 无 gross
    rows = [ObservationDao(conn).get_point(subject_id=s, trade_date="2026-07-03",
            value_type=ValueType.DAILY_FINAL, minute_slot="EOD", source_code="sina_flow")
            for s in (s1, s2)]
    agg = aggregate_sector_tiers(rows, expected_count=2, threshold=0.95)
    assert agg.written and agg.four_gross_cents is None          # net 有,gross 诚实置空
    assert agg.five_tier_cents["main_net_cents"] == 300


# ---------- 派生落库端到端 ----------

def test_derive_sector_tiers_writes_derived_row(conn) -> None:
    rid = _run(conn)
    steel = _sector(conn, "BK钢铁", "钢铁")
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    _write_stock(conn, s1, rid, 100, 100, 2200)
    _write_stock(conn, s2, rid, 200, 200, 400)
    _member(conn, steel, s1); _member(conn, steel, s2)
    res = derive_sector_tiers(conn, trade_date="2026-07-03", threshold=0.95)
    assert res["sectors_written"] == 1
    # 派生行落库:is_derived、四档 gross 求和
    row = ObservationDao(conn).get_point(
        subject_id=steel, trade_date="2026-07-03", value_type=ValueType.DAILY_FINAL,
        minute_slot="EOD", source_code="sina_flow")
    assert row is not None and row.is_derived == 1
    assert row.main_net_cents == 300
    assert row.super_large_gross_cents == 2600
