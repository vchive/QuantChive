"""T024: 主备故障切换——主源限流→切备源、返实际用源、全失败抛错、停用源跳过（SC-004）。"""

from __future__ import annotations

import pytest

from quantchive.datasource.base import DataSourceError
from quantchive.datasource.routing import fetch_with_failover, resolve_sources

_ROUTES = {"money_flow": ["eastmoney", "ths_flow"], "price_hist": ["baostock"]}


def test_resolve_sources() -> None:
    assert resolve_sources("money_flow", routes=_ROUTES) == ["eastmoney", "ths_flow"]
    assert resolve_sources("unknown", routes=_ROUTES) == []


def test_failover_primary_ratelimited_switches_to_backup() -> None:
    """主源限流 → 切备源产出，返回实际用源=备源（SC-004/US3 AC1）。"""
    def fetch(sid):
        if sid == "eastmoney":
            raise DataSourceError("限流", error_type="rate_limited")
        return f"data_from_{sid}"

    result, used = fetch_with_failover("money_flow", fetch, routes=_ROUTES)
    assert result == "data_from_ths_flow"
    assert used == "ths_flow"


def test_failover_primary_ok_uses_primary() -> None:
    result, used = fetch_with_failover("money_flow", lambda sid: f"d_{sid}", routes=_ROUTES)
    assert used == "eastmoney"                    # 主源成功不切


def test_all_sources_fail_raises() -> None:
    """全部源失败 → 抛错（不静默返空当无数据，Edge Case）。"""
    def fetch(sid):
        raise DataSourceError("限流", error_type="rate_limited")

    with pytest.raises(DataSourceError):
        fetch_with_failover("money_flow", fetch, routes=_ROUTES)


def test_semantic_error_does_not_failover() -> None:
    """非限流/超时（如 unsupported）→ 直接抛，不切备源。"""
    calls = []

    def fetch(sid):
        calls.append(sid)
        raise DataSourceError("不支持", error_type="unsupported")

    with pytest.raises(DataSourceError):
        fetch_with_failover("money_flow", fetch, routes=_ROUTES)
    assert calls == ["eastmoney"]                 # 只试主源，未切备源


def test_disabled_source_skipped() -> None:
    """停用源（如 ths_flow 未验 hexin-v）跳过；仅主源可用且成功。"""
    result, used = fetch_with_failover(
        "money_flow", lambda sid: f"d_{sid}", routes=_ROUTES,
        is_enabled=lambda sid: sid != "ths_flow")
    assert used == "eastmoney"


def test_all_disabled_raises() -> None:
    with pytest.raises(DataSourceError):
        fetch_with_failover("money_flow", lambda sid: "x", routes=_ROUTES,
                            is_enabled=lambda sid: False)


def test_failover_observation_source_switches_and_reports_used() -> None:
    """FailoverObservationSource：主源限流→切备源、fetch 透明、last_used_source 记实际用源。"""
    from quantchive.datasource.routing import FailoverObservationSource

    class _Src:
        def __init__(self, sid, rows, total, fail=False):
            self.source_id = sid
            self.last_total = total
            self._rows = rows
            self._fail = fail

        def fetch_observations(self, spec):
            if self._fail:
                raise DataSourceError("限流", error_type="rate_limited")
            return self._rows

    primary = _Src("eastmoney", [], 0, fail=True)
    backup = _Src("ths_flow", ["a", "b"], 2)
    fo = FailoverObservationSource(
        "money_flow", {"eastmoney": primary, "ths_flow": backup}, routes=_ROUTES)
    rows = fo.fetch_observations(spec=None)
    assert rows == ["a", "b"]
    assert fo.last_used_source == "ths_flow"       # 审计实际用源
    assert fo.last_total == 2                       # 转发自实际用源

