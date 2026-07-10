"""基本面信号回测编排（阶段D×B）。读基本面报告期序列 + 价序列 → 事件 → 前向收益统计。

可见时点=法定披露截止日(保守 PIT,见 fundamental_signal);标签价=sina change_pct 链式(复权无关)。
复用 backtest_core.backtest_visible_events + backtest_service._price_index。
"""

from __future__ import annotations

import sqlite3

from quantchive.service.backtest_core import backtest_visible_events
from quantchive.service.backtest_service import BacktestService
from quantchive.service.dto import SignalBacktestResult, SignalBacktestStat
from quantchive.service.errors import NoDataForDate
from quantchive.service.fundamental_signal import (
    FUND_SIGNAL_KINDS,
    PROFIT_TURN_POSITIVE,
    REVENUE_ACCELERATE,
    ROE_JUMP,
    detect_fundamental_signals,
    ytd_to_single_quarter,
)

# 信号 → 驱动它的 performance 指标项
_KIND_ITEM = {
    PROFIT_TURN_POSITIVE: "net_profit_yoy",
    "profit_accelerate": "net_profit_yoy",
    REVENUE_ACCELERATE: "revenue_yoy",
    ROE_JUMP: "roe",
}
# 累计(YTD)口径项:需转单季再比较(审计F3:roe 是累计level,年内机械递增)。
# net_profit_yoy/revenue_yoy 是同比增长率(比率),不转(其口径局限见 fundamental_signal N2)。
_YTD_ITEMS = {"roe"}
_DEFAULT_HORIZONS = (20, 60)


class FundamentalBacktestService:
    """基本面信号历史回测(单股)。复用资金流回测的价指数 + 统计核。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._bt = BacktestService(conn)   # 复用 _price_index / subject 解析

    def backtest_stock(
        self, *, subject_id: int, kinds: list[str] | None = None,
        horizons: tuple[int, ...] = _DEFAULT_HORIZONS, lookback_years: int = 5,
        as_of: str | None = None, jump_bp: int = 2000,
    ) -> SignalBacktestResult:
        subj = self._conn.execute(
            "SELECT source_symbol, display_name FROM subject WHERE subject_id=?",
            (subject_id,)).fetchone()
        if subj is None:
            raise NoDataForDate("主体不存在", detail={"subject_id": subject_id})
        kinds = kinds or list(FUND_SIGNAL_KINDS)

        # 价序列(标签):复用资金流回测的 sina 链式价指数 + as_of/lookback 逻辑
        # 借 BacktestService 组装 flow_rows/price——这里只需价,直接查 sina + 建指数
        as_of = as_of or self._latest_flow_date(subject_id)
        if not as_of:
            raise NoDataForDate(f"{subj['display_name']} 无行情(需 sina_flow)",
                                detail={"subject_id": subject_id})
        from datetime import date as _date, timedelta
        lookback_start = (_date.fromisoformat(as_of) - timedelta(days=365 * lookback_years)).isoformat()
        flow_rows = self._bt._obs.series_daily(
            subject_id=subject_id, sources=["sina_flow"], nonnull_column="main_net_cents",
            start_date=lookback_start, end_date=as_of)
        price_by_date, _note = self._bt._price_index(
            subject_id, flow_rows, lookback_start, as_of, "sina")
        if not price_by_date:
            raise NoDataForDate(f"{subj['display_name']} 无可用标签价",
                                detail={"subject_id": subject_id})
        ordered_dates = sorted(price_by_date)

        stats: list[SignalBacktestStat] = []
        for kind in kinds:
            item = _KIND_ITEM.get(kind)
            if item is None:
                continue
            series = self._period_series(subject_id, item, lookback_start)
            if item in _YTD_ITEMS:
                series = ytd_to_single_quarter(series)   # 累计→单季(审计F3)
            hits = detect_fundamental_signals(series, kind=kind, jump_bp=jump_bp)
            # 同 kind 同可见日去重(如上年报+本Q1同日截止,审计N4)
            visible = sorted({h.visible_date for h in hits})
            for h in horizons:
                st = backtest_visible_events(
                    visible, price_by_date, ordered_dates, kind=kind, horizon=h)
                stats.append(_to_dto_stat(st))

        return SignalBacktestResult(
            subject_id=subject_id, source_symbol=subj["source_symbol"],
            display_name=subj["display_name"], as_of=as_of,
            lookback_start=ordered_dates[0] if ordered_dates else lookback_start,
            stats=stats,
            price_source=("基本面信号(可见日=法定披露截止日,保守PIT;逾期披露尾部除外);"
                          "⚠️数值为最新重述口径非首披值(数据源限制,日期轴无泄漏、数值轴可能含重述);"
                          "sina链式价"))

    def _latest_flow_date(self, subject_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(trade_date) FROM observation WHERE subject_id=? "
            "AND source_code='sina_flow' AND value_type='daily_final'",
            (subject_id,)).fetchone()
        return row[0] if row else None

    def _period_series(self, subject_id: int, item: str, lookback_start: str) -> list[tuple[str, int | None]]:
        """某 performance 指标按报告期升序序列。

        下界(审计F1):只取可见日 ≥ lookback_start 的报告期(否则老事件在价格窗外,
        forward_return_from_visible 会拒但仍白算)。多留 1 期给单季/环比比较用前值。
        """
        from quantchive.service.fundamental_signal import report_period_deadline
        rows = self._conn.execute(
            "SELECT report_period, value_int FROM fundamental_item "
            "WHERE subject_id=? AND statement='performance' AND item=? "
            "ORDER BY report_period", (subject_id, item)).fetchall()
        full = [(r[0], r[1]) for r in rows]
        # 保留可见日在窗内的期,外加紧邻前一期(供 prev/cur 比较,其自身不产事件)
        keep_from = 0
        for i, (period, _v) in enumerate(full):
            if report_period_deadline(period) >= lookback_start:
                keep_from = max(0, i - 1)
                break
        else:
            return []
        return full[keep_from:]


def _to_dto_stat(st) -> SignalBacktestStat:
    from decimal import Decimal

    def pct(x: float) -> str:
        return str((Decimal(str(x)) * 100).quantize(Decimal("0.1")))

    def bp_pct(bp: int) -> str:
        v = (Decimal(bp) / 100).quantize(Decimal("0.01"))
        return f"+{v}" if v >= 0 else str(v)

    return SignalBacktestStat(
        signal_kind=st.kind, horizon=st.horizon, trigger_count=st.trigger_count,
        win_rate=pct(st.win_rate), avg_return_pct=bp_pct(st.avg_return_bp),
        median_return_pct=bp_pct(st.median_return_bp),
        wilson_low=pct(st.wilson_low), wilson_high=pct(st.wilson_high),
        baseline_win_rate=pct(st.baseline_win_rate), reliable=st.reliable, note=st.note)
