"""逐日点资金流信号检测（阶段B,纯函数,无DB,禁float,防前视）。

每个信号是"某股某日是否触发"的逐日判定,只用 as-of 及之前的行(trailing 窗),
绝不看未来——回测的标签(前向收益)由 backtest_core 在信号日之后单独算。

输入统一为按 trade_date 升序的 ObservationRow 列表(单股)。金额整数分,禁 float。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantchive.dao.observation_dao import ObservationRow

# 信号类型
ACCUMULATION = "accumulation"          # 吸筹:价跌但主力累计净流入
DISTRIBUTION = "distribution"          # 派发:价涨但主力累计净流出
NET_INFLOW_STREAK = "net_inflow_streak"    # 连续N日主力净流入
SUPER_LARGE_SPIKE = "super_large_spike"    # 超大单净额异动(trailing z-score)

SIGNAL_KINDS = (ACCUMULATION, DISTRIBUTION, NET_INFLOW_STREAK, SUPER_LARGE_SPIKE)


@dataclass(frozen=True)
class SignalHit:
    """单股单日信号触发点(纯值,无 float 金额)。"""

    trade_date: str
    kind: str
    strength: int | None = None   # 语义随信号:累计净额(分)/连续天数/z-score×100


def _has(row: ObservationRow, attr: str) -> bool:
    return getattr(row, attr) is not None


def detect_signals(
    rows: list[ObservationRow], *, kind: str,
    window: int = 5, streak_n: int = 3, z_window: int = 20, z_threshold: float = 2.0,
) -> list[SignalHit]:
    """检测某类信号的所有逐日触发点。rows 必须按 trade_date 升序。

    防前视:判定第 i 天只用 rows[..i](含 i),不看 i 之后。
    window: 吸筹/派发回看窗;streak_n: 连续净流入天数;z_window/z_threshold: 超大单异动。
    """
    if kind == ACCUMULATION:
        return _divergence(rows, window=window, accumulation=True)
    if kind == DISTRIBUTION:
        return _divergence(rows, window=window, accumulation=False)
    if kind == NET_INFLOW_STREAK:
        return _net_inflow_streak(rows, streak_n=streak_n)
    if kind == SUPER_LARGE_SPIKE:
        return _super_large_spike(rows, z_window=z_window, z_threshold=z_threshold)
    raise ValueError(f"未知信号类型: {kind}")


def _divergence(rows: list[ObservationRow], *, window: int, accumulation: bool) -> list[SignalHit]:
    """价-资金背离逐日点信号(_detect_divergence 逐日滚动化)。

    吸筹:近 window 日价累计跌 且 主力近 window 日累计净流入>0。
    派发:近 window 日价累计涨 且 主力近 window 日累计净流出<0。
    """
    out: list[SignalHit] = []
    for i in range(window, len(rows)):
        win = rows[i - window:i + 1]                 # [i-window .. i],含端点 window+1 点
        pi, p0 = win[-1].price_micro, win[0].price_micro
        if pi is None or p0 is None:
            continue
        if any(not _has(r, "main_net_cents") for r in win):
            continue
        price_delta = pi - p0
        cum_main = sum(r.main_net_cents for r in win)
        if accumulation and price_delta < 0 and cum_main > 0:
            out.append(SignalHit(win[-1].trade_date, ACCUMULATION, strength=cum_main))
        elif (not accumulation) and price_delta > 0 and cum_main < 0:
            out.append(SignalHit(win[-1].trade_date, DISTRIBUTION, strength=cum_main))
    return out


def _net_inflow_streak(rows: list[ObservationRow], *, streak_n: int) -> list[SignalHit]:
    """主力净额连续 streak_n 日 > 0 → 触发(触发点=达成连续的当天)。"""
    out: list[SignalHit] = []
    for i in range(streak_n - 1, len(rows)):
        win = rows[i - streak_n + 1:i + 1]
        if any(not _has(r, "main_net_cents") for r in win):
            continue
        if all(r.main_net_cents > 0 for r in win):
            out.append(SignalHit(win[-1].trade_date, NET_INFLOW_STREAK, strength=streak_n))
    return out


def _super_large_spike(
    rows: list[ObservationRow], *, z_window: int, z_threshold: float,
) -> list[SignalHit]:
    """超大单净额 trailing z-score > 阈值 → 机构大买异动。

    z-score 只用 trailing z_window 日(不含当日)的均值/标准差,防全样本泄漏。
    """
    out: list[SignalHit] = []
    for i in range(z_window, len(rows)):
        cur = rows[i].super_large_net_cents
        if cur is None:
            continue
        hist = [r.super_large_net_cents for r in rows[i - z_window:i]
                if r.super_large_net_cents is not None]
        if len(hist) < z_window:
            continue
        mean = Decimal(sum(hist)) / Decimal(len(hist))
        var = sum((Decimal(x) - mean) ** 2 for x in hist) / Decimal(len(hist))
        if var <= 0:
            continue
        std = var.sqrt()
        z = (Decimal(cur) - mean) / std
        if z > Decimal(str(z_threshold)):
            out.append(SignalHit(rows[i].trade_date, SUPER_LARGE_SPIKE,
                                 strength=int(z * 100)))
    return out
