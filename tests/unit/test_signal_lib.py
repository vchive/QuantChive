"""B1 信号检测纯函数单测(造 ObservationRow,无DB,验防前视)。"""

from __future__ import annotations

from quantchive.dao.observation_dao import ObservationRow
from quantchive.service.signal_lib import (
    ACCUMULATION,
    DISTRIBUTION,
    NET_INFLOW_STREAK,
    SUPER_LARGE_SPIKE,
    detect_signals,
)


def _row(date, price, main, slarge=0):
    """最小 ObservationRow:只填信号用到的字段,其余 None。"""
    return ObservationRow(
        observation_id=0, subject_id=1, source_symbol="600000", display_name="x",
        source_code="sina_flow", trade_date=date, minute_slot="EOD",
        value_type="daily_final", granularity="daily", observed_at="t",
        net_amount_cents=None, main_net_cents=main,
        super_large_net_cents=slarge, large_net_cents=None,
        medium_net_cents=None, small_net_cents=None,
        super_large_gross_cents=None, large_gross_cents=None,
        medium_gross_cents=None, small_gross_cents=None,
        price_micro=price, change_pct_bp=None, volume=None,
        turnover_cents=None, turnover_pct_bp=None)


def test_accumulation_price_down_main_in() -> None:
    """价跌但主力累计净流入 → 吸筹。"""
    # 6日:价从100→95(跌),主力每日+10(累计+)
    rows = [_row(f"2026-06-0{i}", (100 - i) * 1_000_000, 10_00) for i in range(1, 7)]
    hits = detect_signals(rows, kind=ACCUMULATION, window=5)
    assert len(hits) == 1 and hits[0].kind == ACCUMULATION
    assert hits[0].strength == 60_00   # 6点×10元


def test_distribution_price_up_main_out() -> None:
    """价涨但主力累计净流出 → 派发。"""
    rows = [_row(f"2026-06-0{i}", (90 + i) * 1_000_000, -10_00) for i in range(1, 7)]
    hits = detect_signals(rows, kind=DISTRIBUTION, window=5)
    assert len(hits) == 1 and hits[0].kind == DISTRIBUTION


def test_no_divergence_when_aligned() -> None:
    """价涨+主力流入(同向,非背离) → 无吸筹/派发。"""
    rows = [_row(f"2026-06-0{i}", (90 + i) * 1_000_000, 10_00) for i in range(1, 7)]
    assert detect_signals(rows, kind=ACCUMULATION, window=5) == []
    assert detect_signals(rows, kind=DISTRIBUTION, window=5) == []


def test_net_inflow_streak() -> None:
    """主力连续3日净流入 → 触发(逐日,连续窗口每天都触发)。"""
    rows = [_row(f"2026-06-0{i}", 100_000_000, 5_00) for i in range(1, 6)]  # 5日全正
    hits = detect_signals(rows, kind=NET_INFLOW_STREAK, streak_n=3)
    # i=2,3,4 (第3~5天) 各自往前3天全正 → 3个触发点
    assert len(hits) == 3 and all(h.kind == NET_INFLOW_STREAK for h in hits)


def test_net_inflow_streak_broken() -> None:
    """中间一天净流出 → 打断连续,不触发。"""
    mains = [5_00, 5_00, -1_00, 5_00, 5_00]
    rows = [_row(f"2026-06-0{i+1}", 100_000_000, mains[i]) for i in range(5)]
    hits = detect_signals(rows, kind=NET_INFLOW_STREAK, streak_n=3)
    assert len(hits) == 0   # 无任何连续3日全正窗口


def test_super_large_spike_zscore() -> None:
    """超大单净额突增(trailing z-score超阈) → 异动。"""
    # 前20日超大单净额在 100万分附近小幅波动(std>0),第21日暴增
    rows = [_row(f"d{i}", 100_000_000, 0, slarge=1_000_000 + (i % 5) * 10_000)
            for i in range(20)]
    rows.append(_row("d20", 100_000_000, 0, slarge=100_000_000))  # 异动日
    hits = detect_signals(rows, kind=SUPER_LARGE_SPIKE, z_window=20, z_threshold=2.0)
    assert len(hits) == 1 and hits[0].kind == SUPER_LARGE_SPIKE


def test_super_large_spike_lookahead_guard() -> None:
    """z-score 只用 trailing 窗(不含当日),防全样本泄漏:前窗不足不触发。"""
    rows = [_row(f"d{i}", 100_000_000, 0, slarge=1_000_000) for i in range(10)]
    # z_window=20 但只有10天历史 → 无触发(trailing不足)
    assert detect_signals(rows, kind=SUPER_LARGE_SPIKE, z_window=20) == []
