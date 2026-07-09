"""信号扫描（阶段B 前端）。盘后预计算:全市场逐股检测某日是否命中信号 → 落 signal_hit。

仿 derive_sector_tiers 的盘后派生模式:批量取数 + 逐股 detect_signals + 幂等 upsert + 审计。
前端秒读 list_hits(kind, trade_date),不再每次跑 8s 全市场扫描。
"""

from __future__ import annotations

from datetime import date as _date, datetime as _dt, timedelta, timezone as _tz
from itertools import groupby

from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import AssetClass, Caliber, RunType, RunStatus, SubjectKind, SubjectLevel
from quantchive.service.signal_lib import SIGNAL_KINDS, detect_signals

_FLOW_SRC = "sina_flow"


def scan_and_store(
    conn, *, trade_date: str | None = None, window_days: int = 45,
    source_code: str = _FLOW_SRC, progress: "object | None" = None,
) -> dict:
    """扫描全市场:每股最近 window_days 取数 → 4信号检测 → 命中日=trade_date 的落 signal_hit。

    幂等(UNIQUE upsert)、宁缺勿假(无命中不落)、无前视(只用 trade_date 及之前)。
    window_days 需 ≥ z_window(20)对应交易日 + 余量,否则超大单异动 z-score 算不出(45日历≈31交易日)。
    """
    obs = ObservationDao(conn)
    subject_dao = SubjectDao(conn)
    run_dao = RunDao(conn)

    if trade_date is None:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM observation WHERE source_code=? AND value_type='daily_final'",
            (source_code,)).fetchone()
        trade_date = row[0] if row else None
    if not trade_date:
        return {"trade_date": None, "hits": 0, "reason": "无 sina 日线"}

    start = (_date.fromisoformat(trade_date) - timedelta(days=window_days)).isoformat()
    now_iso = _dt.now(_tz.utc).isoformat()
    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.MARKET_AGGREGATE, caliber=Caliber.EASTMONEY,
        trade_date=trade_date, minute_slot="EOD", adapter_version="scan-signals-v1",
        subject_scope="signal_scan", asset_class_code=AssetClass.A_SHARE.value)

    sids = [s["subject_id"] for s in subject_dao.list_by(
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
        subject_kind=SubjectKind.STOCK)]
    rows = obs.series_daily_range_batch(
        subject_ids=sids, start_date=start, end_date=trade_date,
        sources=[source_code], nonnull_column="main_net_cents")

    hits = 0
    scanned = 0
    for _sid, grp in groupby(rows, key=lambda r: r.subject_id):
        g = list(grp)
        scanned += 1
        if not g or g[-1].trade_date != trade_date:
            continue                     # 该股当日无数据 → 跳过(宁缺勿假)
        for kind in SIGNAL_KINDS:
            sigs = detect_signals(g, kind=kind)
            # 只保留"命中日=目标交易日"的信号(今日闪该信号)
            hit = next((s for s in sigs if s.trade_date == trade_date), None)
            if hit is None:
                continue
            conn.execute(
                """INSERT INTO signal_hit (subject_id, trade_date, kind, strength, created_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(subject_id, trade_date, kind) DO UPDATE SET
                     strength=excluded.strength, created_at=excluded.created_at""",
                (_sid, trade_date, kind, hit.strength, now_iso))
            hits += 1
        if progress and scanned % 1000 == 0:
            progress(scanned, len(sids), "")

    run_dao.finish(run_id, status=RunStatus.SUCCESS, subjects_ok=hits, subjects_failed=0)
    return {"trade_date": trade_date, "scanned": scanned, "hits": hits}


def list_hits(conn, *, kind: str, trade_date: str | None = None, top_n: int = 50) -> dict:
    """读某信号某日命中股(按 strength 降序 top_n),带最新价/名/涨跌供展示。"""
    if trade_date is None:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM signal_hit WHERE kind=?", (kind,)).fetchone()
        trade_date = row[0] if row else None
    if not trade_date:
        return {"kind": kind, "trade_date": None, "rows": [], "available_dates": []}

    # signal_hit join 该股当日 sina 行(价/涨跌)
    q = conn.execute(
        """SELECT h.subject_id, s.source_symbol, s.display_name, h.strength,
                  o.price_micro, o.change_pct_bp
           FROM signal_hit h
           JOIN subject s ON s.subject_id = h.subject_id
           LEFT JOIN observation o ON o.subject_id = h.subject_id
                AND o.trade_date = h.trade_date AND o.source_code = ?
                AND o.value_type = 'daily_final'
           WHERE h.kind = ? AND h.trade_date = ?
           ORDER BY h.strength DESC LIMIT ?""",
        (_FLOW_SRC, kind, trade_date, top_n)).fetchall()
    rows = [{
        "subject_id": r[0], "source_symbol": r[1], "display_name": r[2],
        "strength": r[3],
        "price": (None if r[4] is None else str(r[4] / 1_000_000)),
        "change_pct": (None if r[5] is None else str(r[5] / 100)),
    } for r in q]

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM signal_hit WHERE kind=? ORDER BY trade_date DESC LIMIT 60",
        (kind,)).fetchall()]
    return {"kind": kind, "trade_date": trade_date, "rows": rows, "available_dates": dates}
