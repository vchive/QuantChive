"""信号回测编排（阶段B,DB层,仿 MarketAggregator）。

双源取数对齐:sina_flow 流序列(信号) + baostock_hfq 价序列(标签),按 trade_date 内连接。
调纯函数 signal_lib.detect_signals + backtest_core.backtest_signal → SignalBacktestResult DTO。

防前视:end_date=as_of 上界(series_daily 天然截断);标签(前向收益)在信号日之后单独算。
金额整数分/基点→DTO字符串下发(禁前端float)。
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date, timedelta
from decimal import Decimal

from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.service.backtest_core import backtest_signal
from quantchive.service.dto import SignalBacktestResult, SignalBacktestStat
from quantchive.service.signal_lib import SIGNAL_KINDS, detect_signals
from quantchive.service.errors import NoDataForDate

_FLOW_SRC = "sina_flow"
_PRICE_SRC = "baostock_hfq"
_DEFAULT_HORIZONS = (1, 3, 5)


class BacktestService:
    """读双源→信号→回测→DTO。单股(全市场留后续)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._obs = ObservationDao(conn)
        self._subject = SubjectDao(conn)

    def backtest_stock(
        self, *, subject_id: int, kinds: list[str] | None = None,
        horizons: tuple[int, ...] = _DEFAULT_HORIZONS, lookback_years: int = 5,
        as_of: str | None = None, window: int = 5, streak_n: int = 3,
        z_window: int = 20, z_threshold: float = 2.0, price_source: str = "sina",
    ) -> SignalBacktestResult:
        subject = self._subject.get(subject_id)
        if subject is None:
            raise NoDataForDate("主体不存在", detail={"subject_id": subject_id})
        kinds = kinds or list(SIGNAL_KINDS)

        # as_of:默认最新有 sina 流的交易日(防前视上界)
        if as_of is None:
            row = self._conn.execute(
                "SELECT MAX(trade_date) FROM observation WHERE subject_id=? "
                "AND source_code=? AND value_type='daily_final'",
                (subject_id, _FLOW_SRC)).fetchone()
            as_of = row[0] if row else None
        if not as_of:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无资金流日线(需回填 sina_flow)",
                detail={"subject_id": subject_id})
        lookback_start = (_date.fromisoformat(as_of) - timedelta(days=365 * lookback_years)).isoformat()

        # 流序列(信号源):按 trade_date 升序
        flow_rows = self._obs.series_daily(
            subject_id=subject_id, sources=[_FLOW_SRC], nonnull_column="main_net_cents",
            start_date=lookback_start, end_date=as_of)
        if not flow_rows:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无资金流日线(需回填 sina_flow)",
                detail={"subject_id": subject_id})

        # 标签价序列(混合):hfq 可选(严谨总回报,需回填)/ sina 默认(change_pct链式,即时全覆盖)
        price_by_date, price_note = self._price_index(
            subject_id, flow_rows, lookback_start, as_of, price_source)
        if not price_by_date:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无可用标签价(需 sina_flow 日涨跌幅或 baostock_hfq)",
                detail={"subject_id": subject_id})
        ordered_dates = sorted(price_by_date)

        stats: list[SignalBacktestStat] = []
        for kind in kinds:
            signals = detect_signals(
                flow_rows, kind=kind, window=window, streak_n=streak_n,
                z_window=z_window, z_threshold=z_threshold)
            for h in horizons:
                st = backtest_signal(signals, price_by_date, ordered_dates, kind=kind, horizon=h)
                stats.append(SignalBacktestStat(
                    signal_kind=st.kind, horizon=st.horizon, trigger_count=st.trigger_count,
                    win_rate=_pct(st.win_rate), avg_return_pct=_bp_pct(st.avg_return_bp),
                    median_return_pct=_bp_pct(st.median_return_bp),
                    wilson_low=_pct(st.wilson_low), wilson_high=_pct(st.wilson_high),
                    baseline_win_rate=_pct(st.baseline_win_rate),
                    reliable=st.reliable, note=st.note))

        return SignalBacktestResult(
            subject_id=subject_id, source_symbol=subject["source_symbol"],
            display_name=subject["display_name"], as_of=as_of,
            lookback_start=ordered_dates[0] if ordered_dates else lookback_start,
            stats=stats, price_source=price_note)

    def _price_index(
        self, subject_id: int, flow_rows: list, lookback_start: str, as_of: str, price_source: str,
    ) -> tuple[dict[str, int], str]:
        """标签价序列(date→价微元)+ 用了哪个源的说明。

        hfq(严谨,需回填 baostock_hfq):后复权 close,总回报精确。缺则回退 sina。
        sina(默认,即时全覆盖):按 change_pct_bp 链式乘成锚不变价指数(复权无关、可复现;
        除息股息微偏,但信号vs基准对比中抵消)。
        """
        if price_source == "hfq":
            rows = self._obs.series_daily(
                subject_id=subject_id, sources=[_PRICE_SRC], nonnull_column="price_micro",
                start_date=lookback_start, end_date=as_of)
            pbd = {r.trade_date: r.price_micro for r in rows if r.price_micro}
            if pbd:
                return pbd, "baostock_hfq(后复权,总回报精确)"
            # hfq 未回填 → 回退 sina(诚实标注)
        # sina 默认:change_pct_bp 链式乘成价指数(起点 1e6 微元)
        idx: dict[str, int] = {}
        cur = Decimal(1_000_000)
        for r in flow_rows:
            if r.change_pct_bp is None:
                continue
            cur = cur * (Decimal(10000 + r.change_pct_bp) / Decimal(10000))
            idx[r.trade_date] = int(cur)
        note = "sina日涨跌幅链式(复权无关,含股息微偏但对比中抵消)"
        if price_source == "hfq":
            note = "hfq未回填,回退 " + note
        return idx, note


def _pct(x: float) -> str:
    """[0,1] 比率 → 百分比字符串(1位小数)。"""
    return str((Decimal(str(x)) * 100).quantize(Decimal("0.1")))


def _bp_pct(bp: int) -> str:
    """基点 → 百分比字符串带符号(1bp=0.01%)。"""
    v = (Decimal(bp) / 100).quantize(Decimal("0.01"))
    return f"+{v}" if v >= 0 else str(v)
