"""元信息路由（US4/FR-004）。能力自描述 + 主体清单（下钻寻址）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from quantchive.api.deps import get_query_service
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel
from quantchive.service.dto import SubjectCapabilityView, SubjectRef
from quantchive.service.query_service import QueryService

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/capability", response_model=SubjectCapabilityView)
def get_capability(
    asset_class: AssetClass,
    subject_kind: SubjectKind,
    svc: QueryService = Depends(get_query_service),
) -> SubjectCapabilityView:
    """主体×指标能力自描述（不支持的指标明确告知，不伪造）。"""
    return svc.get_capability(asset_class=asset_class, subject_kind=subject_kind)


@router.get("/subjects", response_model=list[SubjectRef])
def get_subjects(
    asset_class: AssetClass,
    level: SubjectLevel | None = None,
    subject_kind: SubjectKind | None = None,
    svc: QueryService = Depends(get_query_service),
) -> list[SubjectRef]:
    """主体清单（下钻寻址）。跨品种不混比——按 asset_class 过滤。"""
    return svc.get_subjects(
        asset_class=asset_class, level=level, subject_kind=subject_kind)
