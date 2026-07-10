"""阶段D 基本面:适配(fake df 不联网)+ backfill + PIT 查询 + 端点。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from quantchive.dao.db_init import init_db
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.fundamental_src import (
    EastMoneyFundamentalSource,
    period_to_yyyymmdd,
    _scale,
)
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel
from quantchive.service.fundamental_service import FundamentalService
from quantchive.service.ingest_service import backfill_fundamentals


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


class _FakeRow(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class _FakeDF:
    """最小 df 替身:iterrows + columns + empty。"""
    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.empty = not rows

    def iterrows(self):
        for i, r in enumerate(self._rows):
            yield i, _FakeRow(r)


def _fake_fetch(rows_by_fn):
    def fetch(fn_name, date):
        return _FakeDF(rows_by_fn.get(fn_name, []))
    return fetch


# ---------- 适配/标度 ----------

def test_scale_units() -> None:
    assert _scale("cents", 150000000) == 15000000000    # 元→分 ×100
    assert _scale("bp", 50.23) == 5023                   # %→基点 ×100
    assert _scale("micro", 1.41) == 1410000              # 元→微元 ×1e6
    assert _scale("cents", "--") is None                 # 缺值不冒充0
    assert _scale("bp", None) is None


def test_period_to_yyyymmdd() -> None:
    assert period_to_yyyymmdd("2024Q4") == "20241231"
    assert period_to_yyyymmdd("2023Q1") == "20230331"


def test_adapter_normalizes_fake_df() -> None:
    rows = [{"股票代码": "002384", "最新公告日期": "2025-04-20",
             "每股收益": 1.41, "净利润-净利润": 70000000.0, "净资产收益率": 14.46}]
    src = EastMoneyFundamentalSource(fetch_df=_fake_fetch({"stock_yjbb_em": rows}))
    items = src.fetch_statement(statement="performance", report_period="2024Q4")
    by = {it.item: it for it in items}
    assert by["eps"].value_int == 1410000 and by["eps"].unit == "micro"
    assert by["net_profit"].value_int == 7000000000    # 元→分
    assert by["roe"].value_int == 1446                 # %→bp
    assert by["eps"].announce_date == "2025-04-20"


# ---------- backfill + PIT ----------

def _stock(conn, sym, name) -> int:
    sid, _ = SubjectDao(conn).upsert(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK, source_code="sina_flow",
        source_symbol=sym, display_name=name, exchange="SZSE")
    return sid


def test_backfill_and_latest(conn) -> None:
    sid = _stock(conn, "002384", "东山精密")
    rows = {"stock_yjbb_em": [{"股票代码": "002384", "最新公告日期": "2025-04-20",
            "每股收益": 1.41, "净资产收益率": 14.46}]}
    src = EastMoneyFundamentalSource(fetch_df=_fake_fetch(rows))
    res = backfill_fundamentals(conn, source=src, periods=["2024Q4"], statements=["performance"])
    assert res["items_written"] == 2
    fr = FundamentalService(conn).latest(subject_id=sid)
    assert fr.report_period == "2024Q4" and fr.announce_date == "2025-04-20"
    assert "业绩" in fr.sections and "每股收益" in fr.sections["业绩"]


def test_pit_excludes_future_announce(conn) -> None:
    """as_of 早于公告日 → 该报告期不可见(无前视)。"""
    sid = _stock(conn, "002384", "东山精密")
    rows = {"stock_yjbb_em": [{"股票代码": "002384", "最新公告日期": "2025-04-20",
            "每股收益": 1.41}]}
    src = EastMoneyFundamentalSource(fetch_df=_fake_fetch(rows))
    backfill_fundamentals(conn, source=src, periods=["2024Q4"], statements=["performance"])
    from quantchive.service.errors import NoDataForDate
    with pytest.raises(NoDataForDate):
        FundamentalService(conn).latest(subject_id=sid, as_of="2025-01-01")  # 公告前


def test_backfill_skips_unknown_code(conn) -> None:
    """code 无对应 subject → 跳过不落(宁缺勿假)。"""
    rows = {"stock_yjbb_em": [{"股票代码": "999999", "每股收益": 1.0}]}
    src = EastMoneyFundamentalSource(fetch_df=_fake_fetch(rows))
    res = backfill_fundamentals(conn, source=src, periods=["2024Q4"], statements=["performance"])
    assert res["items_written"] == 0 and res["skipped_no_subject"] >= 1


def test_fundamentals_endpoint(conn) -> None:
    sid = _stock(conn, "002384", "东山精密")
    rows = {"stock_yjbb_em": [{"股票代码": "002384", "最新公告日期": "2025-04-20",
            "每股收益": 1.41, "净资产收益率": 14.46}]}
    src = EastMoneyFundamentalSource(fetch_df=_fake_fetch(rows))
    backfill_fundamentals(conn, source=src, periods=["2024Q4"], statements=["performance"])
    from fastapi.testclient import TestClient
    from quantchive.app import create_app
    from quantchive.api.deps import get_conn
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    c = TestClient(app)
    r = c.get(f"/api/subjects/{sid}/fundamentals")
    assert r.status_code == 200
    j = r.json()
    assert j["report_period"] == "2024Q4" and "业绩" in j["sections"]
