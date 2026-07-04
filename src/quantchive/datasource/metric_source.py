"""按指标解析权威源（spec004 · 多源查询解析）。

多源共存后，查某指标的历史时序需选「权威源」。series API 单指标，故无需跨源列合并——
每指标解析到有序源优先级，逐日取首个非空的权威源行。

用**存储层实际 observation.source_code 值**（eastmoney/baostock/sina_flow/ths_flow），
非 routing 键（三套命名不同）。优先级依据实测能力：
- 资金流五档：sina_flow（8年深、五档齐、不限流）> eastmoney（限流）> ths_flow（当日快照）
- 价/涨跌：baostock（复权、多年）> sina_flow（有价）> eastmoney
- 量：baostock > eastmoney
排行/大盘是实时，仅东财写 → REALTIME_SOURCE。
"""

from __future__ import annotations

from quantchive.models.enums import SortField

REALTIME_SOURCE = "eastmoney"   # 排行/盘中/大盘求和的实时源（现仅东财写实时）

_FLOW_SOURCES = ["sina_flow", "eastmoney", "ths_flow"]
_PRICE_SOURCES = ["baostock", "sina_flow", "eastmoney"]
_VOLUME_SOURCES = ["baostock", "eastmoney"]

# SortField → 历史日线的源优先级（前者优先）
METRIC_SOURCE_PRIORITY: dict[SortField, list[str]] = {
    SortField.MAIN_NET: _FLOW_SOURCES,
    SortField.NET_AMOUNT: _FLOW_SOURCES,
    SortField.SUPER_LARGE_NET: _FLOW_SOURCES,
    SortField.LARGE_NET: _FLOW_SOURCES,
    SortField.MEDIUM_NET: _FLOW_SOURCES,
    SortField.SMALL_NET: _FLOW_SOURCES,
    SortField.PRICE: _PRICE_SOURCES,
    SortField.CHANGE_PCT: _PRICE_SOURCES,
    SortField.VOLUME: _VOLUME_SOURCES,
}


def resolve_metric_sources(metric: SortField) -> list[str]:
    """返回该指标历史日线的有序源优先级（首个非空源为权威）。未知指标 → 全源兜底。"""
    return list(METRIC_SOURCE_PRIORITY.get(metric, _FLOW_SOURCES + _PRICE_SOURCES))


# SortField → 该指标在 observation 里的「非空判定列」（逐日选源时判哪列非空）
METRIC_NONNULL_COLUMN: dict[SortField, str] = {
    SortField.MAIN_NET: "main_net_cents",
    SortField.NET_AMOUNT: "net_amount_cents",   # 读取端优先 net_amount_cents，回落 main_net_cents
    SortField.SUPER_LARGE_NET: "super_large_net_cents",
    SortField.LARGE_NET: "large_net_cents",
    SortField.MEDIUM_NET: "medium_net_cents",
    SortField.SMALL_NET: "small_net_cents",
    SortField.PRICE: "price_micro",
    SortField.CHANGE_PCT: "change_pct_bp",
    SortField.VOLUME: "volume",
}


def metric_nonnull_column(metric: SortField) -> str:
    """该指标判非空的列（逐日选源用）。未知 → main_net_cents。"""
    return METRIC_NONNULL_COLUMN.get(metric, "main_net_cents")
