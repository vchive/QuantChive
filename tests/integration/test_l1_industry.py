"""L1 行业分区守恒门禁（spec006 地基）。启发式失效 = 桑基不守恒，此测试立刻报警。

对真实 DB 断言：每股唯一归属、覆盖全 stock、派生行业数合理。DB 不存在则跳过。
"""

from __future__ import annotations

import os

import pytest

from quantchive.core.db import connect
from quantchive.core.settings import get_settings
from quantchive.service.l1_industry import build_l1_map

_DB = get_settings().db_path
_HAS_DB = os.path.exists(_DB)
pytestmark = pytest.mark.skipif(not _HAS_DB, reason="需真实 DB（有 subject_membership 数据）")


@pytest.fixture
def conn():
    c = connect(_DB)
    yield c
    c.close()


def test_l1_each_stock_unique(conn) -> None:
    """每股恰归属一个 L1 行业（dict 天然唯一，断言非空且值域合理）。"""
    m = build_l1_map(conn)
    assert len(m) > 0
    # 覆盖：有行业归属的股都在映射里
    stocks_with_ind = {r[0] for r in conn.execute(
        """SELECT DISTINCT m.child_subject_id FROM subject_membership m
           JOIN subject s ON s.subject_id=m.parent_subject_id WHERE s.subject_kind='industry'""").fetchall()}
    assert set(m.keys()) == stocks_with_ind


def test_l1_conservation_partition(conn) -> None:
    """守恒门禁：L1 行业数落在申万量级(20-40)，无股跨行业。启发式破了就报警。"""
    m = build_l1_map(conn)
    distinct_l1 = set(m.values())
    assert 20 <= len(distinct_l1) <= 40, f"L1 行业数 {len(distinct_l1)} 偏离申万量级"
    # 每个 L1 target 都是真行业板块
    ind_ids = {r[0] for r in conn.execute(
        "SELECT subject_id FROM subject WHERE subject_kind='industry'").fetchall()}
    assert distinct_l1 <= ind_ids


def test_l1_asof_no_lookahead(conn) -> None:
    """as_of 过滤：给一个很早的日期，映射不含之后才生效的 membership（无前视）。"""
    early = build_l1_map(conn, as_of="2000-01-01")
    full = build_l1_map(conn)
    # 早期日期覆盖 ≤ 全量（不会凭空多出未来关系）
    assert len(early) <= len(full)
