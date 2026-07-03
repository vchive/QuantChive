"""开放式基金净值旁路源（T044，US4）。

`api.fund.eastmoney.com/f10/lsjz` 历史净值。**必带 Referer: fund.eastmoney.com**（否则 -999）。
返回 FSRQ(净值日期)/DWJZ(单位净值)/JZZZL(日增长率)，净值一律 Decimal 字符串（禁 float，宪章 III）。
本源**不入 registry、不进调度**——开放式基金按需实时查，不持久化（旁路物理隔离，D4/US4）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from quantchive.core.logging import get_logger
from quantchive.datasource.base import DataSourceError

_log = get_logger(__name__)

_LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
# 关键：lsjz 校验 Referer，缺失/错误返回 -999
_FUND_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://fund.eastmoney.com/",
}


@dataclass(frozen=True)
class FundNavPoint:
    nav_date: str          # FSRQ 净值日期 'YYYY-MM-DD'
    unit_nav: Decimal      # DWJZ 单位净值（Decimal，不丢精度）
    growth_pct: Decimal | None  # JZZZL 日增长率（%）


class FundSource:
    """开放式基金净值查询（旁路，不入库）。http_get 可注入（宪章 IV）。"""

    source_id = "eastmoney:fund-lsjz"

    def __init__(
        self,
        *,
        http_get: Callable[..., object] | None = None,
        timeout: float = 15.0,
    ) -> None:
        if http_get is None:
            import requests
            http_get = requests.get
        self._get = http_get
        self._timeout = timeout

    def fetch_nav(self, *, fund_code: str, days: int = 60) -> list[FundNavPoint]:
        """取基金近 days 条历史净值。失败抛结构化 DataSourceError。"""
        resp = self._get(
            _LSJZ_URL,
            params={"fundCode": fund_code, "pageIndex": "1", "pageSize": str(days)},
            headers=_FUND_HEADERS, timeout=self._timeout,
        )
        status = getattr(resp, "status_code", 200)
        if status != 200:
            raise DataSourceError(f"HTTP {status}", error_type="rate_limited")
        payload = resp.json() or {}
        # lsjz Referer 缺失 → ErrCode -999
        if payload.get("ErrCode") not in (0, None):
            raise DataSourceError(
                f"lsjz 错误码 {payload.get('ErrCode')}（疑缺 Referer）",
                error_type="schema_drift", detail={"err_code": payload.get("ErrCode")})
        data = payload.get("Data") or {}
        rows = data.get("LSJZList") or []
        out: list[FundNavPoint] = []
        for r in rows:
            fsrq = r.get("FSRQ")
            dwjz = r.get("DWJZ")
            if not fsrq or dwjz in (None, "", "-"):
                continue
            try:
                nav = Decimal(str(dwjz))
                jzzzl = r.get("JZZZL")
                growth = (Decimal(str(jzzzl)) if jzzzl not in (None, "", "-") else None)
            except (ArithmeticError, ValueError):
                continue
            out.append(FundNavPoint(nav_date=fsrq, unit_nav=nav, growth_pct=growth))
        return out
