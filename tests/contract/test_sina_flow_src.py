"""新浪个股历史资金流源契约（fake http_get，不联网）。"""

from __future__ import annotations

from decimal import Decimal

from quantchive.datasource.sina_flow_src import SinaFlowSource, secid_for_sina


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


# 实测字段结构（元级）
_ROWS = [
    {"opendate": "2026-07-03", "trade": "8.70", "changeratio": "0.00693",
     "netamount": "-108066997.65", "r0_net": "-41550594.21", "r1_net": "-7855611.30",
     "r2_net": "-49132462.61", "r3_net": "-9528329.53"},
    {"opendate": "2026-07-02", "trade": "8.71", "changeratio": "0.005",
     "netamount": "54326046", "r0_net": "37454839", "r1_net": "1000000",
     "r2_net": "0", "r3_net": "0"},
    {"opendate": "2026-06-01", "trade": "8.50", "changeratio": "0.01",   # 窗口外
     "netamount": "1", "r0_net": "1", "r1_net": "1", "r2_net": "0", "r3_net": "0"},
]


def test_secid_mapping() -> None:
    assert secid_for_sina("600000", "SSE") == "sh600000"
    assert secid_for_sina("000001", "SZSE") == "sz000001"
    assert secid_for_sina("600000", None) == "sh600000"       # 前缀兜底


def test_fetch_flow_history_five_tier() -> None:
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["daima"] = params["daima"]
        return _Resp(_ROWS)

    src = SinaFlowSource(http_get=fake_get)
    obs = src.fetch_flow_history(symbol="600000", exchange="SSE",
                                 start_date="2026-07-01", end_date="2026-07-03")
    assert len(obs) == 2                            # 06-01 在窗口外被过滤
    o = obs[0]                                      # 07-03
    assert o.trade_date == "2026-07-03"
    assert o.super_large_net == Decimal("-41550594.21")   # r0
    assert o.large_net == Decimal("-7855611.30")          # r1
    assert o.main_net == Decimal("-49406205.51")          # r0+r1 主力
    assert o.medium_net == Decimal("-49132462.61")        # r2
    assert o.small_net == Decimal("-9528329.53")          # r3
    assert o.change_pct == Decimal("0.69300")             # 比率×100
    assert captured["daima"] == "sh600000"


def test_http_error_is_ratelimited() -> None:
    from quantchive.datasource.base import DataSourceError

    class _Err:
        status_code = 429

        def json(self):
            return []

    src = SinaFlowSource(http_get=lambda *a, **k: _Err())
    try:
        src.fetch_flow_history(symbol="600000", exchange="SSE",
                               start_date="2026-07-01", end_date="2026-07-03")
        assert False
    except DataSourceError as e:
        assert e.error_type == "rate_limited"
