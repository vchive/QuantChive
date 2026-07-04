"""源路由 + 主备故障切换（spec003 D7 · FR-013/SC-004）。

每类指标绑定「优先源 + 备用源」；主源限流/失败自动切备源，返回实际用源（审计）。
仿 adata 分源。路由表可由 settings 覆盖，改路由不迁移 schema。
"""

from __future__ import annotations

from typing import Callable

from quantchive.core.logging import get_logger
from quantchive.datasource.base import DataSourceError

_log = get_logger(__name__)

# 指标类别 → [优先源, 备用源...]（metric_kind 权威取值见 data-model §4）
METRIC_SOURCE_ROUTES: dict[str, list[str]] = {
    "money_flow": ["eastmoney", "ths_flow"],   # 东财主 / 同花顺备（独立后端分流）
    "price_hist": ["baostock"],                 # 历史行情
    "realtime": ["eastmoney"],                  # 实时快照
}

# 可切换的失败类型（限流/超时 → 试下一个源）；语义类错误（unsupported）不切、直接抛
_FAILOVER_TYPES = frozenset({"rate_limited", "timeout"})


def resolve_sources(metric_kind: str, *, routes: dict[str, list[str]] | None = None) -> list[str]:
    """返回该指标类别的 [主, 备...] 源列表；未配置 → 空。"""
    return list((routes or METRIC_SOURCE_ROUTES).get(metric_kind, []))


def fetch_with_failover(
    metric_kind: str,
    fetch_fn: Callable[[str], object],
    *,
    routes: dict[str, list[str]] | None = None,
    is_enabled: Callable[[str], bool] | None = None,
) -> tuple[object, str]:
    """依次试主→备源调 fetch_fn(source_id)，返回 (结果, 实际用源)。

    主源抛 DataSourceError(rate_limited/timeout) → 切下一个；其它错误直接抛。
    is_enabled(source_id)=False 的源跳过（如 ths_flow 未验证 hexin-v 时停用）。
    全部源失败/无可用源 → 抛 DataSourceError（不静默返空，Edge Case）。
    """
    sources = resolve_sources(metric_kind, routes=routes)
    if not sources:
        raise DataSourceError(f"指标 {metric_kind} 无路由源", error_type="unsupported")
    last_exc: DataSourceError | None = None
    tried: list[str] = []
    for sid in sources:
        if is_enabled is not None and not is_enabled(sid):
            continue                              # 停用源跳过（优雅降级）
        tried.append(sid)
        try:
            return fetch_fn(sid), sid
        except DataSourceError as exc:
            if exc.error_type not in _FAILOVER_TYPES:
                raise                             # 语义错误不切源
            last_exc = exc
            _log.info("源故障切换", extra={"context": {
                "metric": metric_kind, "failed_source": sid, "err": exc.error_type}})
            continue
    if not tried:
        raise DataSourceError(
            f"指标 {metric_kind} 无可用源（全部停用）", error_type="unsupported")
    raise DataSourceError(
        f"指标 {metric_kind} 所有源失败: {last_exc}", error_type="rate_limited",
        detail={"tried": tried})


class FailoverObservationSource:
    """把「主源+备源」组合成一个 ObservationSource，fetch 时透明故障切换（SC-004/SC-008）。

    上层采集器无需改动即可获得失败切换能力——只是拿到一个「更聪明的源」。
    暴露 last_used_source（审计实际用源）与 last_total（转发自实际用源，供降级门禁）。
    """

    def __init__(self, metric_kind: str, sources: dict[str, object], *,
                 routes: dict[str, list[str]] | None = None,
                 is_enabled: Callable[[str], bool] | None = None) -> None:
        self.metric_kind = metric_kind
        self._sources = sources               # source_id → 已构造的源实例
        self._routes = routes
        self._is_enabled = is_enabled
        self.source_id = f"failover:{metric_kind}"
        self.adapter_version = "failover-v1"
        self.last_used_source: str | None = None

    @property
    def last_total(self) -> int:
        used = self._sources.get(self.last_used_source) if self.last_used_source else None
        return int(getattr(used, "last_total", 0) or 0)

    def fetch_observations(self, spec):
        def _fetch(sid):
            src = self._sources.get(sid)
            if src is None:
                raise DataSourceError(f"源 {sid} 未构造", error_type="rate_limited")
            return src.fetch_observations(spec)
        result, used = fetch_with_failover(
            self.metric_kind, _fetch, routes=self._routes, is_enabled=self._is_enabled)
        self.last_used_source = used
        return result

    # 其余 Protocol 方法转发主源（维表/成分/日终不做 failover）
    def _primary(self):
        ids = resolve_sources(self.metric_kind, routes=self._routes)
        return self._sources.get(ids[0]) if ids else None

    def capability(self, spec):
        p = self._primary()
        return p.capability(spec) if p else None

    def fetch_subjects(self, spec):
        p = self._primary()
        return p.fetch_subjects(spec) if p else []

    def fetch_members(self, parent):
        p = self._primary()
        return p.fetch_members(parent) if p else []

    def fetch_daily_final(self, spec, trade_date):
        p = self._primary()
        return p.fetch_daily_final(spec, trade_date) if p else []
