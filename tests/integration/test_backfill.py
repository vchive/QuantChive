"""历史日线资金流回填（backfill_daily_flow）+ 日线时序（granularity='daily'）。"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.em_kline_src import (
    DailyFlowPoint,
    secid_for_board,
    secid_for_stock,
)
from quantchive.models.enums import AssetClass, SortField, SubjectKind, SubjectLevel
from quantchive.service.ingest_service import backfill_daily_flow
from quantchive.service.query_service import QueryService


class _FakeDailyFlowSource:
    """按 secid 返回固定 N 日历史五档。"""

    source_id = "fake:fflow"

    def __init__(self, n=5) -> None:
        self._n = n

    def fetch_daily_flow(self, *, secid, days=30):
        d = Decimal
        base = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]
        pts = [DailyFlowPoint(trade_date=base[i], main_net=d(str((i + 1) * 100000000)),
                              super_large_net=d("0"), large_net=d("0"),
                              medium_net=d("0"), small_net=d("0")) for i in range(self._n)]
        return pts[-days:]


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    for d in ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]:
        c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    return c


def test_secid_mapping() -> None:
    assert secid_for_stock("600519", "SSE") == "1.600519"     # 沪
    assert secid_for_stock("000725", "SZSE") == "0.000725"     # 深
    assert secid_for_stock("830799", "BSE") == "0.830799"      # 北
    assert secid_for_board("BK0475") == "90.BK0475"


def test_backfill_sector_writes_daily_final(conn) -> None:
    sd = SubjectDao(conn)
    bid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
                       source_symbol="半导体", display_name="半导体", em_board_code="BK0475")
    summary = backfill_daily_flow(conn, source=_FakeDailyFlowSource(n=5), scope="sector", days=30)
    assert summary["subjects_ok"] == 1 and summary["rows_written"] == 5
    # 5 天 daily_final 落库
    n = conn.execute(
        "SELECT COUNT(*) FROM observation WHERE subject_id=? AND value_type='daily_final'",
        (bid,)).fetchone()[0]
    assert n == 5


def test_backfill_idempotent(conn) -> None:
    sd = SubjectDao(conn)
    sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
              subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
              source_symbol="半导体", display_name="半导体", em_board_code="BK0475")
    backfill_daily_flow(conn, source=_FakeDailyFlowSource(n=5), scope="sector", days=30)
    backfill_daily_flow(conn, source=_FakeDailyFlowSource(n=5), scope="sector", days=30)
    n = conn.execute("SELECT COUNT(*) FROM observation WHERE value_type='daily_final'").fetchone()[0]
    assert n == 5                                              # 幂等，不重复


def test_daily_series_returns_history(conn) -> None:
    sd = SubjectDao(conn)
    bid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
                       source_symbol="半导体", display_name="半导体", em_board_code="BK0475")
    backfill_daily_flow(conn, source=_FakeDailyFlowSource(n=5), scope="sector", days=30)
    svc = QueryService(conn, retention_trade_days=30, source_id="fake:fflow")
    res = svc.get_subject_series(subject_id=bid, metric=SortField.MAIN_NET, granularity="daily")
    assert res.granularity == "daily"
    assert len(res.points) == 5
    assert [p.ts for p in res.points] == [
        "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]
    # 值：主力 1亿元 → "100000000.00"（服务层 Decimal），ts 是 trade_date
    assert res.points[0].value == Decimal("100000000.00")
    assert res.gap_count == 0


def test_backfill_skips_throttled_short_response(conn) -> None:
    """限流降级探测：请求 60 天却只回 1 天 → 判限流，跳过不写脏历史（宁缺勿假）。"""
    sd = SubjectDao(conn)
    bid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
                       source_symbol="半导体", display_name="半导体", em_board_code="BK0475")
    # 源只回 1 天（模拟被东财限流降级）
    summary = backfill_daily_flow(conn, source=_FakeDailyFlowSource(n=1), scope="sector",
                                  days=60, min_days_guard=2)
    assert summary["throttled"] == 1
    assert summary["rows_written"] == 0                       # 不写 1 天假历史
    n = conn.execute("SELECT COUNT(*) FROM observation WHERE value_type='daily_final'").fetchone()[0]
    assert n == 0


def test_daily_series_empty_raises(conn) -> None:
    from quantchive.service.errors import NoDataForDate
    sd = SubjectDao(conn)
    bid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
                       source_symbol="白酒", display_name="白酒", em_board_code="BK0476")
    svc = QueryService(conn, retention_trade_days=30)
    with pytest.raises(NoDataForDate):
        svc.get_subject_series(subject_id=bid, metric=SortField.MAIN_NET, granularity="daily")
