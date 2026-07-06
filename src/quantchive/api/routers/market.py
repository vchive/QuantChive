"""大盘查询路由（US1，contracts/service-api.md）。薄路由 → QueryService.get_market_overview。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_query_service
from quantchive.models.enums import AssetClass, SortField
from quantchive.service.dto import MarketOverviewResult, RankingResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/overview", response_model=MarketOverviewResult)
def get_market_overview(
    asset_class: AssetClass = AssetClass.A_SHARE,
    trade_date: str | None = None,
    svc: QueryService = Depends(get_query_service),
) -> MarketOverviewResult:
    """大盘主力净额（个股求和，覆盖率≥95% 才有值）。无数据→404。"""
    return svc.get_market_overview(asset_class=asset_class, trade_date=trade_date)


@router.get("/scan", response_model=RankingResult)
def scan_market_stocks(
    sort_by: SortField = SortField.MAIN_NET,
    top_n: Annotated[int, Query(ge=1, le=200)] = 50,
    trade_date: str | None = None,
    as_of: str | None = None,
    svc: QueryService = Depends(get_query_service),
) -> RankingResult:
    """全市场个股平铺横截面排行（跨行业统一榜）。bipolar 返双榜、unipolar 单榜。"""
    return svc.scan_market_stocks(
        sort_by=sort_by, top_n=top_n, trade_date=trade_date, as_of=as_of)
