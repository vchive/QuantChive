"""东财板块观测源（ObservationSource）。

`fetch_observations(spec)` → RawObservation（参数化 fs）；`fetch_members(parent)` 板块→成分个股下钻。
HTTP 翻页/重试/header 下沉到 _em_client.EmClient。board 单位=元(×100)、五档齐全。
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
from quantchive.datasource.em_stock_src import _exchange_of
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SortField,
    SubjectKind,
    SubjectLevel,
)

_log = get_logger(__name__)

# 板块采集所需 f-code 字段（名 + 五档；价/量板块层本期不取）
_BOARD_FIELDS = ("f12", "f14", "f62", "f66", "f72", "f78", "f84")
# 板块成员（个股）下钻字段：身份 + 五档 + 价/涨跌/量/额
_MEMBER_FIELDS = ("f12", "f14", "f2", "f3", "f5", "f6",
                  "f62", "f66", "f72", "f78", "f84")
# f-code → RawObservation 语义
_FCODE = {
    "name": "f14",
    "main": "f62", "super_large": "f66", "large": "f72",
    "medium": "f78", "small": "f84",
}
_FIVE_TIER_SORT = (
    SortField.MAIN_NET, SortField.NET_AMOUNT, SortField.SUPER_LARGE_NET,
    SortField.LARGE_NET, SortField.MEDIUM_NET, SortField.SMALL_NET,
)
_FIVE_TIER_METRICS = ("main_net", "super_large_net", "large_net", "medium_net", "small_net")


def _to_yuan(raw: object) -> Decimal:
    """东财金额单位=元 → Decimal(元)。禁 Decimal(float)。"""
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        raise AmountParseError(f"non-numeric: {text!r}")
    return Decimal(text)


def _num_or_none(raw: object) -> Decimal | None:
    """东财数值 → Decimal；空/'-'/NaN → None（指标缺失不造假）。禁 Decimal(float)。"""
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        raise AmountParseError(f"non-numeric: {text!r}")




class EastMoneySource:
    """东方财富板块资金流适配（source_id 用于审计与可复现）。"""

    source_id = "eastmoney:push2delay"
    caliber = Caliber.EASTMONEY
    adapter_version = "push2delay-v2"

    def __init__(
        self,
        *,
        http_get: Callable[..., object] | None = None,
        max_retries: int = 3,
        backoff_base: float = 3.0,
        backoff_cap: float = 20.0,
        timeout: float = 15.0,
        page_size: int = 100,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        import time as _time
        self._client = EmClient(
            http_get=http_get, max_retries=max_retries, backoff_base=backoff_base,
            backoff_cap=backoff_cap, timeout=timeout, page_size=page_size,
            sleep=sleep or _time.sleep,
        )

    # ---- 能力自描述 ----

    def capability(self, spec: FetchSpec) -> SubjectCapability:
        """spec002 主体能力自描述。板块只支持五档资金流、拒价格排序。"""
        return SubjectCapability(
            asset_class=AssetClass.A_SHARE,
            subject_kind=spec.subject_kind,
            has_five_tier=True,
            supported_metrics=_FIVE_TIER_METRICS,
            available_sort_fields=_FIVE_TIER_SORT,
            series_granularity="1min",
        )

    # ---- 新接口（ObservationSource）----

    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]:
        rows = self._client.fetch_diff(fs=spec.fs, fields=",".join(_BOARD_FIELDS))
        refs: list[SubjectRef] = []
        for row in rows:
            name = row.get(_FCODE["name"])
            if not name:
                continue
            refs.append(SubjectRef(
                source_symbol=str(name).strip(), display_name=str(name).strip(),
                asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
                subject_kind=spec.subject_kind, em_board_code=row.get("f12"),
            ))
        return refs

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        rows = self._client.fetch_diff(fs=spec.fs, fields=",".join(_BOARD_FIELDS))
        out: list[RawObservation] = []
        for row in rows:
            obs = self._row_to_observation(row, spec.subject_kind)
            if obs is not None:
                out.append(obs)
        return out

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        """板块→成分个股下钻（fs=b:{em_board_code}）。成员为个股(level=instrument)。"""
        if not parent.em_board_code:
            raise DataSourceError(
                f"板块 {parent.source_symbol} 无 em_board_code，无法下钻成员",
                error_type="unsupported")
        rows = self._client.fetch_diff(
            fs=f"b:{parent.em_board_code}", fields=",".join(_MEMBER_FIELDS))
        out: list[RawObservation] = []
        for row in rows:
            obs = self._member_row_to_observation(row)
            if obs is not None:
                out.append(obs)
        return out

    def _member_row_to_observation(self, row: dict) -> RawObservation | None:
        code = row.get("f12")
        name = row.get("f14")
        if not code or not name:
            return None
        try:
            main = _num_or_none(row.get("f62"))
            xl = _num_or_none(row.get("f66"))
            lg = _num_or_none(row.get("f72"))
            md = _num_or_none(row.get("f78"))
            sm = _num_or_none(row.get("f84"))
            price = _num_or_none(row.get("f2"))
            change_pct = _num_or_none(row.get("f3"))
            volume = _num_or_none(row.get("f5"))
            turnover = _num_or_none(row.get("f6"))
        except AmountParseError as exc:
            _log.info("板块成员行跳过", extra={"context": {"code": code, "reason": str(exc)}})
            return None
        # 五档 all-or-nothing（任一缺失整体判无，不写半档破 CHECK）
        if any(t is None for t in (main, xl, lg, md, sm)):
            main = xl = lg = md = sm = None
        if main is None and price is None and volume is None:
            return None  # 空壳行跳过
        return RawObservation(
            source_symbol=str(code).strip(), display_name=str(name).strip(),
            asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
            subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
            main_net=main, super_large_net=xl, large_net=lg, medium_net=md, small_net=sm,
            price=price, change_pct=change_pct, volume=volume, turnover=turnover,
            exchange=_exchange_of(str(code)), source_unit=AmountUnit.YUAN,
        )

    def fetch_daily_final(
        self, spec: FetchSpec, trade_date: date
    ) -> Sequence[RawObservation]:
        return self.fetch_observations(spec)

    def _row_to_observation(
        self, row: dict, subject_kind: SubjectKind
    ) -> RawObservation | None:
        try:
            name = str(row[_FCODE["name"]]).strip()
            main = _to_yuan(row[_FCODE["main"]])
            xl = _to_yuan(row[_FCODE["super_large"]])
            lg = _to_yuan(row[_FCODE["large"]])
            md = _to_yuan(row[_FCODE["medium"]])
            sm = _to_yuan(row[_FCODE["small"]])
        except (AmountParseError, KeyError, TypeError) as exc:
            _log.info("东财板块行跳过", extra={"context": {"reason": str(exc)}})
            return None
        return RawObservation(
            source_symbol=name, display_name=name, asset_class=AssetClass.A_SHARE,
            level=SubjectLevel.SECTOR, subject_kind=subject_kind, caliber=Caliber.EASTMONEY,
            main_net=main, super_large_net=xl, large_net=lg, medium_net=md, small_net=sm,
            em_board_code=row.get("f12"), source_unit=AmountUnit.YUAN,
        )
