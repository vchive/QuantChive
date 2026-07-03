"""Service 层对外 Pydantic 模型（contracts/service-api.md）。

金额 Decimal → Pydantic v2 JSON 序列化为字符串（前端不进 float, SC-006）。
通用 subject×observation 模型：SubjectRef/ObservationItem/RankingResult/能力/时序/大盘/基金。
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from quantchive.models.enums import (
    AssetClass,
    SortField,
    SourceType,
    SubjectKind,
    SubjectLevel,
)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True)


class DataProvenance(_Base):
    trade_date: str
    source_type: SourceType
    source_id: str
    captured_at: str  # minute_slot 或 'EOD'
    is_stale: bool
    is_approximate_final: bool = False


class HealthResult(_Base):
    status: str
    latest_trade_date: str | None
    last_run: dict | None = None


# ---- spec002 通用观测 DTO（service-api.md，与旧模型并存）----


class SubjectRef(_Base):
    """主体标识（下钻寻址、维表暴露）。"""

    subject_id: int
    source_symbol: str
    display_name: str
    asset_class: AssetClass
    level: SubjectLevel
    subject_kind: SubjectKind


class ObservationItem(_Base):
    """一个主体在某时点的观测（按能力填指标，缺失为 None）。金额 Decimal→字符串。"""

    subject_id: int
    source_symbol: str
    display_name: str
    # 资金流五档（无此指标的主体全 None）
    net_amount: Decimal | None = None
    main_net: Decimal | None = None
    super_large_net: Decimal | None = None
    large_net: Decimal | None = None
    medium_net: Decimal | None = None
    small_net: Decimal | None = None
    # 价/量
    price: Decimal | None = None
    change_pct: Decimal | None = None
    volume: Decimal | None = None
    turnover: Decimal | None = None


class SubjectCapabilityView(_Base):
    """主体×指标能力自描述（对外；按 asset_class,subject_kind 隔离可排序字段）。"""

    asset_class: AssetClass
    subject_kind: SubjectKind
    has_five_tier: bool
    supported_metrics: list[str]
    available_sort_fields: list[SortField]
    series_granularity: str
    notes: str | None = None


class RankingResult(_Base):
    """通用排行结果。mode 决定榜型：

    - bipolar（main_net/change_pct 有正负）→ top_inflow/top_outflow 双榜。
    - unipolar（price/volume 恒正）→ ranked_items 单榜。
    """

    asset_class: AssetClass
    level: SubjectLevel
    subject_kind: SubjectKind
    sort_by: SortField
    mode: str                              # 'bipolar' | 'unipolar'
    provenance: DataProvenance
    top_inflow: list[ObservationItem] = []
    top_outflow: list[ObservationItem] = []
    ranked_items: list[ObservationItem] = []
    all_items: list[ObservationItem] | None = None
    total_subjects: int = 0
    has_five_tier: bool = False


class MarketOverviewResult(_Base):
    """大盘资金全景（US1）。derived 求和 + 覆盖率审计透明（provenance 层）。"""

    asset_class: AssetClass
    trade_date: str
    provenance: DataProvenance
    main_net: Decimal | None = None
    super_large_net: Decimal | None = None
    large_net: Decimal | None = None
    medium_net: Decimal | None = None
    small_net: Decimal | None = None
    constituent_count: int | None = None
    expected_count: int | None = None
    coverage_pct: Decimal | None = None      # constituent/expected ×100
    is_derived: bool = True


class FundNavPointView(_Base):
    nav_date: str
    unit_nav: Decimal
    growth_pct: Decimal | None = None


class FundNavResult(_Base):
    """开放式基金净值（US4，旁路实时查，不入库）。note 明确未持久化。"""

    fund_code: str
    points: list[FundNavPointView]
    note: str = "not persisted"     # 明示未落库（旁路，D4）
    source_id: str = "eastmoney:fund-lsjz"


class SeriesPoint(_Base):
    """单指标时序点。value=None 表示断点（该时点该指标缺失，前端不连线）。"""

    ts: str                        # minute_slot 'HH:MM' | 'HH:00'(降采后)
    value: Decimal | None
    granularity: str               # '1min'|'5min'|'hourly'
    source_type: SourceType


class SubjectSeriesResult(_Base):
    """单主体单指标时序回看（US5）。近 7 天分钟级；8-30 天小时级；超 30 天 OutOfWindow。"""

    subject_id: int
    source_symbol: str
    display_name: str
    metric: str
    trade_date: str
    granularity: str               # 该序列主粒度
    provenance: DataProvenance
    points: list[SeriesPoint]
    gap_count: int                 # 断点数（value=None 的点）
