"""全市场基本面信号聚合统计（precompute）。单股事件稀疏 → 全市场聚合让 CI 收窄到可信。

流式逐股(不全量物化):一次轻量三列价查询 ORDER BY sid,date → groupby 逐股链式价指数 →
配基本面 performance 序列 → 4信号检测(ROE单季化) → forward_return_from_visible(审计过) →
按 (kind,horizon) 累积 → Wilson CI → upsert market_signal_stat。

PIT 口径继承审计结论:可见日=法定截止日(逾期尾部已标注)、窗下界+snap上限、
数值为最新重述口径(F2 数据源限制,结果带 disclosure)。禁float(万分比整数)。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime as _dt, timezone as _tz
from decimal import Decimal
from itertools import groupby

from quantchive.dao.run_dao import RunDao
from quantchive.models.enums import Caliber, RunStatus, RunType
from quantchive.service.backtest_core import (
    forward_return_bp,
    forward_return_from_visible,
    wilson_interval,
)
from quantchive.service.fundamental_signal import (
    FUND_SIGNAL_KINDS,
    detect_fundamental_signals,
    ytd_to_single_quarter,
)
from quantchive.service.signal_lib import (
    SIGNAL_KINDS as FLOW_SIGNAL_KINDS,
    dedupe_hits_non_overlapping,
    detect_signals,
)

_KIND_ITEM = {
    "profit_turn_positive": "net_profit_yoy",
    "profit_accelerate": "net_profit_yoy",
    "revenue_accelerate": "revenue_yoy",
    "roe_jump": "roe",
}
_YTD_ITEMS = {"roe"}
_BASELINE_STRIDE = 5    # pooled 基准抽样步长(每股每5交易日取1入场点,防 O(N·D) 爆炸)

_DISCLOSURE = ("历史条件统计,非预测。可见时点=法定披露截止日(逾期披露尾部除外,保守);"
               "财务数值为最新重述口径(数据源限制);不构成投资建议")


def compute_market_fundamental_stats(
    conn: sqlite3.Connection, *, horizons: tuple[int, ...] = (20, 60),
    kinds: tuple[str, ...] | None = None, jump_bp: int = 2000,
    progress: "object | None" = None,
) -> dict:
    """全市场聚合并落 market_signal_stat。返回摘要。"""
    kinds = kinds or FUND_SIGNAL_KINDS
    now_iso = _dt.now(_tz.utc).isoformat()

    # 基本面 performance 序列一次全查,按股分组备查
    fund_rows = conn.execute(
        "SELECT subject_id, item, report_period, value_int FROM fundamental_item "
        "WHERE statement='performance' AND item IN ('net_profit_yoy','revenue_yoy','roe') "
        "ORDER BY subject_id, item, report_period").fetchall()
    fund_by_sid: dict[int, dict[str, list[tuple[str, int | None]]]] = {}
    for sid, grp in groupby(fund_rows, key=lambda r: r[0]):
        d: dict[str, list[tuple[str, int | None]]] = {}
        for _, item, period, v in grp:
            d.setdefault(item, []).append((period, v))
        fund_by_sid[sid] = d

    as_of_row = conn.execute(
        "SELECT MAX(trade_date) FROM observation WHERE source_code='sina_flow' "
        "AND value_type='daily_final'").fetchone()
    as_of = as_of_row[0] if as_of_row else None
    if not as_of:
        return {"stats_written": 0, "reason": "无行情"}

    run_id = RunDao(conn).start(
        source_code="eastmoney_fin", run_type=RunType.MARKET_AGGREGATE, caliber=Caliber.EASTMONEY,
        trade_date=as_of, minute_slot="STATS", adapter_version="fund-signal-stats-v1",
        subject_scope="market_fund_signals", asset_class_code="a_share")

    # 累积器
    returns: dict[tuple[str, int], list[int]] = {(k, h): [] for k in kinds for h in horizons}
    base_wins: dict[int, int] = {h: 0 for h in horizons}
    base_tot: dict[int, int] = {h: 0 for h in horizons}

    # 流式逐股:轻量三列价查询(ORDER BY 保证 groupby 可用)
    cur = conn.execute(
        "SELECT subject_id, trade_date, change_pct_bp FROM observation "
        "WHERE source_code='sina_flow' AND value_type='daily_final' "
        "AND change_pct_bp IS NOT NULL ORDER BY subject_id, trade_date")
    stocks_done = 0
    for sid, grp in groupby(cur, key=lambda r: r[0]):
        # 链式价指数(Decimal,复权无关)
        price_by_date: dict[str, int] = {}
        acc = Decimal(1_000_000)
        for _, d, chg in grp:
            acc = acc * (Decimal(10000 + chg) / Decimal(10000))
            price_by_date[d] = int(acc)
        ordered = sorted(price_by_date)
        if len(ordered) < max(horizons) + 1:
            continue

        # pooled 基准:每 stride 个交易日取一个入场点
        for h in horizons:
            for i in range(0, len(ordered) - h, _BASELINE_STRIDE):
                p0, p1 = price_by_date[ordered[i]], price_by_date[ordered[i + h]]
                base_tot[h] += 1
                if round((p1 / p0 - 1) * 10000) > 0:
                    base_wins[h] += 1

        # 信号事件 → 前向收益
        fund = fund_by_sid.get(sid)
        if fund:
            for kind in kinds:
                item = _KIND_ITEM[kind]
                series = fund.get(item)
                if not series:
                    continue
                if item in _YTD_ITEMS:
                    series = ytd_to_single_quarter(series)
                hits = detect_fundamental_signals(series, kind=kind, jump_bp=jump_bp)
                visible = sorted({x.visible_date for x in hits})
                for vd in visible:
                    for h in horizons:
                        r = forward_return_from_visible(price_by_date, ordered, vd, h)
                        if r is not None:
                            returns[(kind, h)].append(r)
        stocks_done += 1
        if progress and stocks_done % 1000 == 0:
            progress(stocks_done, 0, "")

    # 统计 → upsert
    written = _upsert_stats(conn, returns, base_wins, base_tot, as_of, now_iso)

    RunDao(conn).finish(run_id, status=RunStatus.SUCCESS, subjects_ok=written, subjects_failed=0)
    return {"as_of": as_of, "stocks": stocks_done, "stats_written": written,
            "events": {f"{k}@{h}": len(v) for (k, h), v in returns.items()}}


def _upsert_stats(conn, returns, base_wins, base_tot, as_of, now_iso, baseline_fn=None) -> int:
    """(kind,horizon)→returns 累积 → Wilson/中位/基准 → upsert market_signal_stat。

    baseline_fn(kind,h)→float 覆盖默认 pooled 基准(组合统计用:对照=基本面单独胜率)。
    """
    written = 0
    for (kind, h), rs in returns.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        low, high = wilson_interval(wins, n)
        srt = sorted(rs)
        median = 0 if not srt else (srt[n // 2] if n % 2 else round((srt[n // 2 - 1] + srt[n // 2]) / 2))
        if baseline_fn is not None:
            baseline = baseline_fn(kind, h)
        else:
            baseline = (base_wins[h] / base_tot[h]) if base_tot[h] else 0.0
        conn.execute(
            """INSERT INTO market_signal_stat
               (signal_kind, horizon, as_of, trigger_count, win_count, avg_return_bp,
                median_return_bp, win_rate_bp, wilson_low_bp, wilson_high_bp, baseline_bp, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signal_kind, horizon) DO UPDATE SET
                 as_of=excluded.as_of, trigger_count=excluded.trigger_count,
                 win_count=excluded.win_count, avg_return_bp=excluded.avg_return_bp,
                 median_return_bp=excluded.median_return_bp, win_rate_bp=excluded.win_rate_bp,
                 wilson_low_bp=excluded.wilson_low_bp, wilson_high_bp=excluded.wilson_high_bp,
                 baseline_bp=excluded.baseline_bp, computed_at=excluded.computed_at""",
            (kind, h, as_of, n, wins, round(sum(rs) / n) if n else 0, median,
             round((wins / n) * 10000) if n else 0, round(low * 10000), round(high * 10000),
             round(baseline * 10000), now_iso))
        written += 1
    return written


class _FlowRow:
    """轻量行(喂 detect_signals,避免构造 33 字段 ObservationRow ×590万)。"""

    __slots__ = ("trade_date", "price_micro", "main_net_cents", "super_large_net_cents")

    def __init__(self, trade_date, price_micro, main_net, super_large_net):
        self.trade_date = trade_date
        self.price_micro = price_micro
        self.main_net_cents = main_net
        self.super_large_net_cents = super_large_net


def compute_market_flow_stats(
    conn: sqlite3.Connection, *, horizons: tuple[int, ...] = (1, 5, 20),
    kinds: tuple[str, ...] | None = None, progress: "object | None" = None,
) -> dict:
    """全市场资金流信号聚合(吸筹/派发/连续净流入/超大单异动)。

    统计诚实:同股同信号 non-overlapping 去重(按最大 horizon,防滑窗连日触发假性收窄CI);
    同日横截面聚簇在 disclosure 明示。信号检测复用审计过的 detect_signals(trailing窗防前视);
    标签价=sina change_pct 链式(复权无关)。horizons 短(1/5/20):资金流是快信号。
    """
    kinds = kinds or FLOW_SIGNAL_KINDS
    now_iso = _dt.now(_tz.utc).isoformat()
    max_h = max(horizons)

    as_of_row = conn.execute(
        "SELECT MAX(trade_date) FROM observation WHERE source_code='sina_flow' "
        "AND value_type='daily_final'").fetchone()
    as_of = as_of_row[0] if as_of_row else None
    if not as_of:
        return {"stats_written": 0, "reason": "无行情"}

    run_id = RunDao(conn).start(
        source_code="sina_flow", run_type=RunType.MARKET_AGGREGATE, caliber=Caliber.EASTMONEY,
        trade_date=as_of, minute_slot="STATS", adapter_version="flow-signal-stats-v1",
        subject_scope="market_flow_signals", asset_class_code="a_share")

    returns: dict[tuple[str, int], list[int]] = {(k, h): [] for k in kinds for h in horizons}
    base_wins: dict[int, int] = {h: 0 for h in horizons}
    base_tot: dict[int, int] = {h: 0 for h in horizons}

    cur = conn.execute(
        "SELECT subject_id, trade_date, main_net_cents, super_large_net_cents, "
        "price_micro, change_pct_bp FROM observation "
        "WHERE source_code='sina_flow' AND value_type='daily_final' "
        "AND main_net_cents IS NOT NULL AND change_pct_bp IS NOT NULL "
        "ORDER BY subject_id, trade_date")
    stocks_done = 0
    for _sid, grp in groupby(cur, key=lambda r: r[0]):
        rows: list[_FlowRow] = []
        price_by_date: dict[str, int] = {}
        acc = Decimal(1_000_000)
        for _, d, main, slarge, px, chg in grp:
            rows.append(_FlowRow(d, px, main, slarge))
            acc = acc * (Decimal(10000 + chg) / Decimal(10000))
            price_by_date[d] = int(acc)     # 标签用链式价指数(复权无关);信号价用原 price_micro
        ordered = sorted(price_by_date)
        if len(ordered) < max_h + 1:
            continue

        for h in horizons:
            for i in range(0, len(ordered) - h, _BASELINE_STRIDE):
                p0, p1 = price_by_date[ordered[i]], price_by_date[ordered[i + h]]
                base_tot[h] += 1
                if round((p1 / p0 - 1) * 10000) > 0:
                    base_wins[h] += 1

        for kind in kinds:
            hits = detect_signals(rows, kind=kind)
            hits = dedupe_hits_non_overlapping(hits, ordered, max_h)   # 统计诚实:非重叠
            for hit in hits:
                for h in horizons:
                    r = forward_return_bp(price_by_date, hit.trade_date, ordered, h)
                    if r is not None:
                        returns[(kind, h)].append(r)
        stocks_done += 1
        if progress and stocks_done % 1000 == 0:
            progress(stocks_done, 0, "")

    written = _upsert_stats(conn, returns, base_wins, base_tot, as_of, now_iso)
    RunDao(conn).finish(run_id, status=RunStatus.SUCCESS, subjects_ok=written, subjects_failed=0)
    return {"as_of": as_of, "stocks": stocks_done, "stats_written": written,
            "events": {f"{k}@{h}": len(v) for (k, h), v in returns.items()}}


_FLOW_DISCLOSURE = ("历史条件统计,非预测。同股同信号已做非重叠去重(触发后horizon内不重复计);"
                    "但同日多股触发受共同市场因素影响,有效独立样本低于名义n;不构成投资建议")


_COMBO_WINDOW = 5   # 资金流确认条件窗:入场日前5个交易日(含入场日),trailing 无前视


def compute_market_combo_stats(
    conn: sqlite3.Connection, *, horizons: tuple[int, ...] = (20, 60),
    jump_bp: int = 2000, progress: "object | None" = None,
) -> dict:
    """组合条件统计(阶段E-E1):基本面事件 × 近5交易日资金流确认 → 叠加有没有增益。

    这是 ML(E2)的先决问题:若组合不比基本面单独更强,线性组合弱信号变不出alpha。
    对照=**基本面信号单独**的胜率(同 pass 同 as_of,存 baseline_bp)→ beats_baseline
    直接回答"叠加是否显著增益"。多重检验(4×4×2=32组合)在 disclosure 披露。
    """
    from bisect import bisect_left
    from collections import defaultdict

    now_iso = _dt.now(_tz.utc).isoformat()
    max_h = max(horizons)

    # 基本面 performance 序列(同 fundamental 版)
    fund_rows = conn.execute(
        "SELECT subject_id, item, report_period, value_int FROM fundamental_item "
        "WHERE statement='performance' AND item IN ('net_profit_yoy','revenue_yoy','roe') "
        "ORDER BY subject_id, item, report_period").fetchall()
    fund_by_sid: dict[int, dict[str, list[tuple[str, int | None]]]] = {}
    for sid, grp in groupby(fund_rows, key=lambda r: r[0]):
        d: dict[str, list[tuple[str, int | None]]] = {}
        for _, item, period, v in grp:
            d.setdefault(item, []).append((period, v))
        fund_by_sid[sid] = d

    as_of_row = conn.execute(
        "SELECT MAX(trade_date) FROM observation WHERE source_code='sina_flow' "
        "AND value_type='daily_final'").fetchone()
    as_of = as_of_row[0] if as_of_row else None
    if not as_of:
        return {"stats_written": 0, "reason": "无行情"}

    run_id = RunDao(conn).start(
        source_code="sina_flow", run_type=RunType.MARKET_AGGREGATE, caliber=Caliber.EASTMONEY,
        trade_date=as_of, minute_slot="STATS", adapter_version="combo-signal-stats-v1",
        subject_scope="market_combo_signals", asset_class_code="a_share")

    fund_returns: dict[tuple[str, int], list[int]] = defaultdict(list)   # 对照(基本面单独)
    combo_returns: dict[tuple[str, int], list[int]] = defaultdict(list)

    cur = conn.execute(
        "SELECT subject_id, trade_date, main_net_cents, super_large_net_cents, "
        "price_micro, change_pct_bp FROM observation "
        "WHERE source_code='sina_flow' AND value_type='daily_final' "
        "AND main_net_cents IS NOT NULL AND change_pct_bp IS NOT NULL "
        "ORDER BY subject_id, trade_date")
    stocks_done = 0
    for sid, grp in groupby(cur, key=lambda r: r[0]):
        fund = fund_by_sid.get(sid)
        rows: list[_FlowRow] = []
        price_by_date: dict[str, int] = {}
        acc = Decimal(1_000_000)
        for _, d, main, slarge, px, chg in grp:
            rows.append(_FlowRow(d, px, main, slarge))
            acc = acc * (Decimal(10000 + chg) / Decimal(10000))
            price_by_date[d] = int(acc)
        if not fund:
            continue                         # 组合只统计有基本面数据的股
        ordered = sorted(price_by_date)
        if len(ordered) < max_h + 1:
            continue

        # 资金流命中日集合(条件方,不去重)
        flow_dates = {k: {h.trade_date for h in detect_signals(rows, kind=k)}
                      for k in FLOW_SIGNAL_KINDS}

        for fund_kind in FUND_SIGNAL_KINDS:
            item = _KIND_ITEM[fund_kind]
            series = fund.get(item)
            if not series:
                continue
            if item in _YTD_ITEMS:
                series = ytd_to_single_quarter(series)
            hits = detect_fundamental_signals(series, kind=fund_kind, jump_bp=jump_bp)
            for vd in sorted({x.visible_date for x in hits}):
                returns_by_h = {}
                for h in horizons:
                    r = forward_return_from_visible(price_by_date, ordered, vd, h)
                    if r is not None:
                        returns_by_h[h] = r
                if not returns_by_h:
                    continue
                for h, r in returns_by_h.items():
                    fund_returns[(fund_kind, h)].append(r)
                # 入场日 trailing 窗内有该资金流信号 → 计入组合
                ei = bisect_left(ordered, vd)
                if ei >= len(ordered):
                    continue
                window_dates = set(ordered[max(0, ei - _COMBO_WINDOW): ei + 1])
                for flow_kind in FLOW_SIGNAL_KINDS:
                    if flow_dates[flow_kind] & window_dates:
                        ck = f"{fund_kind}+{flow_kind}"
                        for h, r in returns_by_h.items():
                            combo_returns[(ck, h)].append(r)
        stocks_done += 1
        if progress and stocks_done % 1000 == 0:
            progress(stocks_done, 0, "")

    # 对照:基本面单独胜率(同 pass 同 as_of)
    fund_wr = {(k, h): (sum(1 for r in rs if r > 0) / len(rs) if rs else 0.0)
               for (k, h), rs in fund_returns.items()}

    def _baseline(kind: str, h: int) -> float:
        return fund_wr.get((kind.split("+")[0], h), 0.0)

    written = _upsert_stats(conn, dict(combo_returns), {}, {}, as_of, now_iso,
                            baseline_fn=_baseline)
    RunDao(conn).finish(run_id, status=RunStatus.SUCCESS, subjects_ok=written, subjects_failed=0)
    return {"as_of": as_of, "stocks": stocks_done, "stats_written": written,
            "events": {f"{k}@{h}": len(v) for (k, h), v in combo_returns.items()}}


_COMBO_DISCLOSURE = ("历史条件统计,非预测。组合=基本面事件且入场前5交易日内有该资金流信号;"
                     "对照(baseline)=**该基本面信号单独**的胜率,beats_baseline=叠加有显著增益;"
                     "32个组合多重检验下单个显著需谨慎(期望~1-2个假阳性),多horizon同向更可信;不构成投资建议")


def read_market_stats(conn: sqlite3.Connection, *, family: str | None = None) -> dict:
    """读全市场信号统计(agent/端点秒读)。family: 'flow'|'fundamental'|'combo'|None(全部)。

    披露按信号族:基本面带重述口径披露;资金流带去重+同日聚簇披露;组合带多重检验披露。
    """
    rows = conn.execute(
        "SELECT signal_kind, horizon, as_of, trigger_count, win_count, avg_return_bp, "
        "median_return_bp, win_rate_bp, wilson_low_bp, wilson_high_bp, baseline_bp, computed_at "
        "FROM market_signal_stat ORDER BY signal_kind, horizon").fetchall()
    if family == "flow":
        rows = [r for r in rows if r[0] in FLOW_SIGNAL_KINDS]
        disclosure = _FLOW_DISCLOSURE
    elif family == "fundamental":
        rows = [r for r in rows if r[0] in FUND_SIGNAL_KINDS]
        disclosure = _DISCLOSURE
    elif family == "combo":
        rows = [r for r in rows if "+" in r[0]]
        disclosure = _COMBO_DISCLOSURE
    else:
        disclosure = _DISCLOSURE + " | 资金流信号: " + _FLOW_DISCLOSURE + " | 组合: " + _COMBO_DISCLOSURE
    if not rows:
        return {"stats": [], "disclosure": disclosure,
                "note": "尚未计算(需 quantchive-collect --target fund-signal-stats / flow-signal-stats)"}

    def pct(bp: int) -> str:
        return str((Decimal(bp) / 100).quantize(Decimal("0.1")))

    stats = [{
        "signal_kind": r[0], "horizon": r[1], "trigger_count": r[3],
        "win_rate": pct(r[7]), "wilson_low": pct(r[8]), "wilson_high": pct(r[9]),
        "avg_return_pct": str((Decimal(r[5]) / 100).quantize(Decimal("0.01"))),
        "median_return_pct": str((Decimal(r[6]) / 100).quantize(Decimal("0.01"))),
        "baseline_win_rate": pct(r[10]),
        "reliable": r[3] >= 30,
        "beats_baseline": r[8] > r[10],   # CI下界 > 基准 → 统计显著优于
    } for r in rows]
    return {"as_of": rows[0][2], "computed_at": rows[0][11],
            "stats": stats, "disclosure": disclosure}
