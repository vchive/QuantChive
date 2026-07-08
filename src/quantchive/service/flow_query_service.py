"""资金档位博弈多档对齐时序（spec005 · US1）。

一次返回四档(净额+流入+流出) + 主力/散户 + 价 + 全程累计 + 背离段。
流入/流出=(gross±net)//2 恒整数；累计=全程绝对(从最早日)；背离=简单方向背离。
金额算术全在后端(整数分求和)，只下发格式化字符串，前端只画不算（宪章III）。
"""

from __future__ import annotations

import sqlite3

from quantchive.core.money import (
    cents_to_yuan_str,
    micro_to_str,
    tier_inflow,
    tier_outflow,
)
from quantchive.core.trading_calendar import Clock, SystemClock
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.metric_source import resolve_metric_sources
from quantchive.models.enums import SortField, SourceType
from quantchive.service.dto import (
    DataProvenance,
    DivergenceSegment,
    TierPoint,
    TiersSeriesResult,
    TierValues,
)
from quantchive.service.errors import NoDataForDate


def _cents_str(c: int | None) -> str | None:
    return None if c is None else cents_to_yuan_str(c)


class FlowQueryService:
    def __init__(self, conn: sqlite3.Connection, *, retention_trade_days: int = 90,
                 clock: Clock | None = None) -> None:
        self._conn = conn
        self._obs = ObservationDao(conn)
        self._subject = SubjectDao(conn)
        self._retention = retention_trade_days
        self._clock = clock or SystemClock()

    def get_tiers_series(
        self, *, subject_id: int, granularity: str = "daily",
    ) -> TiersSeriesResult:
        subject = self._subject.get(subject_id)
        if subject is None:
            raise NoDataForDate("主体不存在", detail={"subject_id": subject_id})

        if granularity == "daily":
            # 各档 net 权威源（新浪五档），价权威源（baostock）——按指标解析
            flow_sources = resolve_metric_sources(SortField.MAIN_NET)
            rows = self._obs.tiers_rows_daily(
                subject_id=subject_id, days=self._retention, sources=flow_sources)
            has_gross = any(r.super_large_gross_cents is not None for r in rows)
        else:
            # 盘中：东财净额分钟序列，无 gross
            rows = self._obs.tiers_rows_intraday(subject_id=subject_id)
            has_gross = False

        if not rows:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无{'日线' if granularity=='daily' else '盘中'}档位数据"
                f"（需回填 quantchive-collect --source sina）",
                detail={"subject_id": subject_id})

        # 全程绝对累计（后端整数求和）
        cum_main = 0
        cum_retail = 0
        points: list[TierPoint] = []
        gap = 0
        for r in rows:
            main = r.main_net_cents
            retail = (None if (r.medium_net_cents is None and r.small_net_cents is None)
                      else (r.medium_net_cents or 0) + (r.small_net_cents or 0))
            if main is not None:
                cum_main += main
            if retail is not None:
                cum_retail += retail
            if main is None:
                gap += 1
            points.append(TierPoint(
                ts=r.trade_date if granularity == "daily" else r.minute_slot,
                price=(None if r.price_micro is None else micro_to_str(r.price_micro)),
                change_pct=(None if r.change_pct_bp is None
                            else str(r.change_pct_bp / 100)),
                super_large=self._tier(r.super_large_net_cents, r.super_large_gross_cents),
                large=self._tier(r.large_net_cents, r.large_gross_cents),
                medium=self._tier(r.medium_net_cents, r.medium_gross_cents),
                small=self._tier(r.small_net_cents, r.small_gross_cents),
                main_net=_cents_str(main), retail_net=_cents_str(retail),
                cum_main_net=cents_to_yuan_str(cum_main),
                cum_retail_net=cents_to_yuan_str(cum_retail),
            ))

        divergence = self._detect_divergence(rows)
        last_td = rows[-1].trade_date
        prov = DataProvenance(
            trade_date=last_td, source_type=SourceType(rows[-1].value_type),
            source_id=rows[-1].source_code, captured_at=rows[-1].minute_slot,
            is_stale=False, validation=self._validation_status(subject_id, last_td))
        return TiersSeriesResult(
            subject_id=subject_id, source_symbol=subject["source_symbol"],
            display_name=subject["display_name"], granularity=granularity,
            has_gross=has_gross, provenance=prov, points=points,
            divergence=divergence, gap_count=gap)

    def _validation_status(self, subject_id: int, trade_date: str) -> str | None:
        """该股该日跨源校验角标:有分歧→'divergence'、有记录全ok→'ok'、无记录→None(未校验)。"""
        row = self._conn.execute(
            """SELECT SUM(verdict='divergence'), COUNT(*) FROM cross_source_check
               WHERE subject_id=? AND trade_date=?""",
            (subject_id, trade_date)).fetchone()
        if not row or not row[1]:
            return None
        return "divergence" if row[0] else "ok"

    @staticmethod
    def _tier(net_cents: int | None, gross_cents: int | None) -> TierValues | None:
        if net_cents is None:
            return None
        if gross_cents is None:                 # 无 gross → 只净额，流入流出为空（诚实）
            return TierValues(net=cents_to_yuan_str(net_cents))
        return TierValues(
            net=cents_to_yuan_str(net_cents),
            inflow=cents_to_yuan_str(tier_inflow(gross_cents, net_cents)),
            outflow=cents_to_yuan_str(tier_outflow(gross_cents, net_cents)))

    @staticmethod
    def _detect_divergence(rows) -> list[DivergenceSegment]:
        """简单方向背离：整个窗口 价净变 与 主力累计净变 方向相反 → 吸筹/派发。"""
        pts = [r for r in rows if r.price_micro is not None and r.main_net_cents is not None]
        if len(pts) < 2:
            return []
        price_delta = pts[-1].price_micro - pts[0].price_micro
        cum_delta = sum(r.main_net_cents for r in pts)   # 全程累计主力净额净变
        if price_delta < 0 and cum_delta > 0:
            return [DivergenceSegment(from_ts=pts[0].trade_date, to_ts=pts[-1].trade_date,
                                      kind="accumulation")]
        if price_delta > 0 and cum_delta < 0:
            return [DivergenceSegment(from_ts=pts[0].trade_date, to_ts=pts[-1].trade_date,
                                      kind="distribution")]
        return []
