"""ingestion_run 审计 DAO（宪章 V，research.md D6 + spec002 泛化）。三态 status。

spec002 泛化：sectors_ok/sectors_failed → subjects_ok/subjects_failed、sector_scope →
subject_scope、+asset_class_code、+aggregate_coverage（大盘求和成分哈希/覆盖率）。
迁移铁律——旧参数名保留为别名，两套列都写，spec001 旧调用与旧测不破。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from quantchive.models.enums import Caliber, RunStatus, RunType


class RunDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(
        self, *, source_code: str, run_type: RunType, caliber: Caliber, trade_date: str,
        minute_slot: str | None, adapter_version: str, target_interval_sec: int | None = None,
        code_version: str | None = None, sector_scope: str | None = None,
        subject_scope: str | None = None, asset_class_code: str | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        scope = subject_scope if subject_scope is not None else sector_scope  # 别名兼容
        cur = self._conn.execute(
            """INSERT INTO ingestion_run
               (source_code, run_type, caliber, sector_scope, asset_class_code, trade_date,
                minute_slot, started_at, status, target_interval_sec, adapter_version,
                code_version, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source_code, run_type.value, caliber.value, scope, asset_class_code, trade_date,
             minute_slot, now, RunStatus.PARTIAL.value, target_interval_sec, adapter_version,
             code_version, now),
        )
        return int(cur.lastrowid)

    def finish(
        self, run_id: int, *, status: RunStatus, sectors_ok: int | None = None,
        sectors_failed: int | None = None, subjects_ok: int | None = None,
        subjects_failed: int | None = None, achieved_interval_sec: int | None = None,
        retry_count: int = 0, error_type: str | None = None, error_detail: dict | None = None,
        aggregate_coverage: dict | None = None,
        degraded: bool = False, used_source_code: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # 别名归一：优先 subjects_*，回落 sectors_*（旧调用），默认 0
        ok = subjects_ok if subjects_ok is not None else (sectors_ok or 0)
        failed = subjects_failed if subjects_failed is not None else (sectors_failed or 0)
        self._conn.execute(
            """UPDATE ingestion_run SET finished_at=?, status=?,
               sectors_ok=?, sectors_failed=?, subjects_ok=?, subjects_failed=?,
               achieved_interval_sec=?, retry_count=?, error_type=?, error_detail=?,
               aggregate_coverage=?, degraded=?, used_source_code=? WHERE run_id=?""",
            (now, status.value, ok, failed, ok, failed, achieved_interval_sec, retry_count,
             error_type, json.dumps(error_detail, ensure_ascii=False) if error_detail else None,
             json.dumps(aggregate_coverage, ensure_ascii=False) if aggregate_coverage else None,
             1 if degraded else 0, used_source_code, run_id),
        )

    def latest(self, caliber: Caliber | None = None) -> dict | None:
        if caliber is not None:
            row = self._conn.execute(
                "SELECT * FROM ingestion_run WHERE caliber=? ORDER BY run_id DESC LIMIT 1",
                (caliber.value,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM ingestion_run ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
