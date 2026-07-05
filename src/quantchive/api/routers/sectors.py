"""板块查询路由（contracts/service-api.md）。薄路由 → get_sector_ranking（通用模型）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_query_service
from quantchive.models.enums import Caliber, SectorType, SortField
from quantchive.service.dto import RankingResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/sectors", tags=["sectors"])


@router.get("/ranking", response_model=RankingResult)
def get_sector_ranking(
    caliber: Caliber,
    sector_type: SectorType,
    sort_by: SortField = SortField.MAIN_NET,
    top_n: Annotated[int, Query(ge=1, le=100)] = 10,
    full: bool = False,
    trade_date: str | None = None,
    svc: QueryService = Depends(get_query_service),
) -> RankingResult:
    """板块资金流排行（bipolar 双榜）。能力隔离：板块拒价格排序→422 UNSUPPORTED_METRIC。"""
    return svc.get_sector_ranking(
        caliber=caliber, sector_type=sector_type, sort_by=sort_by,
        top_n=top_n, full=full, trade_date=trade_date,
    )
