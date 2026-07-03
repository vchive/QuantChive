"""T058: AdaptiveInterval AIMD（成功加性靠拢、限流乘性退避、封顶/封底）。"""

from __future__ import annotations

import pytest

from quantchive.scheduler.interval import AdaptiveInterval


def _iv(**kw):
    kw.setdefault("target_sec", 60)
    kw.setdefault("min_sec", 60)
    kw.setdefault("max_sec", 600)
    return AdaptiveInterval(**kw)


def test_starts_at_target() -> None:
    assert _iv().current == 60


def test_rate_limit_backs_off_multiplicatively() -> None:
    iv = _iv(backoff_factor=2.0)
    assert iv.on_rate_limited() == 120
    assert iv.on_rate_limited() == 240
    assert iv.on_rate_limited() == 480
    assert iv.on_rate_limited() == 600   # 封顶 max
    assert iv.on_rate_limited() == 600


def test_success_additively_returns_to_target() -> None:
    iv = _iv(additive_step_sec=100, backoff_factor=2.0)
    iv.on_rate_limited()          # 120
    iv.on_rate_limited()          # 240
    assert iv.on_success() == 140  # 240-100
    assert iv.on_success() == 60   # 40 下越 target → 收敛到 target(60)
    assert iv.on_success() == 60   # 稳定


def test_target_above_min_stable_on_success() -> None:
    iv = _iv(target_sec=300, min_sec=60, max_sec=600)
    assert iv.on_success() == 300


def test_invalid_bounds_raise() -> None:
    with pytest.raises(ValueError):
        AdaptiveInterval(target_sec=30, min_sec=60, max_sec=600)   # target<min


def test_reset() -> None:
    iv = _iv()
    iv.on_rate_limited()
    iv.reset()
    assert iv.current == 60
