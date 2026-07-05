"""T032: 采集基类——失败分轮重试、过短判未取全下轮重取、超限如实计失败、请求间节流。"""

from __future__ import annotations

from quantchive.datasource._collector_base import run_per_subject


def test_all_succeed_first_round() -> None:
    out = run_per_subject(
        ["a", "b", "c"], lambda s: f"r_{s}", sleep=lambda x: None)
    assert len(out.ok) == 3 and out.failed == [] and out.rounds_used == 1


def test_failed_subject_retried_next_round() -> None:
    """b 首轮抛错、次轮成功 → 分轮重试补齐。"""
    attempts = {"b": 0}

    def fetch(s):
        if s == "b":
            attempts["b"] += 1
            if attempts["b"] == 1:
                raise RuntimeError("transient")
        return f"r_{s}"

    out = run_per_subject(["a", "b"], fetch, sleep=lambda x: None, max_rounds=2)
    assert out.failed == [] and len(out.ok) == 2
    assert out.rounds_used == 2                     # 用了第二轮补 b


def test_persistent_failure_counted() -> None:
    """始终失败的标的超轮次 → 如实计 failed（不假装成功）。"""
    def fetch(s):
        if s == "bad":
            raise RuntimeError("always")
        return f"r_{s}"

    out = run_per_subject(["ok", "bad"], fetch, sleep=lambda x: None, max_rounds=2)
    assert out.failed == ["bad"] and len(out.ok) == 1


def test_short_data_refetched() -> None:
    """结果过短（len<min_expected）判未取全 → 下轮重取；仍短则计失败。"""
    calls = {"x": 0}

    def fetch(s):
        calls["x"] += 1
        return [1] if calls["x"] == 1 else [1, 2, 3]   # 首轮短，次轮足

    out = run_per_subject(
        ["x"], fetch, sleep=lambda x: None, max_rounds=2,
        min_length=len, min_expected=3)
    assert out.failed == [] and out.ok[0][1] == [1, 2, 3]


def test_delay_throttles_between_requests() -> None:
    sleeps = []
    run_per_subject(["a", "b"], lambda s: s, sleep=lambda d: sleeps.append(d), delay=0.5)
    assert sleeps == [0.5, 0.5]                     # 每个 subject 前节流
