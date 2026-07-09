"""FastAPI app 组装（contracts/service-api.md）。

启动初始化 DB + 同步交易日历 + 注册全部路由 + 异常处理器 + 挂载静态 web/。
scheduler_enabled=True 时 lifespan 拉起后台三频率采集调度线程（生产用；默认关）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from quantchive.api.errors import query_error_handler
from quantchive.api.routers import (
    agent,
    etf,
    flow,
    funds,
    health,
    market,
    meta,
    sectors,
    signals,
    stocks,
    subjects,
)
from quantchive.core.db import connect
from quantchive.core.logging import configure_logging, get_logger
from quantchive.core.settings import get_settings
from quantchive.core.trading_calendar import TradingCalendar
from quantchive.dao.db_init import init_db
from quantchive.service.errors import QueryError

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"
_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：建表 + seed 数据源 + 同步交易日历（幂等）
    settings = get_settings()
    conn = connect(settings.db_path)
    try:
        init_db(conn)
        try:
            n = TradingCalendar(conn).sync()
            _log.info("交易日历同步", extra={"context": {"rows": n}})
        except Exception as exc:  # 交易日历同步失败不阻塞启动（禁无声吞：记录）
            _log.warning("交易日历同步失败", extra={"context": {"err": str(exc)}})
    finally:
        conn.close()

    # 后台采集调度（单 worker；仅显式开启时）
    stop_event = thread = None
    if settings.scheduler_enabled:
        from quantchive.core.db import connect as _connect
        from quantchive.scheduler.jobs import Scheduler, default_jobs
        from quantchive.scheduler.runner import start_background
        cal_conn = _connect(settings.db_path)
        scheduler = Scheduler(jobs=default_jobs(settings), cal=TradingCalendar(cal_conn))
        thread, stop_event = start_background(
            db_path=settings.db_path, settings=settings, scheduler=scheduler)
        _log.info("采集调度器已启动")
    try:
        yield
    finally:
        if stop_event is not None:
            stop_event.set()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="QuantChive 通用市场观测平台", version="0.2.0", lifespan=lifespan)
    app.add_exception_handler(QueryError, query_error_handler)
    for r in (sectors, market, stocks, etf, funds, meta, subjects, health, flow, agent, signals):
        app.include_router(r.router)
    app.include_router(flow.topology_router)
    app.include_router(signals.scan_router)
    if _WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app


app = create_app()
