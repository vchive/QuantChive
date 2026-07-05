"""板块内个股下钻路由（US3，contracts/service-api.md）。薄路由 → get_stocks_in_sector。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_query_service
from quantchive.models.enums import SortField
from quantchive.service.dto import RankingResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/sectors", tags=["stocks"])


@router.get("/{sector_subject_id}/stocks", response_model=RankingResult)
def get_stocks_in_sector(
    sector_subject_id: int,
    sort_by: SortField = SortField.MAIN_NET,
    top_n: Annotated[int, Query(ge=1, le=100)] = 20,
    full: bool = False,
    trade_date: str | None = None,
    as_of: str | None = None,
    svc: QueryService = Depends(get_query_service),
) -> RankingResult:
    """板块成分个股排行（下钻）。as_of<today 且无成分→422 OUT_OF_WINDOW（无前视）。"""
    return svc.get_stocks_in_sector(
        sector_subject_id=sector_subject_id, sort_by=sort_by, top_n=top_n,
        full=full, trade_date=trade_date, as_of=as_of,
    )
