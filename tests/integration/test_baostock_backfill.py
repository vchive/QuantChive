"""T019: baostock 增量回填——写 daily_final + coverage_range，二次仅补缺，单主体隔离。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.coverage_dao import CoverageDao
from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import DataSourceError
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import AssetClass, SortField, SubjectKind, SubjectLevel
from quantchive.service.ingest_service import backfill_price_history
from quantchive.service.query_service import QueryService

from decimal import Decimal


class _FakeHistorySource:
    """按 [start,end] 返回该区间每日 price bar；记录被请求的区间（验只补缺）。"""

    source_id = "baostock"

    def __init__(self, *, fail_symbols=None) -> None:
        self.requested: list[tuple[str, str, str]] = []   # (symbol, start, end)
        self._fail = fail_symbols or set()

    def fetch_price_history(self, *, symbol, exchange, start_date, end_date,
                            granularity="daily", adjust="qfq"):
        self.requested.append((symbol, start_date, end_date))
        if symbol in self._fail:
            raise DataSourceError("限流", error_type="rate_limited")
        # 造区间内每个"日"（用简单日期序列，不管交易日历，测试足够）
        from datetime import date, timedelta
        out = []
        d = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        px = 10
        while d <= end:
            out.append(RawObservation(
                source_symbol=symbol, display_name=symbol, asset_class=AssetClass.A_SHARE,
                level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK,
                price=Decimal(px), change_pct=Decimal("1.0"), volume=Decimal("100"),
                turnover=Decimal("1000"), trade_date=d.isoformat()))
            d += timedelta(days=1)
            px += 1
        return out


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    for d in ["2026-06-01", "2026-06-15", "2026-07-01", "2026-07-02", "2026-07-03"]:
        c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    return c


def _stock(conn, code="000725") -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney",
        source_symbol=code, display_name=code, exchange="SZSE")
    return sid


_CLOCK = FixedClock(datetime(2026, 7, 3, 16, 0, tzinfo=SHANGHAI_TZ))


def test_backfill_writes_daily_final_and_coverage(conn) -> None:
    sid = _stock(conn)
    src = _FakeHistorySource()
    summary = backfill_price_history(conn, source=src, days=3, clock=_CLOCK, limit=None)
    assert summary["subjects_ok"] == 1 and summary["rows_written"] > 0
    # daily_final 落库
    n = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE subject_id=? AND value_type='daily_final'",
        (sid,)).fetchone()[0]
    assert n == summary["rows_written"]
    # coverage_range 记区间
    rng = CoverageDao(conn).get_range(subject_id=sid, metric_kind="price_hist",
                                      granularity="daily", source_code="baostock")
    assert rng is not None and rng[1] == "2026-07-03"


def test_second_backfill_skips_covered(conn) -> None:
    """二次回填同窗 → 缺口为空 → 零请求（SC-003）。"""
    _stock(conn)
    src1 = _FakeHistorySource()
    backfill_price_history(conn, source=src1, days=3, clock=_CLOCK)
    n_req1 = len(src1.requested)
    assert n_req1 >= 1
    src2 = _FakeHistorySource()
    summary2 = backfill_price_history(conn, source=src2, days=3, clock=_CLOCK)
    assert src2.requested == []                    # 零请求（已全覆盖）
    assert summary2["skipped_covered"] == 1
    assert summary2["rows_written"] == 0


def test_single_subject_failure_isolated(conn) -> None:
    _stock(conn, "000725")
    _stock(conn, "600519")
    src = _FakeHistorySource(fail_symbols={"600519"})
    summary = backfill_price_history(conn, source=src, days=3, clock=_CLOCK)
    assert summary["subjects_ok"] == 1 and summary["subjects_failed"] == 1  # 一失败不阻塞另一


def test_price_series_after_backfill(conn) -> None:
    """回填后 price 日线时序可查（兑现历史深度 SC-002）。"""
    sid = _stock(conn)
    backfill_price_history(conn, source=_FakeHistorySource(), days=3, clock=_CLOCK)
    svc = QueryService(conn, retention_trade_days=30, clock=_CLOCK)
    res = svc.get_subject_series(subject_id=sid, metric=SortField.PRICE, granularity="daily")
    assert res.granularity == "daily"
    assert len(res.points) >= 3                    # 多日历史，非只有今天
