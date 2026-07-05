"""运维健康路由（US运维，contracts/service-api.md）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantchive.api.deps import get_query_service
from quantchive.service.dto import HealthResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResult)
def health(svc: QueryService = Depends(get_query_service)) -> HealthResult:
    """最新观测交易日 + 最近采集运行 + 各源采集状态。"""
    return svc.health()
