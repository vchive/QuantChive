"""东财全市场个股观测源（T029，US1 大盘求和的数据基础）。

fs=全市场个股（沪深京 A 股 5535），翻页 ceil(total/100)≈56 页动态。每只个股取五档资金流
+ 价/涨跌幅/量/成交额/流通市值。写入用 intraday_latest(LATEST 覆盖，D4)，供大盘求和+当前排行。
翻页 total 经 last_total 暴露，供大盘覆盖率门禁（D2）。串行低并发、页间抖动 sleep。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable, Sequence

from quantchive.core.logging import get_logger
from quantchive.core.money import AmountParseError
from quantchive.datasource._em_client import EmClient
from quantchive.datasource.base import (
    DataSourceError,
    FetchSpec,
    SubjectCapability,
    SubjectRef,
)
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SortField,
    SubjectKind,
    SubjectLevel,
)

_log = get_logger(__name__)

# 全市场个股 fs（沪 m:1 t:2/t:23、深 m:0 t:6/t:80，实测 total≈5535）
STOCK_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
# f-code：身份 f12代码/f14名；价量 f2价/f3涨跌幅/f5量/f6额/f21流通市值；五档 f62/f66/f72/f78/f84
_STOCK_FIELDS = ("f12", "f14", "f2", "f3", "f5", "f6", "f21",
                 "f62", "f66", "f72", "f78", "f84")
_STOCK_SORT = (
    SortField.MAIN_NET, SortField.SUPER_LARGE_NET, SortField.LARGE_NET,
    SortField.MEDIUM_NET, SortField.SMALL_NET, SortField.CHANGE_PCT,
    SortField.PRICE, SortField.VOLUME,
)
_STOCK_METRICS = ("main_net", "super_large_net", "large_net", "medium_net",
                  "small_net", "price", "change_pct", "volume", "turnover", "circ_mktcap")


def _num(raw: object) -> Decimal | None:
    """东财数值 → Decimal；空/'-'/NaN → None（该指标缺失，不造假）。禁 Decimal(float)。"""
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        raise AmountParseError(f"non-numeric: {text!r}")


class EastMoneyStockSource:
    """东财全市场个股适配（ObservationSource）。"""

    source_id = "eastmoney:push2delay"
    caliber = Caliber.EASTMONEY
    adapter_version = "push2delay-stock-v1"

    def __init__(
        self,
        *,
        http_get: Callable[..., object] | None = None,
        max_retries: int = 3,
        timeout: float = 15.0,
        page_size: int = 100,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        import time as _time
        self._client = EmClient(
            http_get=http_get, max_retries=max_retries, timeout=timeout,
            page_size=page_size, sleep=sleep or _time.sleep,
        )
        self.last_total = 0  # 东财报告的 total（覆盖率门禁 expected，D2）

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        return SubjectCapability(
            asset_class=AssetClass.A_SHARE, subject_kind=SubjectKind.STOCK, has_five_tier=True,
            supported_metrics=_STOCK_METRICS, available_sort_fields=_STOCK_SORT,
            series_granularity="5min",
        )

    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]:
        rows, total = self._client.fetch_with_total(fs=spec.fs, fields=",".join(_STOCK_FIELDS))
        self.last_total = total
        refs: list[SubjectRef] = []
        for row in rows:
            code, name = row.get("f12"), row.get("f14")
            if not code or not name:
                continue
            refs.append(SubjectRef(
                source_symbol=str(code).strip(), display_name=str(name).strip(),
                asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                subject_kind=SubjectKind.STOCK, exchange=_exchange_of(str(code)),
            ))
        return refs

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        rows, total = self._client.fetch_with_total(fs=spec.fs, fields=",".join(_STOCK_FIELDS))
        self.last_total = total
        out: list[RawObservation] = []
        for row in rows:
            obs = self._row_to_observation(row)
            if obs is not None:
                out.append(obs)
        return out

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        raise DataSourceError("个股无成员", error_type="unsupported")

    def fetch_daily_final(self, spec: FetchSpec, trade_date: date) -> Sequence[RawObservation]:
        return self.fetch_observations(spec)

    def _row_to_observation(self, row: dict) -> RawObservation | None:
        code = row.get("f12")
        name = row.get("f14")
        if not code or not name:
            return None
        try:
            main = _num(row.get("f62"))
            xl = _num(row.get("f66"))
            lg = _num(row.get("f72"))
            md = _num(row.get("f78"))
            sm = _num(row.get("f84"))
            price = _num(row.get("f2"))
            change_pct = _num(row.get("f3"))
            volume = _num(row.get("f5"))
            turnover = _num(row.get("f6"))
            circ = _num(row.get("f21"))
        except AmountParseError as exc:
            _log.info("个股行跳过", extra={"context": {"code": code, "reason": str(exc)}})
            return None
        # 五档 all-or-nothing：任一档缺失则整体判无五档（不写半档，破 schema CHECK）
        tiers = [main, xl, lg, md, sm]
        if any(t is None for t in tiers):
            main = xl = lg = md = sm = None
        metrics = {"circ_mktcap": circ} if circ is not None else {}
        # 无任何核心指标 → 跳过（防空壳行）
        if main is None and price is None and volume is None:
            return None
        return RawObservation(
            source_symbol=str(code).strip(), display_name=str(name).strip(),
            asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
            subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
            main_net=main, super_large_net=xl, large_net=lg, medium_net=md, small_net=sm,
            price=price, change_pct=change_pct, volume=volume, turnover=turnover,
            metrics=metrics, exchange=_exchange_of(str(code)), source_unit=AmountUnit.YUAN,
        )


def _exchange_of(code: str) -> str | None:
    """按代码前缀判交易所（6=沪, 0/3=深, 4/8=北）。"""
    if code[:1] == "6":
        return "SSE"
    if code[:1] in ("0", "3"):
        return "SZSE"
    if code[:1] in ("4", "8"):
        return "BSE"
    return None
