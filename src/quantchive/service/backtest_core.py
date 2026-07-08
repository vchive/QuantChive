"""信号回测统计核（阶段B,纯函数,无DB）。

前向收益:信号日**之后** T 日,链式乘 hfq close 比值(后复权→复权无关、无标签漂移)。
Wilson 95% 置信区间:闭式解,纯 python math.sqrt(无 scipy)。
样本诚实:n<30 标注不可靠;给 CI 不给假精确点估计;对照无条件基准。

收益用基点(bp,整数,1bp=0.01%);价格用 hfq close 微元(price_micro,×1e6)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantchive.service.signal_lib import SignalHit

_MIN_RELIABLE = 30      # 独立样本统计下限(路线图铁律)


@dataclass(frozen=True)
class BacktestStat:
    """单信号×单horizon的回测统计(纯值)。"""

    kind: str
    horizon: int
    trigger_count: int
    win_count: int
    win_rate: float           # [0,1]
    avg_return_bp: int        # 平均前向收益(基点,四舍五入整数)
    median_return_bp: int
    wilson_low: float
    wilson_high: float
    baseline_win_rate: float  # 无条件基准(全期日涨占比)
    reliable: bool
    note: str | None = None


def forward_return_bp(
    price_by_date: dict[str, int], signal_date: str, ordered_dates: list[str], horizon: int,
) -> int | None:
    """信号日之后 horizon 个交易日的前向收益(基点)。

    price_by_date: date→hfq close 微元;ordered_dates: 全体交易日升序。
    收益 = (P[t+h]/P[t] − 1)×10000。信号日 t 之后才取(严格错位,防前视)。
    数据不足(未来不够 horizon 天 / 价缺失)→ None。
    """
    try:
        idx = ordered_dates.index(signal_date)
    except ValueError:
        return None
    fut = idx + horizon
    if fut >= len(ordered_dates):
        return None
    p0 = price_by_date.get(signal_date)
    p1 = price_by_date.get(ordered_dates[fut])
    if not p0 or not p1:
        return None
    return round((p1 / p0 - 1) * 10000)


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例 Wilson 95% 置信区间(闭式,纯 python)。n=0 → (0,0)。"""
    if n <= 0:
        return (0.0, 0.0)
    phat = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _baseline_win_rate(price_by_date: dict[str, int], ordered_dates: list[str], horizon: int) -> float:
    """无条件基准:全期任取一日,horizon 后上涨的占比(对照信号是否有 edge)。"""
    wins = 0
    tot = 0
    for i in range(len(ordered_dates) - horizon):
        p0 = price_by_date.get(ordered_dates[i])
        p1 = price_by_date.get(ordered_dates[i + horizon])
        if not p0 or not p1:
            continue
        tot += 1
        if p1 > p0:
            wins += 1
    return wins / tot if tot else 0.0


def backtest_signal(
    signals: list[SignalHit], price_by_date: dict[str, int], ordered_dates: list[str],
    *, kind: str, horizon: int,
) -> BacktestStat:
    """回测一类信号在某 horizon 的历史表现:胜率+Wilson CI+均值/中位数+基准对照。"""
    returns: list[int] = []
    for sig in signals:
        r = forward_return_bp(price_by_date, sig.trade_date, ordered_dates, horizon)
        if r is not None:
            returns.append(r)

    n = len(returns)
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins / n if n else 0.0
    avg = round(sum(returns) / n) if n else 0
    median = _median(returns) if n else 0
    low, high = wilson_interval(wins, n)
    baseline = _baseline_win_rate(price_by_date, ordered_dates, horizon)
    reliable = n >= _MIN_RELIABLE
    note = None if reliable else f"样本 {n} < {_MIN_RELIABLE},统计不可靠(仅供参考)"
    return BacktestStat(
        kind=kind, horizon=horizon, trigger_count=n, win_count=wins, win_rate=win_rate,
        avg_return_bp=avg, median_return_bp=median, wilson_low=low, wilson_high=high,
        baseline_win_rate=baseline, reliable=reliable, note=note)


def _median(xs: list[int]) -> int:
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else round((s[m - 1] + s[m]) / 2)
