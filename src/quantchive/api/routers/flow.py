"""资金档位博弈多档时序路由（spec005 US1）。薄路由 → FlowQueryService。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_conn
from quantchive.core.settings import get_settings
from quantchive.service.dto import TiersSeriesResult
from quantchive.service.flow_query_service import FlowQueryService

router = APIRouter(prefix="/api/subjects", tags=["flow"])


@router.get("/{subject_id}/tiers_series", response_model=TiersSeriesResult)
def get_tiers_series(
    subject_id: int,
    granularity: str = Query("daily", pattern="^(daily|intraday)$"),
    conn=Depends(get_conn),
) -> TiersSeriesResult:
    """四档(净额+流入+流出) + 主力/散户 + 价 + 全程累计 + 背离段。"""
    svc = FlowQueryService(
        conn, retention_trade_days=get_settings().retention_trade_days)
    return svc.get_tiers_series(subject_id=subject_id, granularity=granularity)
