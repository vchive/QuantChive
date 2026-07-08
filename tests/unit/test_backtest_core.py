"""B2 回测统计纯函数单测(前向收益链式、Wilson CI 已知值、样本不足标注)。"""

from __future__ import annotations

from quantchive.service.backtest_core import (
    backtest_signal,
    forward_return_bp,
    wilson_interval,
)
from quantchive.service.signal_lib import SignalHit


def test_forward_return_after_signal() -> None:
    """信号日之后 horizon 日收益 = (P[t+h]/P[t]-1)*10000,严格错位。"""
    dates = ["d0", "d1", "d2", "d3", "d4"]
    px = {"d0": 100_000_000, "d1": 101_000_000, "d2": 110_000_000,
          "d3": 100_000_000, "d4": 100_000_000}
    # 信号在 d0,horizon=2 → P[d2]/P[d0]-1 = +10% = 1000bp
    assert forward_return_bp(px, "d0", dates, 2) == 1000


def test_forward_return_insufficient_future() -> None:
    """未来不足 horizon 天 → None(不看不到的未来)。"""
    dates = ["d0", "d1"]
    px = {"d0": 100_000_000, "d1": 101_000_000}
    assert forward_return_bp(px, "d1", dates, 3) is None   # d1 之后没3天


def test_wilson_interval_known() -> None:
    """Wilson CI 已知值:60/100 → 约 [0.502, 0.691]。"""
    low, high = wilson_interval(60, 100)
    assert abs(low - 0.5020) < 0.005 and abs(high - 0.6906) < 0.005


def test_wilson_zero_n() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_backtest_sample_warning() -> None:
    """样本 < 30 → reliable=False + note。"""
    dates = [f"d{i}" for i in range(10)]
    px = {d: 100_000_000 + i * 1_000_000 for i, d in enumerate(dates)}  # 单调涨
    sigs = [SignalHit("d0", "accumulation"), SignalHit("d1", "accumulation")]
    stat = backtest_signal(sigs, px, dates, kind="accumulation", horizon=2)
    assert not stat.reliable and stat.note is not None and "不可靠" in stat.note
    assert stat.trigger_count == 2 and stat.win_count == 2   # 单调涨全胜


def test_backtest_baseline_computed() -> None:
    """基准=全期日涨占比(对照信号 edge)。单调涨→基准接近1。"""
    dates = [f"d{i}" for i in range(20)]
    px = {d: 100_000_000 + i * 1_000_000 for i, d in enumerate(dates)}
    stat = backtest_signal([SignalHit("d0", "accumulation")], px, dates,
                           kind="accumulation", horizon=1)
    assert stat.baseline_win_rate == 1.0   # 每天都涨
