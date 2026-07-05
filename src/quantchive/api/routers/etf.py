"""ETF 排行路由（US4）。薄路由 → get_etf_ranking（单榜 unipolar）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_query_service
from quantchive.models.enums import SortField
from quantchive.service.dto import RankingResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/etf", tags=["etf"])


@router.get("/ranking", response_model=RankingResult)
def get_etf_ranking(
    sort_by: SortField = SortField.CHANGE_PCT,
    top_n: Annotated[int, Query(ge=1, le=100)] = 20,
    full: bool = False,
    trade_date: str | None = None,
    svc: QueryService = Depends(get_query_service),
) -> RankingResult:
    """ETF 榜（有价/涨跌/量，has_five_tier=false）。五档排序→422 UNSUPPORTED_METRIC。"""
    return svc.get_etf_ranking(
        sort_by=sort_by, top_n=top_n, full=full, trade_date=trade_date)
