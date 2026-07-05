"""资金档位博弈路由。薄路由 → service。
- /api/subjects/{id}/tiers_series（spec005 US1 多档时序）
- /api/flow/topology（spec006 资金流向拓扑）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quantchive.api.deps import get_conn
from quantchive.core.settings import get_settings
from quantchive.service.dto import FlowTopologyResult, TiersSeriesResult
from quantchive.service.flow_query_service import FlowQueryService
from quantchive.service.flow_topology_service import FlowTopologyService

router = APIRouter(prefix="/api/subjects", tags=["flow"])
topology_router = APIRouter(prefix="/api/flow", tags=["flow-topology"])


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


@topology_router.get("/topology", response_model=FlowTopologyResult)
def get_flow_topology(
    trade_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    tier: str = Query("main", pattern="^(main|super_large|large|medium|small)$"),
    top_sectors: int = Query(20, ge=1, le=40),
    top_stocks_per_sector: int = Query(10, ge=1, le=50),
    conn=Depends(get_conn),
) -> FlowTopologyResult:
    """资金流向拓扑：大盘→行业（单日快照，四档可切换）。abs 定线宽、红绿定方向。"""
    return FlowTopologyService(conn).get_topology(
        trade_date=trade_date, tier=tier, top_sectors=top_sectors,
        top_stocks_per_sector=top_stocks_per_sector)


@topology_router.get("/topology/sector/{sector_id}", response_model=FlowTopologyResult)
def get_sector_stocks(
    sector_id: int,
    trade_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    tier: str = Query("main", pattern="^(main|super_large|large|medium|small)$"),
    top_stocks: int = Query(10, ge=1, le=50),
    conn=Depends(get_conn),
) -> FlowTopologyResult:
    """行业下钻：该 L1 行业成分股 TopN + 其他（桑基就地展开 / Treemap）。"""
    return FlowTopologyService(conn).get_sector_stocks(
        sector_id=sector_id, trade_date=trade_date, tier=tier, top_stocks=top_stocks)
