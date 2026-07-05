"""API 异常处理（contracts/api.md）。QueryError.code → HTTP，统一响应体。"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from quantchive.service.errors import CODE_TO_HTTP, QueryError


async def query_error_handler(request: Request, exc: QueryError) -> JSONResponse:
    status = CODE_TO_HTTP.get(exc.code, 500)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
    )
