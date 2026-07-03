"""交易日历与时钟（宪章 I 确定性；contracts/ingestion.md）。

- trade_calendar 表由 akshare tool_trade_date_hist_sina() 同步、缓存落库。
- 交易日判定 / 交易时段(含午休) / 保留窗口 cutoff 全部以它为准（非"数据源返回空"猜测）。
- Clock 契约返回 aware datetime (Asia/Shanghai)，禁机器本地时区。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

# A股无夏令时，固定 +08:00
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# 交易时段（Asia/Shanghai），午休 11:30-13:00 不采
SESSIONS: tuple[tuple[time, time], ...] = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


class Clock(Protocol):
    """时钟契约：返回 aware datetime。可注入以测试（宪章 I 可复现）。"""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(SHANGHAI_TZ)


@dataclass(frozen=True)
class FixedClock:
    """测试用固定时钟。"""

    fixed: datetime

    def now(self) -> datetime:
        return self.fixed


def is_trading_time(dt: datetime) -> bool:
    """dt（aware，Asia/Shanghai）是否落在交易时段内。"""
    local = dt.astimezone(SHANGHAI_TZ).time()
    return any(start <= local <= end for start, end in SESSIONS)


def in_session_window(dt: datetime) -> bool:
    """是否处于 09:30-15:00 的大盘时段（含午休），用于调度器唤醒判断。"""
    local = dt.astimezone(SHANGHAI_TZ).time()
    return SESSIONS[0][0] <= local <= SESSIONS[-1][1]


class TradingCalendar:
    """基于 trade_calendar 表的交易日历。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def sync(self) -> int:
        """从 akshare 拉取交易日并 upsert（返回写入行数）。"""
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        rows = [(str(d), 1) for d in df["trade_date"].tolist()]
        self._conn.executemany(
            "INSERT INTO trade_calendar (trade_date, is_open) VALUES (?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET is_open=excluded.is_open",
            rows,
        )
        return len(rows)

    def is_trading_day(self, d: date | str) -> bool:
        key = d if isinstance(d, str) else d.isoformat()
        row = self._conn.execute(
            "SELECT is_open FROM trade_calendar WHERE trade_date = ?", (key,)
        ).fetchone()
        return bool(row and row[0])

    def latest_trading_day(self, on_or_before: date | str) -> str | None:
        key = on_or_before if isinstance(on_or_before, str) else on_or_before.isoformat()
        row = self._conn.execute(
            "SELECT trade_date FROM trade_calendar "
            "WHERE trade_date <= ? AND is_open = 1 ORDER BY trade_date DESC LIMIT 1",
            (key,),
        ).fetchone()
        return row[0] if row else None

    def retention_cutoff(self, today: date | str, retention_trade_days: int) -> str | None:
        """返回保留窗口的最早交易日：早于此日期的数据可清理。"""
        key = today if isinstance(today, str) else today.isoformat()
        row = self._conn.execute(
            "SELECT trade_date FROM trade_calendar "
            "WHERE trade_date <= ? AND is_open = 1 ORDER BY trade_date DESC LIMIT 1 OFFSET ?",
            (key, retention_trade_days - 1),
        ).fetchone()
        return row[0] if row else None
