"""东财 clist 通用客户端（从 akshare_src 抽出）。

- push2delay 主域名（抗限流）+ push2 回退。
- 翻页：单页硬顶 100，动态翻至 ceil(total/100)（5535 股需 56 页，绝不静默截断）。
- **翻页页间随机节流 + 退避随机抖动**（spec003 D1，防高频打限流）。
- 重试退避、结构化 DataSourceError、http_get/sleep/rng 可注入（宪章 IV）。
返回 data.diff 原始行（含 f-code），映射交给各 Source。
"""

from __future__ import annotations

import random
import time as _time
from math import ceil
from typing import Callable

from quantchive.core.logging import get_logger
from quantchive.datasource._http_client import backoff_delay
from quantchive.datasource.base import DataSourceError

_log = get_logger(__name__)

_EM_HOSTS = ("https://push2delay.eastmoney.com", "https://push2.eastmoney.com")
_EM_PATH = "/api/qt/clist/get"
_EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
_PAGE_CAP = 100  # 东财 clist 单页硬顶


class EmClient:
    """东财 clist 翻页客户端。fs/fields 由调用方传入（主体无关）。"""

    def __init__(
        self,
        *,
        http_get: Callable[..., object] | None = None,
        max_retries: int = 3,
        backoff_base: float = 3.0,
        backoff_cap: float = 20.0,
        backoff_jitter: float = 1.5,
        timeout: float = 15.0,
        page_size: int = _PAGE_CAP,
        max_pages: int | None = None,
        page_sleep: tuple[float, float] = (0.5, 1.5),
        sleep: Callable[[float], None] = _time.sleep,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if http_get is None:
            import requests
            http_get = requests.get
        self._get = http_get
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._backoff_jitter = backoff_jitter
        self._timeout = timeout
        # 单页不得超东财硬顶 100（传更大也强制收敛，防只拿首页）
        self._page_size = min(page_size, _PAGE_CAP)
        self._max_pages = max_pages   # None=动态 ceil(total/pz)；仅测试/保护性封顶用
        self._page_sleep = page_sleep  # 翻页页间随机节流区间（防限流，D1）
        self._sleep = sleep
        self._rng = rng

    def _params(self, fs: str, fields: str, fid: str, page: int) -> dict:
        return {
            "pn": str(page), "pz": str(self._page_size), "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": fid,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fs": fs, "fields": fields,
        }

    def _get_page(self, host: str, fs: str, fields: str, fid: str, page: int) -> tuple[list[dict], int]:
        resp = self._get(host + _EM_PATH, params=self._params(fs, fields, fid, page),
                         headers=_EM_HEADERS, timeout=self._timeout)
        status = getattr(resp, "status_code", 200)
        if status != 200:
            raise DataSourceError(f"HTTP {status}", error_type="rate_limited")
        data = (resp.json() or {}).get("data")
        if not isinstance(data, dict):
            raise DataSourceError("空返回(data)", error_type="empty_or_dash")
        total = int(data.get("total") or 0)
        diff = data.get("diff")
        rows = (list(diff.values()) if isinstance(diff, dict)
                else list(diff) if diff else [])
        return rows, total

    def _fetch_all_pages(self, fs: str, fields: str, fid: str) -> tuple[list[dict], int]:
        """翻页取全部行，返回 (rows, total)。动态翻至 ceil(total/pz)——个股 5535 需 56 页。"""
        last_exc: Exception | None = None
        for host in _EM_HOSTS:
            try:
                first, total = self._get_page(host, fs, fields, fid, page=1)
                if not first:
                    raise DataSourceError("空返回(data.diff)", error_type="empty_or_dash")
                rows = list(first)
                # 动态页数上限：优先 total 推算，回落到已取行数；保护性封顶 self._max_pages
                need_pages = ceil(total / self._page_size) if total else 1
                if self._max_pages is not None:
                    need_pages = min(need_pages, self._max_pages)
                page = 2
                while len(rows) < total and page <= need_pages:
                    self._sleep(self._rng(*self._page_sleep))  # 页间随机节流（防限流，D1）
                    more, _ = self._get_page(host, fs, fields, fid, page=page)
                    if not more:
                        break
                    rows.extend(more)
                    page += 1
                return rows, total
            except DataSourceError:
                raise
            except Exception as exc:
                last_exc = exc
                _log.info("东财主机切换/失败", extra={"context": {"host": host,
                          "err": type(exc).__name__}})
                continue
        raise DataSourceError(
            f"东财所有主机失败: {last_exc}", error_type="timeout",
            detail={"exc": type(last_exc).__name__ if last_exc else None},
        )

    def fetch_diff(self, *, fs: str, fields: str, fid: str = "f62") -> list[dict]:
        """取 clist 全部行（含重试退避）。fid=排序字段（默认主力净额 f62）。"""
        return self.fetch_with_total(fs=fs, fields=fields, fid=fid)[0]

    def fetch_with_total(
        self, *, fs: str, fields: str, fid: str = "f62"
    ) -> tuple[list[dict], int]:
        """取 clist 全部行 + 东财报告的 total（含重试退避）。

        total 供大盘求和覆盖率门禁（constituent/expected）——翻页静默截断时 total>len(rows)，
        使覆盖率<95% 不落假大盘（D2 宁缺勿假）。
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return self._fetch_all_pages(fs, fields, fid)
            except DataSourceError as exc:
                if exc.error_type in ("unsupported", "empty_or_dash"):
                    raise
                last_exc = exc
                if attempt < self._max_retries - 1:
                    delay = backoff_delay(
                        attempt, base=self._backoff_base, cap=self._backoff_cap,
                        jitter=self._backoff_jitter, rng=self._rng)  # 退避加抖动（D1）
                    _log.info("东财 clist 重试", extra={"context": {
                        "attempt": attempt + 1, "delay": round(delay, 2),
                        "err": type(exc).__name__}})
                    self._sleep(delay)
        raise DataSourceError(
            f"东财 clist 失败(重试{self._max_retries}次): {last_exc}",
            error_type="rate_limited",
            detail={"exc": type(last_exc).__name__ if last_exc else None},
        )
