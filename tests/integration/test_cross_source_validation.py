"""T030-T032: 跨源校验编排(mock 百度+sina) + 分歧记审计 + provenance 角标。不联网。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quantchive.datasource.baidu_flow_src import BaiduTierProbe
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
from quantchive.service.ingest_service import run_cross_source_validation


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


class _FakeBaidu:
    """按 code→四档(gross,net元) 预设返 probe,不联网。"""
    def __init__(self, data: dict[str, dict[str, tuple]]) -> None:
        self._data = data

    def probe(self, codes):
        out = []
        for c in codes:
            if c in self._data:
                tiers = {t: {"gross": Decimal(str(g)), "net": Decimal(str(n))}
                         for t, (g, n) in self._data[c].items()}
                out.append(BaiduTierProbe(code=c, tiers=tiers))
        return out


def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SSE")
    return sid


def _run(conn):
    return RunDao(conn).start(source_code="sina_flow", run_type=RunType.EOD_BACKFILL,
                              caliber=Caliber.EASTMONEY, trade_date="2026-07-03",
                              minute_slot="EOD", adapter_version="t", subject_scope="x",
                              asset_class_code="a_share")


def _write(conn, sid, rid, sl_net):
    now = datetime.now(timezone.utc).isoformat()
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="sina_flow", trade_date="2026-07-03", minute_slot="EOD",
        value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=now,
        five_tier={"main_net_cents": sl_net, "super_large_net_cents": sl_net,
                   "large_net_cents": 0, "medium_net_cents": 0, "small_net_cents": 0},
        four_gross={"super_large_gross_cents": abs(sl_net) * 3, "large_gross_cents": 0,
                    "medium_gross_cents": 0, "small_gross_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at=now)


def test_validation_records_divergence(conn) -> None:
    """新浪超大单净额+100元 vs 百度-500元 方向相反 → divergence 记审计。"""
    rid = _run(conn)
    s1 = _stock(conn, "600001", "甲")
    _write(conn, s1, rid, 10000)   # sina 超大单净 +10000分 = +100元
    fake = _FakeBaidu({"600001": {"super_large": (1000, -500)}})  # 百度 -500元 方向反
    res = run_cross_source_validation(conn, baidu_source=fake, trade_date="2026-07-03")
    assert res["divergences"] >= 1
    row = conn.execute(
        "SELECT verdict, reason FROM cross_source_check WHERE subject_id=? AND tier='super_large'",
        (s1,)).fetchone()
    assert row["verdict"] == "divergence"


def test_validation_ok_not_flagged_divergence(conn) -> None:
    """方向量级合理(口径差异) → ok,不报分歧。"""
    rid = _run(conn)
    s1 = _stock(conn, "600002", "乙")
    _write(conn, s1, rid, 10000)   # +100元
    fake = _FakeBaidu({"600002": {"super_large": (1000, 120)}})  # 百度 +120元 同向量级近
    res = run_cross_source_validation(conn, baidu_source=fake, trade_date="2026-07-03")
    row = conn.execute(
        "SELECT verdict FROM cross_source_check WHERE subject_id=? AND tier='super_large'",
        (s1,)).fetchone()
    assert row["verdict"] == "ok" and res["divergences"] == 0


def test_provenance_validation_badge(conn) -> None:
    """校验记录 → tiers_series provenance.validation 下发角标。"""
    rid = _run(conn)
    s1 = _stock(conn, "600003", "丙")
    _write(conn, s1, rid, 10000)
    fake = _FakeBaidu({"600003": {"super_large": (1000, -500)}})  # 分歧
    run_cross_source_validation(conn, baidu_source=fake, trade_date="2026-07-03")
    res = FlowQueryService(conn, retention_trade_days=90).get_tiers_series(subject_id=s1)
    assert res.provenance.validation == "divergence"


def test_no_check_means_none(conn) -> None:
    """没校验记录 → validation=None(未校验,不误报)。"""
    rid = _run(conn)
    s1 = _stock(conn, "600004", "丁")
    _write(conn, s1, rid, 10000)
    res = FlowQueryService(conn, retention_trade_days=90).get_tiers_series(subject_id=s1)
    assert res.provenance.validation is None
