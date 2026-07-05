"""单主体时序回看路由（US5）。薄路由 → get_subject_series。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantchive.api.deps import get_query_service
from quantchive.models.enums import SortField
from quantchive.service.dto import SubjectSeriesResult
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/subjects", tags=["subjects"])


@router.get("/{subject_id}/series", response_model=SubjectSeriesResult)
def get_subject_series(
    subject_id: int,
    metric: SortField = SortField.MAIN_NET,
    trade_date: str | None = None,
    granularity: str = "intraday",
    svc: QueryService = Depends(get_query_service),
) -> SubjectSeriesResult:
    """单主体单指标时序。granularity='daily' 跨日历史 / 'intraday' 当日分钟。

    断点 value=null 不连线；intraday 超保留窗→422 OUT_OF_WINDOW。
    """
    return svc.get_subject_series(
        subject_id=subject_id, metric=metric, trade_date=trade_date, granularity=granularity)
