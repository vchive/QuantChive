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
from quantchive.service.backtest_core import forward_return_from_visible, wilson_interval
from quantchive.service.fundamental_signal import (
    FUND_SIGNAL_KINDS,
    detect_fundamental_signals,
    ytd_to_single_quarter,
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
    written = 0
    for (kind, h), rs in returns.items():
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        low, high = wilson_interval(wins, n)
        srt = sorted(rs)
        median = 0 if not srt else (srt[n // 2] if n % 2 else round((srt[n // 2 - 1] + srt[n // 2]) / 2))
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

    RunDao(conn).finish(run_id, status=RunStatus.SUCCESS, subjects_ok=written, subjects_failed=0)
    return {"as_of": as_of, "stocks": stocks_done, "stats_written": written,
            "events": {f"{k}@{h}": len(v) for (k, h), v in returns.items()}}


def read_market_stats(conn: sqlite3.Connection) -> dict:
    """读全市场基本面信号统计(agent/端点秒读)。含重述披露。"""
    rows = conn.execute(
        "SELECT signal_kind, horizon, as_of, trigger_count, win_count, avg_return_bp, "
        "median_return_bp, win_rate_bp, wilson_low_bp, wilson_high_bp, baseline_bp, computed_at "
        "FROM market_signal_stat ORDER BY signal_kind, horizon").fetchall()
    if not rows:
        return {"stats": [], "disclosure": _DISCLOSURE,
                "note": "尚未计算(需 quantchive-collect --target fund-signal-stats)"}

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
            "stats": stats, "disclosure": _DISCLOSURE}
