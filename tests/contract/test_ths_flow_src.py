"""T030: 同花顺资金流源契约（fake records，不联网）。"""

from __future__ import annotations

from decimal import Decimal

from quantchive.datasource.base import DataSourceError, FetchSpec
from quantchive.datasource.ths_flow_src import ThsFlowSource
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel


def _spec():
    return FetchSpec(fs="x", level=SubjectLevel.INSTRUMENT, asset_class=AssetClass.A_SHARE,
                     subject_kind=SubjectKind.STOCK)


_FAKE = [
    {"股票代码": "301199", "股票简称": "迈赫股份", "最新价": "21.28", "涨跌幅": "20.02%",
     "换手率": "5.75%", "流入资金": "1.08亿", "流出资金": "9896.30万", "净额": "941.88万", "成交额": "2.07亿"},
    {"股票代码": "600519", "股票简称": "茅台", "最新价": "1680.0", "涨跌幅": "-1.2%",
     "换手率": "0.5%", "流入资金": "5.62亿", "流出资金": "5.17亿", "净额": "4465.79万", "成交额": "10.79亿"},
]


def test_ths_flow_parses_and_normalizes() -> None:
    src = ThsFlowSource(records_fn=lambda symbol: _FAKE)
    obs = src.fetch_observations(_spec())
    assert len(obs) == 2
    o = obs[0]
    assert o.source_symbol == "301199" and o.display_name == "迈赫股份"
    assert o.main_net == Decimal("9418800.00")     # 941.88万 → 元
    assert o.change_pct == Decimal("20.02")
    assert o.turnover == Decimal("207000000")      # 2.07亿 → 元
    assert o.super_large_net is None                # 无五档（诚实，仅主力净额）
    assert o.exchange == "SZSE"                     # 301→深
    assert obs[1].exchange == "SSE"                 # 600→沪


def test_ths_flow_failure_is_ratelimited() -> None:
    """底层失败 → DataSourceError(rate_limited)，供路由 failover 回主源。"""
    def boom(symbol):
        raise RuntimeError("hexin-v expired")

    src = ThsFlowSource(records_fn=boom)
    try:
        src.fetch_observations(_spec())
        assert False, "应抛错"
    except DataSourceError as e:
        assert e.error_type == "rate_limited"
