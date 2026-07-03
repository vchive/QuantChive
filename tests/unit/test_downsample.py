"""T049: 保留分级降采口径（小时聚合：资金流取末值/价取收盘/量累计）+ 清理。"""

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
from quantchive.service.ingest_service import retention_cleanup, retention_downsample


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subject(conn, symbol="600519") -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol=symbol, display_name=symbol)
    return sid


def _run(conn) -> int:
    return RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-06-01", minute_slot="10:00", adapter_version="test")


def _write(conn, sid, rid, slot, *, main, price, volume):
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date="2026-06-01", minute_slot=slot,
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
        net_amount_cents=main,
        five_tier={"main_net_cents": main, "super_large_net_cents": 0, "large_net_cents": 0,
                   "medium_net_cents": 0, "small_net_cents": 0},
        price_micro=price, volume=volume, source_unit="yuan", ingestion_run_id=rid,
        created_at=_now())


def test_downsample_hourly_caliber(conn) -> None:
    """10:xx 三个分钟点 → 一个 10:00 小时点：资金流/价取末值，量取累计。"""
    sid, rid = _subject(conn), _run(conn)
    _write(conn, sid, rid, "10:15", main=100, price=1_000_000, volume=10)
    _write(conn, sid, rid, "10:30", main=200, price=1_100_000, volume=20)
    _write(conn, sid, rid, "10:45", main=300, price=1_200_000, volume=30)  # 末点
    # as_of 距 2026-06-01 远超 7 天 → 触发降采
    res = retention_downsample(conn, as_of_date="2026-07-03", downsample_after_days=7)
    assert res["downsampled_groups"] == 1
    assert res["rows_deleted"] == 3

    # 原分钟行已删，只剩 1 个小时点
    rows = conn.execute(
        "SELECT minute_slot, value_type, granularity, main_net_cents, price_micro, volume "
        "FROM observation WHERE subject_id=?", (sid,)).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["minute_slot"] == "10:00" and r["value_type"] == "hourly_rollup"
    assert r["granularity"] == "hourly"
    assert r["main_net_cents"] == 300        # 资金流取小时末累计值
    assert r["price_micro"] == 1_200_000     # 价取小时收盘
    assert r["volume"] == 30                  # 量取小时末值(东财 f5 为当日累计,取末点=累计到小时末)


def test_downsample_recent_data_untouched(conn) -> None:
    """近 7 天内的数据不降采（保持分钟级）。"""
    sid = _subject(conn)
    rid = RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-02", minute_slot="10:15", adapter_version="test")
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date="2026-07-02", minute_slot="10:15",
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
        net_amount_cents=100,
        five_tier={"main_net_cents": 100, "super_large_net_cents": 0, "large_net_cents": 0,
                   "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    res = retention_downsample(conn, as_of_date="2026-07-03", downsample_after_days=7)
    assert res["downsampled_groups"] == 0    # 07-02 在 7 天内，不降采
    n = conn.execute("SELECT COUNT(*) FROM observation WHERE granularity='1min'").fetchone()[0]
    assert n == 1


def test_downsample_idempotent(conn) -> None:
    sid, rid = _subject(conn), _run(conn)
    _write(conn, sid, rid, "10:15", main=100, price=1_000_000, volume=10)
    retention_downsample(conn, as_of_date="2026-07-03", downsample_after_days=7)
    res2 = retention_downsample(conn, as_of_date="2026-07-03", downsample_after_days=7)
    assert res2["downsampled_groups"] == 0   # 原行已删，重跑无候选


def test_downsample_cascades_metrics(conn) -> None:
    """降采删原行 CASCADE 带走 observation_metric。"""
    sid, rid = _subject(conn), _run(conn)
    oid = ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date="2026-06-01", minute_slot="10:15",
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
        price_micro=1_000_000, source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    ObservationDao(conn).upsert_metric(observation_id=oid, metric_name="circ_mktcap", value_int=999)
    retention_downsample(conn, as_of_date="2026-07-03", downsample_after_days=7)
    # 原 observation 删 → 其 metric CASCADE 删
    n = conn.execute("SELECT COUNT(*) FROM observation_metric").fetchone()[0]
    assert n == 0


def test_retention_cleanup_deletes_old(conn) -> None:
    sid = _subject(conn)
    for td in ["2026-05-01", "2026-06-20", "2026-07-02"]:
        rid = RunDao(conn).start(
            source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
            caliber=Caliber.EASTMONEY, trade_date=td, minute_slot="EOD", adapter_version="t")
        ObservationDao(conn).upsert(
            subject_id=sid, source_code="eastmoney", trade_date=td, minute_slot="EOD",
            value_type=ValueType.DAILY_FINAL, granularity="daily", observed_at=_now(),
            net_amount_cents=1,
            five_tier={"main_net_cents": 1, "super_large_net_cents": 0, "large_net_cents": 0,
                       "medium_net_cents": 0, "small_net_cents": 0},
            source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    # as_of=07-03, 保留 30 天 → cutoff=06-03；删 05-01（< 06-03），留 06-20/07-02
    res = retention_cleanup(conn, as_of_date="2026-07-03", retention_trade_days=30)
    assert res["rows_deleted"] == 1
    remaining = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM observation")}
    assert remaining == {"2026-06-20", "2026-07-02"}
