"""Service 层结构化错误（contracts/service.md）。Service 从不裸抛内置异常。"""

from __future__ import annotations


class QueryError(Exception):
    code: str = "QUERY_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class CaliberNotAvailable(QueryError):
    code = "CALIBER_DATA_UNAVAILABLE"


class MetricNotSupported(QueryError):
    code = "METRIC_NOT_SUPPORTED"


class OutOfRetentionWindow(QueryError):
    code = "OUT_OF_RETENTION_WINDOW"


class SectorNotFound(QueryError):
    code = "SECTOR_NOT_FOUND"


class NoDataForDate(QueryError):
    code = "NO_DATA_FOR_DATE"


# ---- spec002 通用错误（service-api.md）----


class SubjectNotFound(QueryError):
    code = "SUBJECT_NOT_FOUND"


class AssetClassNotProvisioned(QueryError):
    code = "ASSET_CLASS_NOT_PROVISIONED"


class OutOfWindow(QueryError):
    code = "OUT_OF_WINDOW"


class UnsupportedMetric(QueryError):
    code = "UNSUPPORTED_METRIC"


# code → HTTP（api 层用）
CODE_TO_HTTP: dict[str, int] = {
    "CALIBER_DATA_UNAVAILABLE": 503,
    "METRIC_NOT_SUPPORTED": 422,
    "OUT_OF_RETENTION_WINDOW": 422,
    "SECTOR_NOT_FOUND": 404,
    "NO_DATA_FOR_DATE": 404,
    # spec002
    "SUBJECT_NOT_FOUND": 404,
    "ASSET_CLASS_NOT_PROVISIONED": 422,
    "OUT_OF_WINDOW": 422,
    "UNSUPPORTED_METRIC": 422,
    "QUERY_ERROR": 500,
}
