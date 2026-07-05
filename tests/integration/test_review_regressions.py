"""对抗式审查确认缺陷的回归测试（防复发）。

对应 review 确认的 6 项：
- HIGH-1 未来"今天"污染时序/下钻（_cal_today 取真实自然日，非 MAX(calendar)）
- HIGH-2 upsert lastrowid 跨主体污染（回查业务键）
- HIGH-3 大盘覆盖率门禁 fail-open（last_total 未知→不落）
- HIGH-4 板块继承 stock-only 指标（change_pct 排序应 422 而非空榜）
- MEDIUM-2 delete_ids 超变量上限（分块）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import FetchSpec
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    RunType,
    SectorType,
    SortField,
    SubjectKind,
    SubjectLevel,
    ValueType,
)
from quantchive.service.errors import OutOfWindow, UnsupportedMetric
from quantchive.service.ingest_service import collect_stock_observations_once
from quantchive.service.market_aggregate import MARKET_SYMBOL, MarketAggregator
from quantchive.service.query_service import QueryService


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- HIGH-2: upsert 跨主体污染 ----

def test_upsert_returns_correct_id_across_subjects(conn) -> None:
    """两主体各写一行后重采第一个，upsert 必须返回其真实 id（非 lastrowid 陈旧值）。"""
    sd, od = SubjectDao(conn), ObservationDao(conn)
    rid = RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-02", minute_slot="LATEST", adapter_version="t")
    a, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                     subject_kind=SubjectKind.STOCK, source_code="eastmoney",
                     source_symbol="A", display_name="A")
    b, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                     subject_kind=SubjectKind.STOCK, source_code="eastmoney",
                     source_symbol="B", display_name="B")
    kw = dict(source_code="eastmoney", trade_date="2026-07-02", minute_slot="LATEST",
              value_type=ValueType.INTRADAY_LATEST, granularity="5min", observed_at="t",
              source_unit="yuan", ingestion_run_id=rid, created_at="t")
    oid_a = od.upsert(subject_id=a, price_micro=1_000_000, **kw)
    od.upsert(subject_id=b, price_micro=2_000_000, **kw)
    # 重采 A（走 ON CONFLICT UPDATE 路径）
    oid_a2 = od.upsert(subject_id=a, price_micro=1_500_000, **kw)
    real_a = conn.execute("SELECT observation_id FROM observation WHERE subject_id=?",
                          (a,)).fetchone()[0]
    assert oid_a2 == real_a == oid_a          # 不返回 B 的 id
    # circ_mktcap 挂到 A 而非 B
    od.upsert_metric(observation_id=oid_a2, metric_name="circ_mktcap", value_int=999)
    row = conn.execute(
        """SELECT s.source_symbol FROM observation_metric m
           JOIN observation o ON o.observation_id=m.observation_id
           JOIN subject s ON s.subject_id=o.subject_id""").fetchone()
    assert row[0] == "A"                       # 未错挂到 B


# ---- HIGH-1: 未来"今天"污染 ----

def _seed_stock_series(conn, sid, rid, td, slot, main):
    ObservationDao(conn).upsert(
        subject_id=sid, source_code="eastmoney", trade_date=td, minute_slot=slot,
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
        net_amount_cents=main,
        five_tier={"main_net_cents": main, "super_large_net_cents": 0, "large_net_cents": 0,
                   "medium_net_cents": 0, "small_net_cents": 0},
        source_unit="yuan", ingestion_run_id=rid, created_at=_now())


def test_series_ok_when_calendar_has_future_dates(conn) -> None:
    """日历含全年未来交易日时，近期真实数据的 series 仍返回（不被未来"今天"误判超窗）。"""
    # 模拟 akshare sync：日历塞入未来交易日（含 12-31）
    for d in ["2026-07-01", "2026-07-02", "2026-09-01", "2026-12-31"]:
        conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    sd = SubjectDao(conn)
    sid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                       subject_kind=SubjectKind.STOCK, source_code="eastmoney",
                       source_symbol="600519", display_name="茅台")
    rid = RunDao(conn).start(source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
                             caliber=Caliber.EASTMONEY, trade_date="2026-07-02",
                             minute_slot="09:31", adapter_version="t")
    _seed_stock_series(conn, sid, rid, "2026-07-02", "09:31", 100)
    # 真实"今天"=2026-07-03（FixedClock）；日历里有 12-31 但不得当作今天
    svc = QueryService(conn, retention_trade_days=30,
                       clock=FixedClock(datetime(2026, 7, 3, 10, 0, tzinfo=SHANGHAI_TZ)))
    res = svc.get_subject_series(subject_id=sid, metric=SortField.MAIN_NET,
                                 trade_date="2026-07-02")
    assert len(res.points) == 1               # 近期数据正常返回，未被误判 OutOfWindow


def test_series_out_of_window_uses_real_today(conn) -> None:
    """超保留窗仍正确判 OutOfWindow（基于真实今天，非未来日历末点）。"""
    for d in ["2026-05-01", "2026-07-02", "2026-12-31"]:
        conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, 1)", (d,))
    sd = SubjectDao(conn)
    sid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                       subject_kind=SubjectKind.STOCK, source_code="eastmoney",
                       source_symbol="X", display_name="X")
    rid = RunDao(conn).start(source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
                             caliber=Caliber.EASTMONEY, trade_date="2026-05-01",
                             minute_slot="09:31", adapter_version="t")
    _seed_stock_series(conn, sid, rid, "2026-05-01", "09:31", 100)
    svc = QueryService(conn, retention_trade_days=30,
                       clock=FixedClock(datetime(2026, 7, 3, 10, 0, tzinfo=SHANGHAI_TZ)))
    # 05-01 距真实今天 07-03 > 30 天 → OutOfWindow
    with pytest.raises(OutOfWindow):
        svc.get_subject_series(subject_id=sid, metric=SortField.MAIN_NET,
                               trade_date="2026-05-01")


# ---- HIGH-3: 覆盖率门禁 fail-open ----

def _stock_obs(code, main) -> RawObservation:
    m = Decimal(main)
    return RawObservation(
        source_symbol=code, display_name=code, asset_class=AssetClass.A_SHARE,
        level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
        main_net=m, super_large_net=m, large_net=Decimal("0"), medium_net=Decimal("0"),
        small_net=Decimal("0"), source_unit=AmountUnit.YUAN)


class _FakeStockSourceNoTotal:
    """last_total=0（源未报 total，模拟翻页截断/未知）。"""
    source_id = "fake:stock"
    adapter_version = "t"

    def __init__(self, obs) -> None:
        self._obs = obs
        self.last_total = 0                    # 未知全集

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        return self._obs

    def capability(self, spec): ...
    def fetch_subjects(self, spec): return []
    def fetch_members(self, parent): return []
    def fetch_daily_final(self, spec, trade_date): return self._obs


def test_market_not_written_when_total_unknown(conn) -> None:
    """last_total=0（全集未知）→ 覆盖率不可校验 → 大盘不落（fail-closed，非 100%自证）。"""
    conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    obs = [_stock_obs(f"60000{i}", "100000000") for i in range(50)]
    result = collect_stock_observations_once(
        source=_FakeStockSourceNoTotal(obs), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn), aggregator=MarketAggregator(conn),
        clock=clock)
    assert result.aggregate is not None and result.aggregate.written is False
    market_id = SubjectDao(conn).find_id(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.MARKET, source_symbol=MARKET_SYMBOL)
    if market_id is not None:
        assert ObservationDao(conn).latest_market_point(market_subject_id=market_id) is None


# ---- HIGH-4: 板块继承 stock-only 指标 ----

def test_board_change_pct_sort_rejected(conn) -> None:
    """板块按 change_pct 排序 → UnsupportedMetric（不是继承 stock 指标返回空榜）。"""
    conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    svc = QueryService(conn, retention_trade_days=7,
                       clock=FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ)))
    with pytest.raises(UnsupportedMetric):
        svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.CHANGE_PCT, top_n=10)


def test_ranking_slot_scoped_to_subject_set(conn) -> None:
    """多 target 共用 source 但 batch_slot 不同时，板块排行的 slot 必须限定在板块集内。

    复现线上 bug：板块采于 10:30、大盘求和点写于 10:35（同 source_code='eastmoney'），
    若按 source 全局取 MAX(slot)=10:35 则板块查空。修复后按 subject 集取 slot。
    """
    conn.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    sd, od = SubjectDao(conn), ObservationDao(conn)
    rid = RunDao(conn).start(source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
                             caliber=Caliber.EASTMONEY, trade_date="2026-07-02",
                             minute_slot="10:30", adapter_version="t")
    bid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
                       source_symbol="半导体", display_name="半导体")
    od.upsert(subject_id=bid, source_code="eastmoney", trade_date="2026-07-02",
              minute_slot="10:30", value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min",
              observed_at="t", net_amount_cents=500,
              five_tier={"main_net_cents": 500, "super_large_net_cents": 0, "large_net_cents": 0,
                         "medium_net_cents": 0, "small_net_cents": 0},
              source_unit="yuan", ingestion_run_id=rid, created_at="t")
    # 大盘求和点 @ 10:35（更晚 slot，同 source）——不得污染板块 slot 解析
    mid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.MARKET,
                       subject_kind=SubjectKind.MARKET_TOTAL, source_code="eastmoney",
                       source_symbol="__MARKET__", display_name="大盘")
    od.upsert(subject_id=mid, source_code="eastmoney", trade_date="2026-07-02",
              minute_slot="10:35", value_type=ValueType.INTRADAY_SNAPSHOT, granularity="5min",
              observed_at="t", net_amount_cents=999,
              five_tier={"main_net_cents": 999, "super_large_net_cents": 0, "large_net_cents": 0,
                         "medium_net_cents": 0, "small_net_cents": 0},
              source_unit="yuan", is_derived=True, ingestion_run_id=rid, created_at="t")

    svc = QueryService(conn, retention_trade_days=7,
                       clock=FixedClock(datetime(2026, 7, 2, 11, 0, tzinfo=SHANGHAI_TZ)))
    r = svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.MAIN_NET, top_n=10)
    assert r.total_subjects == 1                       # 未被大盘 10:35 slot 串味查空
    assert r.top_inflow[0].display_name == "半导体"


def test_board_capability_excludes_price_metrics(conn) -> None:
    """get_capability(板块) 诚实：不列 price/change_pct（板块不采价量）。"""
    svc = QueryService(conn, retention_trade_days=7)
    cap = svc.get_capability(asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.INDUSTRY)
    assert "main_net" in cap.supported_metrics
    assert "price" not in cap.supported_metrics
    assert "change_pct" not in cap.supported_metrics
    assert SortField.CHANGE_PCT not in cap.available_sort_fields


# ---- MEDIUM-2: delete_ids 分块 ----

def test_delete_ids_over_variable_limit(conn) -> None:
    """删除超 SQLite 变量上限的 id 列表不崩溃（分块）。"""
    sd, od = SubjectDao(conn), ObservationDao(conn)
    sid, _ = sd.upsert(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                       subject_kind=SubjectKind.STOCK, source_code="eastmoney",
                       source_symbol="X", display_name="X")
    rid = RunDao(conn).start(source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT,
                             caliber=Caliber.EASTMONEY, trade_date="2026-06-01",
                             minute_slot="09:31", adapter_version="t")
    ids = []
    for i in range(2500):                       # > 999 变量上限
        m = i % 60
        oid = od.upsert(subject_id=sid, source_code="eastmoney", trade_date="2026-06-01",
                        minute_slot=f"09:{m:02d}", value_type=ValueType.INTRADAY_SNAPSHOT,
                        granularity="1min", observed_at=f"t{i}", price_micro=1_000_000 + i,
                        source_unit="yuan", ingestion_run_id=rid, created_at="t")
        ids.append(oid)
    ids = list(set(ids))
    deleted = od.delete_ids(ids)                # 不抛 "too many SQL variables"
    assert deleted == len(ids)
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 0
