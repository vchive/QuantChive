"""开放式基金净值路由（US4，旁路不入库）。薄路由 → FundLookupService。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_fund_lookup_service
from quantchive.service.dto import FundNavResult
from quantchive.service.fund_lookup_service import FundLookupService

router = APIRouter(prefix="/api/funds", tags=["funds"])


@router.get("/{fund_code}/nav", response_model=FundNavResult)
def get_fund_nav(
    fund_code: str,
    days: Annotated[int, Query(ge=1, le=365)] = 60,
    svc: FundLookupService = Depends(get_fund_lookup_service),
) -> FundNavResult:
    """基金净值实时查（旁路，note='not persisted'）。净值 Decimal 字符串。"""
    return svc.get_fund_nav(fund_code=fund_code, days=days)
