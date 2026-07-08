"""信号回测路由（阶段B）。薄路由 → BacktestService.backtest_stock。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_backtest_service
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
    svc: BacktestService = Depends(get_backtest_service),
) -> SignalBacktestResult:
    """某股资金流信号历史回测:胜率+Wilson CI+样本数+基准对照(历史统计,非预测)。

    信号只用 as-of 及之前(防前视);标签用 baostock_hfq 前向收益链式乘(防漂移)。
    样本<30 标注不可靠。end_date/as_of 为 as-of 上界。
    """
    kind_list = (
        [k.strip() for k in kinds.split(",") if k.strip() in SIGNAL_KINDS]
        if kinds else list(SIGNAL_KINDS))
    hz = tuple(int(h) for h in horizons.split(",") if h.strip().isdigit())
    return svc.backtest_stock(
        subject_id=subject_id, kinds=kind_list, horizons=hz or (1, 3, 5),
        lookback_years=lookback_years, as_of=as_of)
