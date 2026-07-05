"""大盘查询路由（US1，contracts/service-api.md）。薄路由 → QueryService.get_market_overview。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantchive.api.deps import get_query_service
from quantchive.models.enums import AssetClass
from quantchive.service.dto import MarketOverviewResult
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
