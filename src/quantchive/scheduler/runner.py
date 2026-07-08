"""采集调度执行器（T059）。tick_once 可测；run_loop 为后台线程 sleep 循环。"""

from __future__ import annotations

import sqlite3
import threading
import time as _time
from datetime import datetime
from typing import Callable

from quantchive.core.logging import get_logger
from quantchive.core.settings import Settings
from quantchive.core.trading_calendar import SHANGHAI_TZ, Clock, SystemClock
from quantchive.scheduler.jobs import Scheduler, run_retention, run_target

_log = get_logger(__name__)

RunTargetFn = Callable[[sqlite3.Connection, str], object]


def tick_once(
    scheduler: Scheduler,
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    now: datetime,
    now_epoch: float,
    run_target_fn: RunTargetFn = run_target,
) -> dict:
    """执行一个调度 tick：跑到点的盘中 JOB + EOD + 保留。返回本 tick 摘要（可测）。"""
    ran: list[str] = []
    # 1) 盘中三频率
    for job in scheduler.due_intraday(now=now, now_epoch=now_epoch):
        try:
            run_target_fn(conn, job.target)
            scheduler.mark_ran(job.name, now_epoch)
            ran.append(job.name)
        except Exception as exc:  # 单 JOB 失败不阻塞其他（断点=缺行）
            _log.warning("JOB 失败", extra={"context": {"job": job.name, "err": str(exc)}})
            scheduler.mark_ran(job.name, now_epoch)  # 仍记时避免热重试风暴
    # 2) EOD 补全
    eod = False
    if scheduler.due_eod(now):
        try:
            run_target_fn(conn, "sector")  # 收盘补板块日终（个股/ETF 同理可扩展）
        except Exception as exc:
            _log.warning("EOD 失败", extra={"context": {"err": str(exc)}})
        # 板块四档由成分股当日四档求和派生（写 is_derived daily_final 行,供板块博弈图）
        try:
            from quantchive.service.ingest_service import derive_sector_tiers
            r = derive_sector_tiers(conn)
            _log.info("板块四档派生", extra={"context": r})
        except Exception as exc:
            _log.warning("板块派生失败", extra={"context": {"err": str(exc)}})
        scheduler.mark_eod_done(now)
        eod = True
    # 3) 保留降采 + 清理
    retention = False
    if scheduler.due_retention(now):
        as_of = now.astimezone(SHANGHAI_TZ).date().isoformat()
        try:
            run_retention(conn, settings, as_of_date=as_of)
        except Exception as exc:
            _log.warning("保留任务失败", extra={"context": {"err": str(exc)}})
        scheduler.mark_retention_done(now)
        retention = True
    return {"ran": ran, "eod": eod, "retention": retention}


def run_loop(
    *,
    db_path: str,
    settings: Settings,
    scheduler: Scheduler,
    stop_event: threading.Event,
    clock: Clock | None = None,
    tick_sec: int = 5,
) -> None:
    """后台线程循环：每 tick_sec 秒醒来跑一次 tick_once，直到 stop_event 置位。

    单 worker（SQLite 单写者约束）；自建线程本地 conn。
    """
    from quantchive.core.db import connect
    from quantchive.core.trading_calendar import TradingCalendar

    clock = clock or SystemClock()
    conn = connect(db_path)
    # 交易日历必须绑定本线程的 conn（SQLite 连接不可跨线程）——重建避免用到主线程连接
    scheduler.cal = TradingCalendar(conn)
    _log.info("调度器启动", extra={"context": {"jobs": [j.name for j in scheduler.jobs]}})
    try:
        while not stop_event.is_set():
            now = clock.now()
            tick_once(scheduler, conn, settings, now=now, now_epoch=_time.time())
            stop_event.wait(tick_sec)
    finally:
        conn.close()
        _log.info("调度器停止")


def start_background(
    *, db_path: str, settings: Settings, scheduler: Scheduler,
) -> tuple[threading.Thread, threading.Event]:
    """起后台调度线程，返回 (thread, stop_event)。lifespan 用。"""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_loop,
        kwargs={"db_path": db_path, "settings": settings, "scheduler": scheduler,
                "stop_event": stop_event},
        name="quantchive-scheduler", daemon=True)
    thread.start()
    return thread, stop_event
