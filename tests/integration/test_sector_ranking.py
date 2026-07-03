"""板块排行正确性（通用 subject×observation 模型）。

原为迁移对拍回归（T023，已证 SC-010 零回退）。Phase 8 收敛后旧 get_ranking 已删，
本测试保留其价值：验证 get_sector_ranking 的双榜/五档/多排序字段/provenance 正确性。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from quantchive.core.trading_calendar import FixedClock, SHANGHAI_TZ
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import Caliber, SectorType, SortField
from quantchive.service.errors import UnsupportedMetric
from quantchive.service.ingest_service import (
    CollectRequest,
    collect_sector_observations_once,
)
from quantchive.service.query_service import QueryService
from tests._fakes import FakeSectorObservationSource


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


@pytest.fixture
def svc(conn):
    clock = FixedClock(datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ))
    req = CollectRequest(caliber=Caliber.EASTMONEY, source_code="eastmoney",
                         sector_types=(SectorType.INDUSTRY,), adapter_version="test")
    collect_sector_observations_once(
        req, source=FakeSectorObservationSource(), observation_dao=ObservationDao(conn),
        run_dao=RunDao(conn), subject_dao=SubjectDao(conn), clock=clock)
    return QueryService(conn, retention_trade_days=7, source_id="fake:eastmoney", clock=clock)


@pytest.mark.parametrize("sort_by", [
    SortField.MAIN_NET, SortField.NET_AMOUNT, SortField.SUPER_LARGE_NET,
    SortField.LARGE_NET, SortField.MEDIUM_NET, SortField.SMALL_NET,
])
def test_sector_ranking_dual_board_five_tier(svc, sort_by) -> None:
    r = svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=sort_by, top_n=10, full=True)
    assert r.mode == "bipolar"
    assert r.total_subjects == 4
    assert r.has_five_tier is True
    # 双榜按符号切分：所有 inflow 该字段 > 0，所有 outflow < 0
    def val(it):
        return {
            SortField.MAIN_NET: it.main_net, SortField.NET_AMOUNT: it.net_amount,
            SortField.SUPER_LARGE_NET: it.super_large_net, SortField.LARGE_NET: it.large_net,
            SortField.MEDIUM_NET: it.medium_net, SortField.SMALL_NET: it.small_net,
        }[sort_by]
    assert all(val(it) > 0 for it in r.top_inflow)
    assert all(val(it) < 0 for it in r.top_outflow)


def test_sector_ranking_top_n_and_order(svc) -> None:
    r = svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.MAIN_NET, top_n=1)
    assert r.top_inflow[0].source_symbol == "半导体"   # 最大正
    assert r.top_outflow[0].source_symbol == "银行"    # 最负在前
    assert r.top_inflow[0].main_net == Decimal("500000000.00")  # 服务层为 Decimal（JSON 才字符串）


def test_sector_ranking_provenance_real_source(svc) -> None:
    r = svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.MAIN_NET, top_n=10)
    assert r.provenance.source_id == "fake:eastmoney"
    assert not r.provenance.source_id.startswith("akshare:")


def test_sector_ranking_rejects_price_sort(svc) -> None:
    with pytest.raises(UnsupportedMetric):
        svc.get_sector_ranking(caliber=Caliber.EASTMONEY, sector_type=SectorType.INDUSTRY,
                               sort_by=SortField.PRICE, top_n=10)
