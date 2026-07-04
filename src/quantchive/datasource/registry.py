"""按 source_id 选源工厂 + 缺失优雅降级（spec003 D7 · FR-012/FR-014/SC-005）。

仿 vnpy get_datafeed：**基类 no-op 兜底**而非强制 ABC——未注册/依赖缺失时返回空数据源
（不崩、记 warning），其余源不受影响。新增源只需在此登记构造器，上层零改动消费（SC-008）。
"""

from __future__ import annotations

from typing import Callable, Sequence

from quantchive.core.logging import get_logger
from quantchive.datasource.base import FetchSpec, SubjectRef
from quantchive.datasource.dto import RawObservation

_log = get_logger(__name__)


class NoOpSource:
    """缺失源的兜底：不崩、返回空/None，供 misconfig 优雅降级。"""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.adapter_version = "noop"

    def capability(self, spec: FetchSpec):
        return None

    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]:
        return []

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        _log.warning("NoOpSource.fetch_observations", extra={"context": {"source_id": self.source_id}})
        return []

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        return []

    def fetch_daily_final(self, spec: FetchSpec, trade_date) -> Sequence[RawObservation]:
        return []

    def fetch_price_history(self, **kw) -> Sequence[RawObservation]:
        return []


def _em_sector():
    from quantchive.datasource._http_client import default_http_get
    from quantchive.datasource.em_sector_src import EastMoneySource
    return EastMoneySource(http_get=default_http_get())


def _em_stock():
    from quantchive.datasource._http_client import default_http_get
    from quantchive.datasource.em_stock_src import EastMoneyStockSource
    return EastMoneyStockSource(http_get=default_http_get())


def _baostock():
    from quantchive.datasource.baostock_src import BaostockSource
    return BaostockSource()


def _ths_flow():
    from quantchive.datasource.ths_flow_src import ThsFlowSource
    return ThsFlowSource()


# source_id → 构造器（惰性，避免导入期副作用）。缺失 → NoOpSource。
_OBSERVATION_SOURCES: dict[str, Callable[[], object]] = {
    "eastmoney": _em_sector,
    "eastmoney_stock": _em_stock,
    "ths_flow": _ths_flow,
}
_HISTORY_SOURCES: dict[str, Callable[[], object]] = {
    "baostock": _baostock,
}


def get_source(source_id: str) -> object:
    """按 source_id 取观测源实例；未注册/构造失败 → NoOpSource（不崩，记 warning）。"""
    ctor = _OBSERVATION_SOURCES.get(source_id)
    if ctor is None:
        _log.warning("未注册的 source_id", extra={"context": {"source_id": source_id}})
        return NoOpSource(source_id)
    try:
        return ctor()
    except Exception as exc:  # noqa: BLE001 依赖缺失/构造失败 → 降级不崩
        _log.warning("源构造失败，降级 NoOp", extra={"context": {
            "source_id": source_id, "err": type(exc).__name__}})
        return NoOpSource(source_id)


def get_history_source(source_id: str) -> object:
    """按 source_id 取历史源实例；未注册/构造失败 → NoOpSource。"""
    ctor = _HISTORY_SOURCES.get(source_id)
    if ctor is None:
        _log.warning("未注册的历史 source_id", extra={"context": {"source_id": source_id}})
        return NoOpSource(source_id)
    try:
        return ctor()
    except Exception as exc:  # noqa: BLE001
        _log.warning("历史源构造失败，降级 NoOp", extra={"context": {
            "source_id": source_id, "err": type(exc).__name__}})
        return NoOpSource(source_id)


def register_source(source_id: str, ctor: Callable[[], object], *, history: bool = False) -> None:
    """登记新源构造器（测试/扩展用）。上层零改动即可消费（SC-005/SC-008）。"""
    (_HISTORY_SOURCES if history else _OBSERVATION_SOURCES)[source_id] = ctor
