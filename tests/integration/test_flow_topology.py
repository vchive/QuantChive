"""T: 资金流向拓扑守恒 + 负流量方向（spec006）。mock 数据不依赖真实 DB。"""

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
from quantchive.service.flow_topology_service import FlowTopologyService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _sector(conn, name, size_hint) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol=f"BK{name}", display_name=name)
    return sid


def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SSE")
    return sid


def _membership(conn, parent, child, run_id):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO subject_membership
           (parent_subject_id, child_subject_id, relation_kind, effective_from,
            source_code, ingestion_run_id, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (parent, child, "sector_constituent", "2020-01-01",
         "eastmoney", run_id, now))


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-03",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _write_flow(conn, sid, main, run_id):
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": main, "super_large_net_cents": main,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        price_micro=8_000_000, ingestion_run_id=run_id, source_unit="yuan", created_at=now)


def test_topology_conservation_and_direction(conn) -> None:
    """守恒：Σ行业边 == 大盘净额；方向：净流入 in、净流出 out。"""
    rid = _run(conn)
    # 钢铁(大板块,2股净流入)、煤炭(小板块,1股净流出)
    steel = _sector(conn, "钢铁", 2)
    coal = _sector(conn, "煤炭", 1)
    s1, s2, s3 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙"), _stock(conn, "600003", "丙")
    _membership(conn, steel, s1, rid); _membership(conn, steel, s2, rid)
    _membership(conn, coal, s3, rid)
    _write_flow(conn, s1, 30000, rid)   # 钢铁 +300
    _write_flow(conn, s2, 20000, rid)   # 钢铁 +200 → 钢铁共 +500
    _write_flow(conn, s3, -10000, rid)  # 煤炭 -100

    res = FlowTopologyService(conn).get_topology(tier="main", top_sectors=20)
    market = next(n for n in res.nodes if n.id == "market")
    assert market.net_yuan == "400.00"          # 500-100=400元
    # 守恒：Σ行业边签名值 == 大盘
    link_sum = sum(float(l.signed_value) for l in res.links)
    assert abs(link_sum - float(market.net_yuan)) < 0.01
    # 方向
    steel_node = next(n for n in res.nodes if n.subject_id == steel)
    coal_node = next(n for n in res.nodes if n.subject_id == coal)
    assert steel_node.direction == "in" and steel_node.net_yuan == "500.00"
    assert coal_node.direction == "out" and coal_node.net_yuan == "-100.00"
    # 边宽恒正（abs）
    steel_link = next(l for l in res.links if l.target == f"sector:{steel}")
    assert steel_link.abs_value == "500.00" and steel_link.signed_value == "500.00"
    coal_link = next(l for l in res.links if l.target == f"sector:{coal}")
    assert coal_link.abs_value == "100.00" and coal_link.signed_value == "-100.00"


def test_topology_other_sector_conserves(conn) -> None:
    """TopN 截断后其余归'其他'，守恒不破（子和==父）。"""
    rid = _run(conn)
    secs = []
    for i in range(5):
        sec = _sector(conn, f"行业{i}", 1)
        st = _stock(conn, f"60010{i}", f"股{i}")
        _membership(conn, sec, st, rid)
        _write_flow(conn, st, (i + 1) * 10000, rid)
        secs.append(sec)
    # top_sectors=2 → 3个进"其他"
    res = FlowTopologyService(conn).get_topology(tier="main", top_sectors=2)
    market = next(n for n in res.nodes if n.id == "market")
    link_sum = sum(float(l.signed_value) for l in res.links)
    assert abs(link_sum - float(market.net_yuan)) < 0.01   # 含"其他"后仍守恒
    assert any(n.id == "other:market" for n in res.nodes)


def test_sector_drilldown(conn) -> None:
    """行业下钻：成分股 TopN。"""
    rid = _run(conn)
    steel = _sector(conn, "钢铁", 2)
    s1, s2 = _stock(conn, "600001", "甲"), _stock(conn, "600002", "乙")
    _membership(conn, steel, s1, rid); _membership(conn, steel, s2, rid)
    _write_flow(conn, s1, 30000, rid); _write_flow(conn, s2, 20000, rid)
    res = FlowTopologyService(conn).get_sector_stocks(sector_id=steel, tier="main", top_stocks=10)
    stocks = [n for n in res.nodes if n.depth == 2]
    assert len(stocks) == 2
    assert stocks[0].name == "甲"   # |净额| 降序
