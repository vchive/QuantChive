"""单主体时序回看路由（US5）。薄路由 → get_subject_series。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_query_service
from quantchive.models.enums import SortField
from quantchive.service.dto import BatchSeriesResult, SubjectSeriesResult
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


@router.get("/{subject_id}/series_range", response_model=SubjectSeriesResult)
def get_subject_series_range(
    subject_id: int,
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    metric: SortField = SortField.MAIN_NET,
    svc: QueryService = Depends(get_query_service),
) -> SubjectSeriesResult:
    """单主体日线区间取数（回测/策略）。任意历史区间，不受实时保留窗限制。end_date 为 as-of 上界。"""
    return svc.get_subject_series_range(
        subject_id=subject_id, metric=metric, start_date=start_date, end_date=end_date)


@router.get("/series_batch", response_model=BatchSeriesResult)
def get_subjects_series_batch(
    subject_ids: str = Query(..., description="逗号分隔的 subject_id"),
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    metric: SortField = SortField.MAIN_NET,
    svc: QueryService = Depends(get_query_service),
) -> BatchSeriesResult:
    """多主体日线区间批量（回测组合取数）。subject_ids 逗号分隔，一次取全部。"""
    ids = [int(x) for x in subject_ids.split(",") if x.strip()]
    return svc.get_subjects_series_batch(
        subject_ids=ids, metric=metric, start_date=start_date, end_date=end_date)
