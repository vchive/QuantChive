"""T011-T014: 通用 DAO（subject/metric/observation/run）单元测试。

覆盖迁移 critique 硬点：find_id 跨主体防误命中、ranking LIMIT 下推、series 排除
intraday_latest/'LATEST'、run_dao 新旧参数别名兼容。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.metric_dao import MetricDao
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass,
    Caliber,
    RunStatus,
    RunType,
    SortField,
    SubjectKind,
    SubjectLevel,
    ValueType,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- T011 SubjectDao ----

def test_subject_upsert_idempotent(conn: sqlite3.Connection) -> None:
    dao = SubjectDao(conn)
    sid1, new1 = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol="半导体", display_name="半导体",
    )
    sid2, new2 = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney",
        source_symbol="半导体", display_name="半导体",
    )
    assert new1 is True and new2 is False and sid1 == sid2


def test_find_id_no_cross_subject_false_match(conn: sqlite3.Connection) -> None:
    """同 symbol 不同 level（板块 vs 个股）不得互相误命中（critique 硬点）。"""
    dao = SubjectDao(conn)
    sec_id, _ = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.CONCEPT, source_code="eastmoney", source_symbol="XX",
    )
    stk_id, _ = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney", source_symbol="XX",
    )
    assert sec_id != stk_id
    assert dao.find_id(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                       source_symbol="XX") == sec_id
    assert dao.find_id(asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                       source_symbol="XX") == stk_id


def test_children_of_asof_no_lookahead(conn: sqlite3.Connection) -> None:
    dao = SubjectDao(conn)
    parent, _ = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind.INDUSTRY, source_code="eastmoney", source_symbol="P")
    child, _ = dao.upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="eastmoney", source_symbol="C")
    conn.execute(
        """INSERT INTO subject_membership (parent_subject_id, child_subject_id, relation_kind,
           effective_from, effective_to, source_code, created_at)
           VALUES (?,?,'sector_constituent','2026-01-01','2026-06-01','eastmoney',?)""",
        (parent, child, _now()),
    )
    assert dao.children_of(parent, "2026-03-01") == [child]   # 区间内
    assert dao.children_of(parent, "2026-07-01") == []        # 已退出，无前视


# ---- T012 MetricDao（能力自描述）----

def test_metric_applicability_capability(conn: sqlite3.Connection) -> None:
    dao = MetricDao(conn)
    # 板块(INDUSTRY)有五档，但**不继承** stock-only 的价/量（HIGH-4 回归：
    # 否则板块谎报支持 change_pct → 按其排序返回空榜而非诚实 422）
    board_metrics = dao.applicable_metrics(
        asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.INDUSTRY)
    assert "main_net" in board_metrics
    assert "price" not in board_metrics and "change_pct" not in board_metrics
    # 个股(STOCK)既有五档也有价/量
    stock_metrics = dao.applicable_metrics(
        asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.STOCK)
    assert "main_net" in stock_metrics and "price" in stock_metrics
    assert "change_pct" in stock_metrics
    # 板块可排序含五档、不含 change_pct（板块不采价量）
    board_sortable = dao.sortable_metrics(
        asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.INDUSTRY)
    assert "main_net" in board_sortable and "change_pct" not in board_sortable
    # 个股可排序含 change_pct（is_sortable=1）
    stock_sortable = dao.sortable_metrics(
        asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.STOCK)
    assert "change_pct" in stock_sortable
    # ETF 不支持五档
    assert dao.supports(asset_class=AssetClass.FUND_ETF,
                        subject_kind=SubjectKind.ETF, metric_name="main_net") is False
    assert dao.supports(asset_class=AssetClass.FUND_ETF,
                        subject_kind=SubjectKind.ETF, metric_name="price") is True


def test_metric_scale_factors_loaded(conn: sqlite3.Connection) -> None:
    dao = MetricDao(conn)
    assert dao.get("price").scale_factor == 1_000_000
    assert dao.get("change_pct").scale_factor == 100
    assert dao.get("main_net").is_sortable is True


# ---- T013 ObservationDao ----

def _seed_subject(conn, symbol, level=SubjectLevel.SECTOR, kind=SubjectKind.INDUSTRY) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=level, subject_kind=kind,
        source_code="eastmoney", source_symbol=symbol, display_name=symbol)
    return sid


def _seed_run(conn) -> int:
    return RunDao(conn).start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-03", minute_slot="10:30", adapter_version="v2")


def test_observation_upsert_returns_id_idempotent(conn: sqlite3.Connection) -> None:
    dao = ObservationDao(conn)
    sid, rid = _seed_subject(conn, "半导体"), _seed_run(conn)
    kwargs = dict(
        subject_id=sid, source_code="eastmoney", trade_date="2026-07-03", minute_slot="10:30",
        value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
        net_amount_cents=100,
        five_tier={"main_net_cents": 100, "super_large_net_cents": 1, "large_net_cents": 1,
                   "medium_net_cents": 1, "small_net_cents": 1},
        source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    oid1 = dao.upsert(**kwargs)
    oid2 = dao.upsert(**{**kwargs, "net_amount_cents": 200})  # 覆盖同键
    assert oid1 == oid2
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 1
    assert conn.execute("SELECT net_amount_cents FROM observation").fetchone()[0] == 200


def test_ranking_snapshot_limit_pushdown(conn: sqlite3.Connection) -> None:
    dao = ObservationDao(conn)
    rid = _seed_run(conn)
    ids = []
    for i in range(5):
        sid = _seed_subject(conn, f"B{i}")
        ids.append(sid)
        dao.upsert(
            subject_id=sid, source_code="eastmoney", trade_date="2026-07-03", minute_slot="10:30",
            value_type=ValueType.INTRADAY_SNAPSHOT, granularity="1min", observed_at=_now(),
            net_amount_cents=i * 100,
            five_tier={"main_net_cents": i * 100, "super_large_net_cents": 0, "large_net_cents": 0,
                       "medium_net_cents": 0, "small_net_cents": 0},
            source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    top = dao.ranking_snapshot(
        subject_ids=ids, trade_date="2026-07-03", value_type=ValueType.INTRADAY_SNAPSHOT,
        minute_slot="10:30", sort_by=SortField.MAIN_NET, top_n=2)
    assert len(top) == 2                       # DB 层 LIMIT
    assert top[0].main_net_cents == 400        # 降序
    assert top[1].main_net_cents == 300


def test_series_excludes_latest(conn: sqlite3.Connection) -> None:
    """series 排除 intraday_latest 且过滤 'LATEST' 槽（防字符串排序假点）。"""
    dao = ObservationDao(conn)
    sid, rid = _seed_subject(conn, "AAA", level=SubjectLevel.INSTRUMENT, kind=SubjectKind.STOCK), _seed_run(conn)
    for slot, vt in [("09:31", ValueType.INTRADAY_SNAPSHOT),
                     ("10:00", ValueType.INTRADAY_SNAPSHOT),
                     ("LATEST", ValueType.INTRADAY_LATEST)]:
        dao.upsert(
            subject_id=sid, source_code="eastmoney", trade_date="2026-07-03", minute_slot=slot,
            value_type=vt, granularity="5min", observed_at=_now(),
            price_micro=1_000_000, source_unit="yuan", ingestion_run_id=rid, created_at=_now())
    series = dao.series(subject_id=sid, trade_date="2026-07-03")
    slots = [r.minute_slot for r in series]
    assert slots == ["09:31", "10:00"]         # 无 'LATEST'


# ---- T014 RunDao 泛化（别名兼容）----

def test_run_dao_legacy_sectors_alias(conn: sqlite3.Connection) -> None:
    """旧调用 sectors_ok/sectors_failed 仍工作（迁移铁律）。"""
    dao = RunDao(conn)
    rid = dao.start(
        source_code="eastmoney", run_type=RunType.INTRADAY_SNAPSHOT, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-03", minute_slot="10:30", adapter_version="v2", sector_scope="industry")
    dao.finish(rid, status=RunStatus.SUCCESS, sectors_ok=991, sectors_failed=0)
    row = dao.latest()
    assert row["sectors_ok"] == 991 and row["subjects_ok"] == 991


def test_run_dao_subjects_and_coverage(conn: sqlite3.Connection) -> None:
    dao = RunDao(conn)
    rid = dao.start(
        source_code="eastmoney", run_type=RunType.MARKET_AGGREGATE, caliber=Caliber.EASTMONEY,
        trade_date="2026-07-03", minute_slot="10:30", adapter_version="v2",
        subject_scope="all_stocks", asset_class_code="a_share")
    dao.finish(rid, status=RunStatus.SUCCESS, subjects_ok=5535, subjects_failed=0,
               aggregate_coverage={"coverage": 0.98, "n": 5400})
    row = dao.latest()
    assert row["subjects_ok"] == 5535 and row["asset_class_code"] == "a_share"
    assert "0.98" in row["aggregate_coverage"]
