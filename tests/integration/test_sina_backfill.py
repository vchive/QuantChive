"""新浪资金流历史回填——写五档 daily_final + coverage、二次仅补缺、main_net 时序可查。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.coverage_dao import CoverageDao
from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import AssetClass, SortField, SubjectKind, SubjectLevel
from quantchive.service.ingest_service import backfill_flow_history
from quantchive.service.query_service import QueryService


class _FakeSinaSource:
    source_id = "sina_flow"

    def __init__(self) -> None:
        self.requested: list = []

    def fetch_flow_history(self, *, symbol, exchange, start_date, end_date, max_rows=800):
        self.requested.append((symbol, start_date, end_date))
        from datetime import date, timedelta
        out = []
        d = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        v = 1000000
        while d <= end:
            out.append(RawObservation(
                source_symbol=symbol, display_name=symbol, asset_class=AssetClass.A_SHARE,
                level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK,
                main_net=Decimal(v), super_large_net=Decimal(v * 6 // 10),
                large_net=Decimal(v * 4 // 10), medium_net=Decimal("0"), small_net=Decimal("0"),
                price=Decimal("8.5"), change_pct=Decimal("1.0"), trade_date=d.isoformat()))
            d += timedelta(days=1)
            v += 100000
        return out


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    for d in ["2026-07-01", "2026-07-02", "2026-07-03"]:
        c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    return c


def _stock(conn, code="600150") -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol=code, display_name="中国船舶", exchange="SSE")
    return sid


_CLOCK = FixedClock(datetime(2026, 7, 3, 16, 0, tzinfo=SHANGHAI_TZ))


def test_backfill_writes_five_tier_flow(conn) -> None:
    sid = _stock(conn)
    summary = backfill_flow_history(conn, source=_FakeSinaSource(), days=3, clock=_CLOCK, sleep_sec=0)
    assert summary["subjects_ok"] == 1 and summary["rows_written"] > 0
    # net_amount_cents 非空（有资金流，非 baostock 的 null）
    row = conn.execute(
        """SELECT net_amount_cents FROM observation WHERE subject_id=? AND value_type='daily_final'
           AND source_code='sina_flow' ORDER BY trade_date DESC LIMIT 1""", (sid,)).fetchone()
    assert row["net_amount_cents"] is not None and row["net_amount_cents"] != 0
    rng = CoverageDao(conn).get_range(subject_id=sid, metric_kind="money_flow",
                                      granularity="daily", source_code="sina_flow")
    assert rng is not None


def test_second_backfill_skips(conn) -> None:
    _stock(conn)
    backfill_flow_history(conn, source=_FakeSinaSource(), days=3, clock=_CLOCK, sleep_sec=0)
    src2 = _FakeSinaSource()
    s2 = backfill_flow_history(conn, source=src2, days=3, clock=_CLOCK, sleep_sec=0)
    assert src2.requested == [] and s2["skipped_covered"] == 1


def test_main_net_series_after_backfill(conn) -> None:
    """回填后 main_net 日线时序可查（中国船舶资金流历史兑现）。"""
    sid = _stock(conn)
    backfill_flow_history(conn, source=_FakeSinaSource(), days=3, clock=_CLOCK, sleep_sec=0)
    svc = QueryService(conn, retention_trade_days=30, clock=_CLOCK)
    res = svc.get_subject_series(subject_id=sid, metric=SortField.MAIN_NET, granularity="daily")
    non_null = [p for p in res.points if p.value is not None]
    assert len(non_null) >= 3                       # 真实资金流历史，非空
