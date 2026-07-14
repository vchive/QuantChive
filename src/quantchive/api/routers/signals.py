"""信号回测路由（阶段B）。薄路由 → BacktestService.backtest_stock。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_backtest_service, get_conn
from quantchive.service.backtest_service import BacktestService
from quantchive.service.dto import SignalBacktestResult
from quantchive.service.signal_lib import SIGNAL_KINDS

router = APIRouter(prefix="/api/subjects", tags=["signals"])


@router.get("/{subject_id}/signal_backtest", response_model=SignalBacktestResult)
def signal_backtest(
    subject_id: int,
    kinds: str | None = Query(None, description="逗号分隔信号类型;缺省=全部4种"),
    horizons: str = Query("1,3,5", description="逗号分隔前向天数"),
    lookback_years: int = Query(5, ge=1, le=16),
    as_of: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    price_source: str = Query("sina", description="标签价源:sina(默认,即时全覆盖)/hfq(严谨,需回填)"),
    svc: BacktestService = Depends(get_backtest_service),
) -> SignalBacktestResult:
    """某股资金流信号历史回测:胜率+Wilson CI+样本数+基准对照(历史统计,非预测)。

    信号只用 as-of 及之前(防前视);标签默认 sina 日涨跌幅链式(复权无关、即时全覆盖),
    price_source=hfq 用后复权总回报(需先回填 baostock_hfq)。样本<30 标注不可靠。
    """
    kind_list = (
        [k.strip() for k in kinds.split(",") if k.strip() in SIGNAL_KINDS]
        if kinds else list(SIGNAL_KINDS))
    hz = tuple(int(h) for h in horizons.split(",") if h.strip().isdigit())
    return svc.backtest_stock(
        subject_id=subject_id, kinds=kind_list, horizons=hz or (1, 3, 5),
        lookback_years=lookback_years, as_of=as_of, price_source=price_source)


scan_router = APIRouter(prefix="/api/signals", tags=["signals"])


@scan_router.get("/scan")
def signal_scan(
    kind: str = Query("accumulation", description="信号类型"),
    trade_date: str | None = Query(None, description="YYYY-MM-DD;空=最新"),
    top_n: int = Query(50, ge=1, le=200),
    conn=Depends(get_conn),
) -> dict:
    """某信号某日全市场命中股(盘后预计算,按强度降序)。历史统计,非预测。

    kind: accumulation|distribution|net_inflow_streak|super_large_spike。
    trade_date 空/缺省=该信号最新扫描日。返回命中股+available_dates。
    """
    import re
    from quantchive.service.scan_service import list_hits
    k = kind if kind in SIGNAL_KINDS else "accumulation"
    td = trade_date if (trade_date and re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date)) else None
    return list_hits(conn, kind=k, trade_date=td, top_n=top_n)


@scan_router.get("/market_stats")
def market_stats(
    family: str | None = Query(None, description="flow|fundamental|缺省全部"),
    conn=Depends(get_conn),
) -> dict:
    """全市场信号历史统计(预计算秒读)。历史条件统计非预测,披露按信号族。"""
    from quantchive.service.market_signal_service import read_market_stats
    f = family if family in ("flow", "fundamental", "combo") else None
    return read_market_stats(conn, family=f)
