"""东财 ETF 观测源（T043，US4）。

fs=b:MK0021..MK0024 全采 ETF(≈1521)。ETF **无五档资金流**（money_flow 列全 NULL），
只有价/涨跌/量/成交额 + f21 流通市值。诚实命名：f21 是 **circ_mktcap(流通市值)**，
仅作规模 proxy，**不冒充 aum(基金资产净值规模)**——两者口径不同（T042）。circ_mktcap 入
稀疏附表 observation_metric。
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

# ETF 板块 fs（MK0021 沪ETF / MK0022 深ETF / MK0023..24 其他，实测合计≈1521）
ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"
_ETF_FIELDS = ("f12", "f14", "f2", "f3", "f5", "f6", "f21")
# ETF 可排序：涨跌幅（is_sortable=1）；价/量为展示列。无五档。
_ETF_SORT = (SortField.CHANGE_PCT,)
_ETF_METRICS = ("price", "change_pct", "volume", "turnover", "circ_mktcap")


def _num_or_none(raw: object) -> Decimal | None:
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        raise AmountParseError(f"non-numeric: {text!r}")


class EastMoneyEtfSource:
    """东财 ETF 适配（ObservationSource）。无五档，价/量 + circ_mktcap KV。"""

    source_id = "eastmoney:push2delay"
    caliber = Caliber.EASTMONEY
    adapter_version = "push2delay-etf-v1"

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
        self.last_total = 0

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        return SubjectCapability(
            asset_class=AssetClass.FUND_ETF, subject_kind=SubjectKind.ETF,
            has_five_tier=False,                       # ETF 无五档资金流（诚实自描述）
            supported_metrics=_ETF_METRICS, available_sort_fields=_ETF_SORT,
            series_granularity="5min",
            notes="ETF 无资金流五档；circ_mktcap 为流通市值 proxy，非基金 aum",
        )

    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]:
        rows, total = self._client.fetch_with_total(
            fs=spec.fs, fields=",".join(_ETF_FIELDS), fid="f3")
        self.last_total = total
        refs: list[SubjectRef] = []
        for row in rows:
            code, name = row.get("f12"), row.get("f14")
            if not code or not name:
                continue
            refs.append(SubjectRef(
                source_symbol=str(code).strip(), display_name=str(name).strip(),
                asset_class=AssetClass.FUND_ETF, level=SubjectLevel.INSTRUMENT,
                subject_kind=SubjectKind.ETF))
        return refs

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        rows, total = self._client.fetch_with_total(
            fs=spec.fs, fields=",".join(_ETF_FIELDS), fid="f3")
        self.last_total = total
        out: list[RawObservation] = []
        for row in rows:
            obs = self._row_to_observation(row)
            if obs is not None:
                out.append(obs)
        return out

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        raise DataSourceError("ETF 无成员下钻", error_type="unsupported")

    def fetch_daily_final(self, spec: FetchSpec, trade_date: date) -> Sequence[RawObservation]:
        return self.fetch_observations(spec)

    def _row_to_observation(self, row: dict) -> RawObservation | None:
        code, name = row.get("f12"), row.get("f14")
        if not code or not name:
            return None
        try:
            price = _num_or_none(row.get("f2"))
            change_pct = _num_or_none(row.get("f3"))
            volume = _num_or_none(row.get("f5"))
            turnover = _num_or_none(row.get("f6"))
            circ = _num_or_none(row.get("f21"))
        except AmountParseError as exc:
            _log.info("ETF 行跳过", extra={"context": {"code": code, "reason": str(exc)}})
            return None
        if price is None and volume is None:
            return None  # 空壳行跳过
        # circ_mktcap 进稀疏附表（诚实命名：流通市值 proxy，非 aum）
        metrics = {"circ_mktcap": circ} if circ is not None else {}
        return RawObservation(
            source_symbol=str(code).strip(), display_name=str(name).strip(),
            asset_class=AssetClass.FUND_ETF, level=SubjectLevel.INSTRUMENT,
            subject_kind=SubjectKind.ETF, caliber=Caliber.EASTMONEY,
            # 五档全 None（ETF 无资金流）
            price=price, change_pct=change_pct, volume=volume, turnover=turnover,
            metrics=metrics, source_unit=AmountUnit.YUAN,
        )
