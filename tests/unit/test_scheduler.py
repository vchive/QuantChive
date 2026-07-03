"""T059: 调度决策（交易时段门禁、三频率到点、EOD/保留一次性）+ tick_once 执行。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from quantchive.core.settings import Settings
from quantchive.core.trading_calendar import SHANGHAI_TZ, TradingCalendar
from quantchive.dao.db_init import init_db
from quantchive.scheduler.jobs import Scheduler, default_jobs, is_collection_time
from quantchive.scheduler.runner import tick_once


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_db(c)
    # 2026-07-02 交易日；2026-07-04 周六非交易日
    c.execute("INSERT INTO trade_calendar (trade_date, is_open) VALUES ('2026-07-02', 1)")
    return c


def _dt(h, m, day=2):
    return datetime(2026, 7, day, h, m, tzinfo=SHANGHAI_TZ)


def _scheduler(conn):
    return Scheduler(jobs=default_jobs(Settings()), cal=TradingCalendar(conn))


def test_is_collection_time_gates(conn) -> None:
    cal = TradingCalendar(conn)
    assert is_collection_time(_dt(10, 30), cal) is True      # 交易日盘中
    assert is_collection_time(_dt(8, 0), cal) is False        # 盘前
    assert is_collection_time(_dt(16, 0), cal) is False       # 盘后
    assert is_collection_time(_dt(10, 30, day=4), cal) is False  # 非交易日(无日历行)


def test_due_intraday_only_during_session(conn) -> None:
    sch = _scheduler(conn)
    # 盘前：无 due
    assert sch.due_intraday(now=_dt(8, 0), now_epoch=1000.0) == []
    # 盘中首次：三 JOB 全 due
    due = sch.due_intraday(now=_dt(10, 30), now_epoch=1000.0)
    assert {j.name for j in due} == {"minute_1min", "stock_5min", "etf_5min"}


def test_due_intraday_respects_interval(conn) -> None:
    sch = _scheduler(conn)
    now_epoch = 1000.0
    for j in sch.due_intraday(now=_dt(10, 30), now_epoch=now_epoch):
        sch.mark_ran(j.name, now_epoch)
    # 30s 后：板块(60s)未到、个股/ETF(300s)未到 → 无
    assert sch.due_intraday(now=_dt(10, 30), now_epoch=now_epoch + 30) == []
    # 61s 后：仅板块 1min 到点
    due = sch.due_intraday(now=_dt(10, 31), now_epoch=now_epoch + 61)
    assert {j.name for j in due} == {"minute_1min"}
    # 301s 后：三个都到
    due2 = sch.due_intraday(now=_dt(10, 35), now_epoch=now_epoch + 301)
    assert {j.name for j in due2} == {"minute_1min", "stock_5min", "etf_5min"}


def test_eod_once_per_day(conn) -> None:
    sch = _scheduler(conn)
    assert sch.due_eod(_dt(14, 0)) is False       # 15:05 前
    assert sch.due_eod(_dt(15, 10)) is True        # 15:05 后
    sch.mark_eod_done(_dt(15, 10))
    assert sch.due_eod(_dt(15, 20)) is False        # 当日已补


def test_retention_once_per_day(conn) -> None:
    sch = _scheduler(conn)
    assert sch.due_retention(_dt(15, 0)) is False   # 15:30 前
    assert sch.due_retention(_dt(15, 40)) is True    # 15:30 后
    sch.mark_retention_done(_dt(15, 40))
    assert sch.due_retention(_dt(16, 0)) is False


def test_tick_once_runs_due_jobs_via_injected_runner(conn) -> None:
    """tick_once 派发 due JOB，用注入 runner 避免联网。"""
    sch = _scheduler(conn)
    calls = []
    result = tick_once(sch, conn, Settings(), now=_dt(10, 30), now_epoch=1000.0,
                       run_target_fn=lambda c, target: calls.append(target))
    assert set(result["ran"]) == {"minute_1min", "stock_5min", "etf_5min"}
    assert set(calls) == {"sector", "stock", "etf"}
    # 立即再 tick（同 epoch）→ 无重复
    result2 = tick_once(sch, conn, Settings(), now=_dt(10, 30), now_epoch=1000.0,
                        run_target_fn=lambda c, target: calls.append(target))
    assert result2["ran"] == []


def test_tick_job_failure_isolated(conn) -> None:
    """单 JOB 抛错不阻塞其他，且仍记时避免热重试。"""
    sch = _scheduler(conn)

    def flaky(c, target):
        if target == "stock":
            raise RuntimeError("限流")

    result = tick_once(sch, conn, Settings(), now=_dt(10, 30), now_epoch=1000.0,
                       run_target_fn=flaky)
    # 隔离：stock 失败不阻塞 minute/etf（二者成功执行）
    assert "minute_1min" in result["ran"] and "etf_5min" in result["ran"]
    assert "stock_5min" not in result["ran"]     # 失败未计入成功列表
    # 但失败 JOB 仍记时（避免热重试）：同 epoch 再 tick 无重复
    result2 = tick_once(sch, conn, Settings(), now=_dt(10, 30), now_epoch=1000.0,
                        run_target_fn=flaky)
    assert result2["ran"] == []
