"""三频率错峰采集 JOB + 调度决策核心（T059，contracts/ingestion.md D3/D4）。

三 JOB（相位错峰，仅交易日 09:30-15:00）：
- minute_1min（相位0s）：板块 → intraday_snapshot 1min。
- stock_5min（相位20s）：全市场个股 → intraday_latest(LATEST 覆盖) + 触发大盘求和。
- etf_5min（相位40s）：ETF → intraday_snapshot 5min。
盘后：EOD 补全（15:05）、保留降采 + 清理（每日一次）。

`Scheduler.due()` 是纯决策（给定 now + 各 JOB 上次运行 epoch），可单测；实际 sleep 循环在 runner。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from quantchive.core.settings import Settings
from quantchive.core.trading_calendar import (
    SHANGHAI_TZ,
    TradingCalendar,
    in_session_window,
)
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import Caliber, SectorType
from quantchive.service.ingest_service import (
    CollectRequest,
    collect_etf_observations_once,
    collect_sector_observations_once,
    collect_stock_observations_once,
    retention_cleanup,
    retention_downsample,
)
from quantchive.service.market_aggregate import MarketAggregator


@dataclass(frozen=True)
class JobSpec:
    name: str
    interval_sec: int
    phase_offset_sec: int
    target: str            # 'sector' | 'stock' | 'etf'


def default_jobs(settings: Settings) -> list[JobSpec]:
    return [
        JobSpec("minute_1min", settings.minute_interval_sec, 0, "sector"),
        JobSpec("stock_5min", settings.coarse_interval_sec, 20, "stock"),
        JobSpec("etf_5min", settings.coarse_interval_sec, 40, "etf"),
    ]


def run_target(conn: sqlite3.Connection, target: str) -> object:
    """按 target 调对应 collector（工厂在此，源默认东财直连）。"""
    from quantchive.core.settings import get_settings
    from quantchive.datasource.em_etf_src import EastMoneyEtfSource
    from quantchive.datasource.em_sector_src import EastMoneySource
    from quantchive.datasource._http_client import default_http_get
    from quantchive.datasource.em_stock_src import EastMoneyStockSource

    settings = get_settings()
    if target == "sector":
        req = CollectRequest(
            caliber=Caliber.EASTMONEY, source_code="eastmoney",
            sector_types=(SectorType.INDUSTRY, SectorType.CONCEPT),
            adapter_version="push2delay-v2")
        return collect_sector_observations_once(
            req, source=EastMoneySource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
            run_dao=RunDao(conn), subject_dao=SubjectDao(conn))
    if target == "stock":
        return collect_stock_observations_once(
            source=EastMoneyStockSource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
            run_dao=RunDao(conn), subject_dao=SubjectDao(conn),
            aggregator=MarketAggregator(conn, threshold=settings.market_coverage_threshold),
            adapter_version="push2delay-stock-v1")
    if target == "etf":
        return collect_etf_observations_once(
            source=EastMoneyEtfSource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
            subject_dao=SubjectDao(conn), run_dao=RunDao(conn),
            adapter_version="push2delay-etf-v1")
    raise ValueError(f"未知 target={target}")


def is_collection_time(now: datetime, cal: TradingCalendar) -> bool:
    """是否交易日的盘中时段（09:30-15:00，含午休停采由 JOB 内部快照跳过）。"""
    local = now.astimezone(SHANGHAI_TZ)
    return cal.is_trading_day(local.date().isoformat()) and in_session_window(now)


# 盘后触发时刻（Asia/Shanghai）
_EOD_HHMM = (15, 5)        # 15:05 EOD 补全
_RETENTION_HHMM = (15, 30)  # 15:30 保留降采 + 清理


@dataclass
class Scheduler:
    """采集调度决策（纯逻辑：给定 now + 状态 → 该跑哪些）。实际执行/sleep 在 runner。"""

    jobs: list[JobSpec]
    cal: TradingCalendar
    _last_run: dict[str, float] = field(default_factory=dict)   # job name → epoch
    _eod_done_date: str | None = None
    _retention_done_date: str | None = None

    def due_intraday(self, *, now: datetime, now_epoch: float) -> list[JobSpec]:
        """盘中到点该跑的 JOB（非交易时段返回空）。"""
        if not is_collection_time(now, self.cal):
            return []
        due = []
        for job in self.jobs:
            last = self._last_run.get(job.name)
            if last is None or (now_epoch - last) >= job.interval_sec:
                due.append(job)
        return due

    def mark_ran(self, job_name: str, now_epoch: float) -> None:
        self._last_run[job_name] = now_epoch

    def due_eod(self, now: datetime) -> bool:
        """交易日 15:05 后、当日尚未补全 → 触发 EOD。"""
        local = now.astimezone(SHANGHAI_TZ)
        today = local.date().isoformat()
        if not self.cal.is_trading_day(today):
            return False
        after = (local.hour, local.minute) >= _EOD_HHMM
        return after and self._eod_done_date != today

    def mark_eod_done(self, now: datetime) -> None:
        self._eod_done_date = now.astimezone(SHANGHAI_TZ).date().isoformat()

    def due_retention(self, now: datetime) -> bool:
        """15:30 后、当日尚未做过保留降采/清理 → 触发（每日一次，交易日与否都可）。"""
        local = now.astimezone(SHANGHAI_TZ)
        today = local.date().isoformat()
        after = (local.hour, local.minute) >= _RETENTION_HHMM
        return after and self._retention_done_date != today

    def mark_retention_done(self, now: datetime) -> None:
        self._retention_done_date = now.astimezone(SHANGHAI_TZ).date().isoformat()


def run_retention(conn: sqlite3.Connection, settings: Settings, *, as_of_date: str) -> dict:
    """盘后保留：先降采（7天前分钟→小时）再清理（删 30 天前）。"""
    down = retention_downsample(
        conn, as_of_date=as_of_date, downsample_after_days=settings.downsample_after_days)
    clean = retention_cleanup(
        conn, as_of_date=as_of_date, retention_trade_days=settings.retention_trade_days)
    return {"downsample": down, "cleanup": clean}
