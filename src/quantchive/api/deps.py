"""API 依赖注入（contracts/api.md）。提供 per-request QueryService。"""

from __future__ import annotations

import sqlite3

from quantchive.core.settings import get_settings
from quantchive.service.query_service import QueryService


def get_conn() -> sqlite3.Connection:
    settings = get_settings()
    from quantchive.core.db import connect
    return connect(settings.db_path)


def get_query_service() -> QueryService:
    settings = get_settings()
    conn = get_conn()
    return QueryService(conn, retention_trade_days=settings.retention_trade_days)


def get_fund_lookup_service():
    """基金净值旁路服务（物理隔离：只吃 FundSource，无 dao 写句柄）。"""
    from quantchive.datasource.fund_src import FundSource
    from quantchive.service.fund_lookup_service import FundLookupService
    return FundLookupService(FundSource())
