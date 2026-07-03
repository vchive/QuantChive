"""DataSource 抽象（contracts/datasource.md, research.md D5）。

- ObservationSource Protocol：通用多品种多主体，参数化 fs。
- 换源三保证：依赖倒置 + 形状收口(RawObservation) + 能力自描述(capability)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AssetClass,
    SectorType,
    SortField,
    SubjectKind,
    SubjectLevel,
)


class DataSourceError(Exception):
    """数据源层结构化异常（限流/超时/schema 漂移/空）。"""

    def __init__(self, message: str, *, error_type: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type  # 'rate_limited'|'empty_or_dash'|'schema_drift'|'timeout'
        self.detail = detail or {}


# ---- 通用观测源抽象（contracts/datasource.md）----

# 东财 clist 默认字段：f14名/f62主力/f66超大/f72大/f78中/f84小/f2价/f3涨跌幅/f5量/f6额/f21流通市值
_EM_FIELDS_DEFAULT: tuple[str, ...] = (
    "f12", "f14", "f2", "f3", "f5", "f6", "f21",
    "f62", "f66", "f69", "f72", "f75", "f78", "f81", "f84", "f87",
)


@dataclass(frozen=True)
class FetchSpec:
    """一次采集的参数（东财 clist fs + 主体分类）。page_size 默认 100（东财单页硬顶）。"""

    fs: str
    level: SubjectLevel
    asset_class: AssetClass
    subject_kind: SubjectKind
    sector_type: SectorType | None = None
    fields: tuple[str, ...] = _EM_FIELDS_DEFAULT
    page_size: int = 100


@dataclass(frozen=True)
class SubjectRef:
    """维表刷新用的主体标识（fetch_subjects 返回）。"""

    source_symbol: str
    display_name: str
    asset_class: AssetClass
    level: SubjectLevel
    subject_kind: SubjectKind
    exchange: str | None = None
    em_board_code: str | None = None


@dataclass(frozen=True)
class SubjectCapability:
    """主体×指标能力自描述（按主体隔离可排序字段，板块拒价格排序）。"""

    asset_class: AssetClass
    subject_kind: SubjectKind
    has_five_tier: bool
    supported_metrics: tuple[str, ...]
    available_sort_fields: tuple[SortField, ...]
    series_granularity: str                       # '1min'|'5min'|'daily'
    notes: str | None = None


@runtime_checkable
class ObservationSource(Protocol):
    """通用观测源。上层依赖此接口，不感知东财/EFinance/同花顺身份（SC-008）。"""

    source_id: str

    def capability(self, spec: FetchSpec) -> SubjectCapability: ...

    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]:
        """维表刷新：拉取该 spec 下的主体清单。"""
        ...

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        """指标快照（盘中可轮询）。失败经重试后抛 DataSourceError。"""
        ...

    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]:
        """板块→个股下钻（fs=b:BK{code}）。本期 US2 预留。"""
        ...

    def fetch_daily_final(
        self, spec: FetchSpec, trade_date: date
    ) -> Sequence[RawObservation]:
        """日终确定值。"""
        ...
