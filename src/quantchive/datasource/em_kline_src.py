"""东财历史日线资金流源（历史回填）。

`push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` 返回个股/板块的历史每日五档
净额（主力/超大/大/中/小，单位元），可回溯约 120 交易日。用于补齐时序历史深度——
盘中快照只能向前积累，历史日线是唯一能拿到"过去"的路径。

klines CSV 字段序（实测）：日期, 主力(f52), 小单(f53), 中单(f54), 大单(f55), 超大单(f56), ...
secid：个股沪 1.600xxx / 深 0.000xxx；板块 90.BK{code}。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from quantchive.core.logging import get_logger
from quantchive.core.money import AmountParseError
from quantchive.datasource.base import DataSourceError

_log = get_logger(__name__)

_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_URL_FALLBACK = "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


@dataclass(frozen=True)
class DailyFlowPoint:
    """某交易日的历史五档净额（元）。"""

    trade_date: str          # 'YYYY-MM-DD'
    main_net: Decimal        # 主力
    super_large_net: Decimal
    large_net: Decimal
    medium_net: Decimal
    small_net: Decimal


def _num(raw: object) -> Decimal:
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        raise AmountParseError(f"non-numeric: {text!r}")
    return Decimal(text)


def secid_for_stock(code: str, exchange: str | None) -> str:
    """个股 secid：SSE→1.，SZSE/BSE→0.（北交所东财 fflow 归 market 0）。"""
    market = "1" if exchange == "SSE" else "0"
    return f"{market}.{code}"


def secid_for_board(em_board_code: str) -> str:
    """板块 secid：90.BK{code}。

    注意：东财 fflow/daykline 对板块 secid 只返回当日一条（板块历史资金流不由此端点提供）。
    板块日线历史后续应由成分个股日线求和派生，或改接板块专用历史端点。
    """
    return f"90.{em_board_code}"


class EastMoneyDailyFlowSource:
    """东财历史日线资金流。http_get 可注入（宪章 IV）。"""

    source_id = "eastmoney:fflow-daykline"
    adapter_version = "fflow-daykline-v1"

    def __init__(
        self,
        *,
        http_get: Callable[..., object] | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        if http_get is None:
            import requests
            http_get = requests.get
        self._get = http_get
        self._timeout = timeout
        self._max_retries = max_retries

    def fetch_daily_flow(self, *, secid: str, days: int = 30) -> list[DailyFlowPoint]:
        """取该 secid 近 days 交易日的历史五档净额，按日期升序。lmt=0 取全量后截尾。"""
        last_exc: Exception | None = None
        for host in (_URL, _URL_FALLBACK):
            for attempt in range(self._max_retries):
                try:
                    return self._fetch(host, secid, days)
                except DataSourceError:
                    raise
                except Exception as exc:  # 网络抖动重试
                    last_exc = exc
                    continue
        raise DataSourceError(
            f"历史日线所有主机失败: {last_exc}", error_type="timeout",
            detail={"secid": secid})

    def _fetch(self, host: str, secid: str, days: int) -> list[DailyFlowPoint]:
        resp = self._get(
            host,
            params={"lmt": "0", "klt": "101", "fields1": "f1,f2,f3,f7",
                    "fields2": _FIELDS2, "secid": secid,
                    "ut": "b2884a393a59ad64002292a3e90d46a5"},
            headers=_HEADERS, timeout=self._timeout,
        )
        status = getattr(resp, "status_code", 200)
        if status != 200:
            raise DataSourceError(f"HTTP {status}", error_type="rate_limited")
        data = (resp.json() or {}).get("data")
        if not isinstance(data, dict):
            raise DataSourceError("空返回(data)", error_type="empty_or_dash")
        klines = data.get("klines") or []
        out: list[DailyFlowPoint] = []
        for row in klines:
            parts = str(row).split(",")
            if len(parts) < 6:
                continue
            try:
                out.append(DailyFlowPoint(
                    trade_date=parts[0],
                    main_net=_num(parts[1]),
                    small_net=_num(parts[2]),
                    medium_net=_num(parts[3]),
                    large_net=_num(parts[4]),
                    super_large_net=_num(parts[5]),
                ))
            except AmountParseError:
                continue  # 单日缺失跳过
        return out[-days:] if days and len(out) > days else out
