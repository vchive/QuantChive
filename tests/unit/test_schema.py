"""数据源 seed 校验（新表 schema 约束见 test_schema_v2）。"""

from __future__ import annotations

import sqlite3

import pytest

from quantchive.dao.db_init import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON;")
    init_db(c)
    return c


def test_data_sources_seeded(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT source_code, amount_unit FROM data_source ORDER BY source_code").fetchall()
    codes = {r["source_code"]: r["amount_unit"] for r in rows}
    # spec002：eastmoney(元) + ths(亿) + em_fund(元)
    assert codes["eastmoney"] == "yuan"
    assert codes["ths"] == "yi"
