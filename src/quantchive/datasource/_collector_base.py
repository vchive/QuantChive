"""采集纪律基类（spec003 D8 · FR-016，仿 qlib BaseCollector）。

固化批量采集的限流纪律，各源复用而非各写一套：
- 低并发（单写者：串行 per-subject，契合 SQLite 单写者约束）。
- 请求间 delay（节流，可注入 sleep）。
- 失败标的**分轮有限重试**（只重试上一轮失败者，仿 qlib max_collector_count）。
- 完整性校验：结果过短判为「未取全」→ 纳入下轮重取（仿 qlib check_data_length）。
纯逻辑 + 可注入 sleep，mock 可测不联网（宪章 IV）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from quantchive.core.logging import get_logger

_log = get_logger(__name__)


@dataclass
class CollectOutcome:
    ok: list = field(default_factory=list)         # [(subject, result)]
    failed: list = field(default_factory=list)     # [subject]（超重试上限仍失败/仍过短）
    rounds_used: int = 0


def run_per_subject(
    subjects: list,
    fetch_one: Callable[[object], object],
    *,
    sleep: Callable[[float], None],
    delay: float = 0.0,
    max_rounds: int = 2,
    min_length: Callable[[object], int] | None = None,
    min_expected: int = 0,
) -> CollectOutcome:
    """对每个 subject 串行调 fetch_one，失败/过短分轮重试。

    - delay>0：每次 fetch 前 sleep(delay)（请求间节流）。
    - fetch_one 抛异常 → 该 subject 进下轮重试队列。
    - min_length + min_expected：结果长度 < min_expected 判未取全 → 下轮重取。
    - max_rounds 轮后仍失败/过短 → 计入 failed（如实，不假装成功）。
    """
    pending = list(subjects)
    outcome = CollectOutcome()
    for round_no in range(1, max_rounds + 1):
        outcome.rounds_used = round_no
        retry: list = []
        for subj in pending:
            if delay > 0:
                sleep(delay)
            try:
                result = fetch_one(subj)
            except Exception as exc:  # noqa: BLE001 单主体隔离 → 下轮重试
                _log.info("采集失败待重试", extra={"context": {
                    "round": round_no, "err": type(exc).__name__}})
                retry.append(subj)
                continue
            # 完整性校验：过短判未取全
            if min_length is not None and min_expected > 0 and min_length(result) < min_expected:
                retry.append(subj)
                continue
            outcome.ok.append((subj, result))
        pending = retry
        if not pending:
            break
    outcome.failed = pending                        # 超轮次仍未成 → 如实计失败
    return outcome
