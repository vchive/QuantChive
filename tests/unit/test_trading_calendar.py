"""T014: 交易日历与时段判断（宪章 I 确定性）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from quantchive.core.trading_calendar import (
    FixedClock,
    TradingCalendar,
    is_trading_time,
    SHANGHAI_TZ,
)
from quantchive.dao.db_init import init_db


@pytest.fixture
def cal() -> TradingCalendar:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_db(c)
    # 造一段交易日：周一至周五开，周末休（2026-06-29 一 ~ 07-03 五）
    days = [
        ("2026-06-29", 1), ("2026-06-30", 1), ("2026-07-01", 1),
        ("2026-07-02", 1), ("2026-07-03", 1),
        ("2026-06-27", 0), ("2026-06-28", 0),  # 周末
    ]
    c.executemany("INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, ?)", days)
    return TradingCalendar(c)


def test_is_trading_day(cal: TradingCalendar) -> None:
    assert cal.is_trading_day("2026-07-02") is True
    assert cal.is_trading_day("2026-06-27") is False  # 周六
    assert cal.is_trading_day("2026-01-01") is False  # 不在表中


def test_latest_trading_day_on_weekend(cal: TradingCalendar) -> None:
    # 周六 06-27 访问 → 回退到最近开市日 06-26... 不在表中；表内最近 <=06-27 的开市日是 06-26 无、故回退到更早
    # 表内 06-27/06-28 为休市，最近开市 <= 06-28 是 06-26? 06-26 不在表 → 06-25 也不在 → 返回表内 <=日期的最近开市日
    # 用可确定的断言：查 07-03(五) 得自身；查 07-04(六,不在表) 得 <=07-04 的最近开市 07-03
    assert cal.latest_trading_day("2026-07-03") == "2026-07-03"
    assert cal.latest_trading_day("2026-07-04") == "2026-07-03"
    # 周六 06-27（休市）→ 最近开市 <= 06-27 是 06-26（不在表）→ 落到表内 06-26 之前无 → None 或表内更早
    # 表里 <=06-27 的开市日：无（29/30/07-xx 都 >06-27）→ None
    assert cal.latest_trading_day("2026-06-27") is None


def test_retention_cutoff(cal: TradingCalendar) -> None:
    # 今天 07-03，保留 3 个交易日 → cutoff = 07-01 (07-03,07-02,07-01)
    assert cal.retention_cutoff("2026-07-03", 3) == "2026-07-01"


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ((10, 0), True),    # 上午盘中
        ((11, 30), True),   # 上午收盘边界
        ((12, 0), False),   # 午休
        ((13, 0), True),    # 下午开盘
        ((14, 59), True),
        ((15, 1), False),   # 收盘后
        ((9, 0), False),    # 开盘前
    ],
)
def test_is_trading_time(hhmm: tuple[int, int], expected: bool) -> None:
    dt = datetime(2026, 7, 2, hhmm[0], hhmm[1], tzinfo=SHANGHAI_TZ)
    assert is_trading_time(dt) is expected


def test_fixed_clock_is_aware() -> None:
    dt = datetime(2026, 7, 2, 10, 30, tzinfo=SHANGHAI_TZ)
    clk = FixedClock(dt)
    assert clk.now().tzinfo is not None
