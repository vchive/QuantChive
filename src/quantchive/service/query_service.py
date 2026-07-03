"""QueryService —— 只读查询业务真源（contracts/service.md）。

人端路由与未来 agent Tool 消费同一实例（FR-012）。通用 subject×observation 模型：
大盘/板块/个股/ETF 排行、板块下钻、单主体时序、能力自描述。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from quantchive.core.money import (
    basis_points_to_str,
    cents_to_yuan_str,
    micro_to_str,
)
from quantchive.core.trading_calendar import SHANGHAI_TZ, Clock, SystemClock, TradingCalendar
from quantchive.dao.constituent_dao import ConstituentDao
from quantchive.dao.observation_dao import ObservationDao, ObservationRow
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import (
    AssetClass,
    Caliber,
    SectorType,
    SortField,
    SourceType,
    SubjectKind,
    SubjectLevel,
    ValueType,
)
from quantchive.service.capability_registry import CapabilityRegistry
from quantchive.service.dto import (
    DataProvenance,
    HealthResult,
    MarketOverviewResult,
    ObservationItem,
    RankingResult,
    SeriesPoint,
    SubjectCapabilityView,
    SubjectRef,
    SubjectSeriesResult,
)
from quantchive.service.errors import (
    NoDataForDate,
    OutOfWindow,
    SubjectNotFound,
    UnsupportedMetric,
)


def _dec(cents: int | None) -> Decimal | None:
    return None if cents is None else Decimal(cents_to_yuan_str(cents))


# ---- spec002 通用观测排行辅助 ----

_SECTOR_TYPE_TO_KIND = {
    SectorType.INDUSTRY: SubjectKind.INDUSTRY,
    SectorType.CONCEPT: SubjectKind.CONCEPT,
    SectorType.REGION: SubjectKind.REGION,
}

# 主体细类 → 序列粒度（能力自描述，contracts/datasource.md）
_SERIES_GRANULARITY = {
    SubjectKind.INDUSTRY: "1min",
    SubjectKind.CONCEPT: "1min",
    SubjectKind.REGION: "1min",
    SubjectKind.STOCK: "5min",
    SubjectKind.ETF: "5min",
    SubjectKind.MARKET_TOTAL: "5min",
    SubjectKind.OPEN_FUND: "daily",
}

# SortField → ObservationItem 取值器（双榜按符号切分，与 _SORT_ATTR 语义一致）
_OBS_SORT_ATTR = {
    SortField.MAIN_NET: lambda it: it.main_net,
    SortField.NET_AMOUNT: lambda it: it.net_amount if it.net_amount is not None else it.main_net,
    SortField.SUPER_LARGE_NET: lambda it: it.super_large_net,
    SortField.LARGE_NET: lambda it: it.large_net,
    SortField.MEDIUM_NET: lambda it: it.medium_net,
    SortField.SMALL_NET: lambda it: it.small_net,
    SortField.CHANGE_PCT: lambda it: it.change_pct,
}


def _obs_to_item(row: ObservationRow) -> ObservationItem:
    # 东财板块 net_amount 恒等 main_net（若观测未单独存 net_amount，用 main_net 回填对齐）
    net = row.net_amount_cents if row.net_amount_cents is not None else row.main_net_cents
    return ObservationItem(
        subject_id=row.subject_id, source_symbol=row.source_symbol,
        display_name=row.display_name,
        net_amount=_dec(net), main_net=_dec(row.main_net_cents),
        super_large_net=_dec(row.super_large_net_cents),
        large_net=_dec(row.large_net_cents), medium_net=_dec(row.medium_net_cents),
        small_net=_dec(row.small_net_cents),
        price=(None if row.price_micro is None else Decimal(str(row.price_micro)) / Decimal(1_000_000)),
        change_pct=(None if row.change_pct_bp is None else Decimal(str(row.change_pct_bp)) / Decimal(100)),
        volume=(None if row.volume is None else Decimal(str(row.volume))),
        turnover=_dec(row.turnover_cents),
    )


# 单主体单指标时序：SortField → (ObservationRow 取值器 → Decimal|None)
_METRIC_GETTER = {
    SortField.MAIN_NET: lambda r: (None if r.main_net_cents is None
                                   else Decimal(cents_to_yuan_str(r.main_net_cents))),
    SortField.NET_AMOUNT: lambda r: (
        None if (r.net_amount_cents is None and r.main_net_cents is None)
        else Decimal(cents_to_yuan_str(
            r.net_amount_cents if r.net_amount_cents is not None else r.main_net_cents))),
    SortField.SUPER_LARGE_NET: lambda r: (None if r.super_large_net_cents is None
                                          else Decimal(cents_to_yuan_str(r.super_large_net_cents))),
    SortField.LARGE_NET: lambda r: (None if r.large_net_cents is None
                                    else Decimal(cents_to_yuan_str(r.large_net_cents))),
    SortField.MEDIUM_NET: lambda r: (None if r.medium_net_cents is None
                                     else Decimal(cents_to_yuan_str(r.medium_net_cents))),
    SortField.SMALL_NET: lambda r: (None if r.small_net_cents is None
                                    else Decimal(cents_to_yuan_str(r.small_net_cents))),
    SortField.PRICE: lambda r: (None if r.price_micro is None
                                else Decimal(micro_to_str(r.price_micro))),
    SortField.CHANGE_PCT: lambda r: (None if r.change_pct_bp is None
                                     else Decimal(basis_points_to_str(r.change_pct_bp))),
    SortField.VOLUME: lambda r: (None if r.volume is None else Decimal(str(r.volume))),
}


def _metric_value(row: ObservationRow, metric: SortField) -> Decimal | None:
    getter = _METRIC_GETTER.get(metric)
    return getter(row) if getter else None


def _date_diff_days(later: str, earlier: str) -> int:
    """later - earlier 的天数（ISO 'YYYY-MM-DD'）。用于保留窗判定。"""
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


def _assemble_ranking(
    items: list[ObservationItem], *, mode: str, sort_by: SortField,
    asset_class: AssetClass, level: SubjectLevel, subject_kind: SubjectKind,
    prov: DataProvenance, has_five_tier: bool, full: bool, top_n: int,
) -> RankingResult:
    """items → RankingResult：unipolar 单榜 / bipolar 按排序字段符号切双榜。

    get_sector_ranking 与 get_stocks_in_sector 共用（双榜切分逻辑与 spec001 get_ranking 一致）。
    """
    result_kw = dict(
        asset_class=asset_class, level=level, subject_kind=subject_kind, sort_by=sort_by,
        mode=mode, provenance=prov, all_items=items if full else None,
        total_subjects=len(items), has_five_tier=has_five_tier,
    )
    if mode == "unipolar":
        return RankingResult(ranked_items=items[:top_n], **result_kw)
    sort_val = _OBS_SORT_ATTR[sort_by]
    positives = [it for it in items if sort_val(it) is not None and sort_val(it) > 0]
    negatives = [it for it in items if sort_val(it) is not None and sort_val(it) < 0]
    top_inflow = positives[:top_n]
    top_outflow = list(reversed(negatives[-top_n:])) if negatives else []
    return RankingResult(top_inflow=top_inflow, top_outflow=top_outflow, **result_kw)


class QueryService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        retention_trade_days: int = 7,
        source_id: str = "eastmoney:push2delay",
        clock: Clock | None = None,
    ) -> None:
        self._conn = conn
        self._subject = SubjectDao(conn)
        self._obs = ObservationDao(conn)
        self._constituent = ConstituentDao(conn)
        self._registry = CapabilityRegistry(conn)
        self._cal = TradingCalendar(conn)
        self._retention = retention_trade_days
        self._source_id = source_id  # 真实来源标识（provenance 不谎报）
        self._clock = clock or SystemClock()  # "今天"取真实自然日，非 MAX(calendar)（避免未来日）

    # ---- 通用板块排行（subject×observation）----

    def get_sector_ranking(
        self, *, caliber: Caliber, sector_type: SectorType,
        sort_by: SortField = SortField.MAIN_NET, top_n: int = 10, full: bool = False,
        trade_date: str | None = None, as_of: str | None = None,
        level: SubjectLevel = SubjectLevel.SECTOR,
    ) -> RankingResult:
        """通用模型上的板块排行。双榜 mode、tiebreaker、五档与 get_ranking 逐字段相等。"""
        subject_kind = _SECTOR_TYPE_TO_KIND[sector_type]
        asset_class = AssetClass.A_SHARE
        # 能力隔离：板块拒非法排序字段（按 asset_class,subject_kind）
        if not self._registry.is_sort_allowed(
            asset_class=asset_class, subject_kind=subject_kind, sort_by=sort_by
        ):
            raise UnsupportedMetric(
                f"板块 {subject_kind.value} 不支持排序字段 {sort_by.value}",
                detail={"available": [f.value for f in self._registry.available_sort_fields(
                    asset_class=asset_class, subject_kind=subject_kind)]},
            )

        td = trade_date or self._resolve_obs_trade_date(asset_class, level)
        if td is None:
            raise NoDataForDate("无任何已采集观测", detail={"asset_class": asset_class.value})

        # 该品种该层该细类的全部 subject_id（先缩集再进 observation，不跨全表扫）
        subject_ids = [
            s["subject_id"] for s in self._subject.list_by(
                asset_class=asset_class, level=level, subject_kind=subject_kind)
        ]
        today = (as_of or self._cal.latest_trading_day(td) or td)
        value_type = ValueType.DAILY_FINAL if td < today else ValueType.INTRADAY_SNAPSHOT
        # slot 必须限定在板块主体集内（多 target 共用 source，slot 不同，防串味查空）
        slot = self._obs.latest_slot_for_subjects(
            subject_ids=subject_ids, trade_date=td, value_type=value_type)
        if slot is None:
            value_type = (ValueType.INTRADAY_SNAPSHOT
                          if value_type is ValueType.DAILY_FINAL else ValueType.DAILY_FINAL)
            slot = self._obs.latest_slot_for_subjects(
                subject_ids=subject_ids, trade_date=td, value_type=value_type)
        if slot is None:
            raise NoDataForDate(f"{td} 无板块观测", detail={"trade_date": td})

        # 双榜须拿全部板块再按符号切分——top_n 设为全集大小（board ~991，可全量物化）
        rows = self._obs.ranking_snapshot(
            subject_ids=subject_ids, trade_date=td, value_type=value_type,
            minute_slot=slot, sort_by=sort_by, top_n=len(subject_ids) or 1, descending=True,
        )
        items = [_obs_to_item(r) for r in rows]
        has_five_tier = self._registry.has_five_tier(
            asset_class=asset_class, subject_kind=subject_kind)
        mode = self._registry.ranking_mode(sort_by)

        prov = DataProvenance(
            trade_date=td,
            source_type=SourceType(value_type.value),
            source_id=self._source_id,          # 真实来源，不谎报
            captured_at=slot,
            is_stale=td < (as_of or self._cal.latest_trading_day(self._cal_today()) or td),
        )
        return _assemble_ranking(
            items, mode=mode, sort_by=sort_by, asset_class=asset_class, level=level,
            subject_kind=subject_kind, prov=prov, has_five_tier=has_five_tier,
            full=full, top_n=top_n,
        )

    def get_stocks_in_sector(
        self, *, sector_subject_id: int, sort_by: SortField = SortField.MAIN_NET,
        top_n: int = 20, full: bool = False, trade_date: str | None = None,
        as_of: str | None = None, caliber: Caliber = Caliber.EASTMONEY,
    ) -> RankingResult:
        """板块内个股下钻排行。as_of<today 且无成分记录→OutOfWindow（无前视，不静默用当前成分）。"""
        sector = self._subject.get(sector_subject_id)
        if sector is None or sector["level"] != SubjectLevel.SECTOR.value:
            raise SubjectNotFound(
                f"板块主体 {sector_subject_id} 不存在", detail={"subject_id": sector_subject_id})
        asset_class = AssetClass.A_SHARE
        stock_kind = SubjectKind.STOCK
        # 能力隔离：个股支持五档+价量排序（按 stock 主体隔离）
        if not self._registry.is_sort_allowed(
            asset_class=asset_class, subject_kind=stock_kind, sort_by=sort_by
        ):
            raise UnsupportedMetric(
                f"个股不支持排序字段 {sort_by.value}",
                detail={"available": [f.value for f in self._registry.available_sort_fields(
                    asset_class=asset_class, subject_kind=stock_kind)]},
            )

        # 目标日 + 无前视 as-of 成分解析
        td = trade_date or self._resolve_obs_trade_date(asset_class, SubjectLevel.INSTRUMENT)
        if td is None:
            raise NoDataForDate("无任何已采集个股观测", detail={"sector_id": sector_subject_id})
        today = self._cal.latest_trading_day(self._cal_today()) or td
        as_of_date = as_of or td
        member_ids = self._constituent.members_asof(
            parent_subject_id=sector_subject_id, as_of=as_of_date)
        if not member_ids:
            # 历史 as_of 无成分 → OutOfWindow（不静默回退当前成分，宪章 II 无前视）
            if as_of is not None and as_of < today:
                raise OutOfWindow(
                    f"板块 {sector['display_name']} 在 {as_of} 无成分记录",
                    detail={"as_of": as_of, "sector_id": sector_subject_id})
            raise NoDataForDate(
                f"板块 {sector['display_name']} 无成分数据（尚未采集成员）",
                detail={"sector_id": sector_subject_id})

        # 成员观测时点：当日 intraday_latest/LATEST；历史取 daily_final/EOD
        is_history = as_of is not None and as_of < today
        if is_history:
            value_type, slot = ValueType.DAILY_FINAL, "EOD"
        else:
            value_type, slot = ValueType.INTRADAY_LATEST, "LATEST"
        rows = self._obs.ranking_snapshot(
            subject_ids=member_ids, trade_date=td, value_type=value_type,
            minute_slot=slot, sort_by=sort_by, top_n=len(member_ids), descending=True,
        )
        items = [_obs_to_item(r) for r in rows]
        has_five_tier = self._registry.has_five_tier(
            asset_class=asset_class, subject_kind=stock_kind)
        mode = self._registry.ranking_mode(sort_by)
        prov = DataProvenance(
            trade_date=td, source_type=SourceType(value_type.value),
            source_id=self._source_id, captured_at=slot,
            is_stale=td < today,
        )
        return _assemble_ranking(
            items, mode=mode, sort_by=sort_by, asset_class=asset_class,
            level=SubjectLevel.INSTRUMENT, subject_kind=stock_kind, prov=prov,
            has_five_tier=has_five_tier, full=full, top_n=top_n,
        )

    def get_etf_ranking(
        self, *, sort_by: SortField = SortField.CHANGE_PCT, top_n: int = 20,
        full: bool = False, trade_date: str | None = None,
        caliber: Caliber = Caliber.EASTMONEY,
    ) -> RankingResult:
        """ETF 排行（单榜 unipolar，无资金流双榜）。五档排序→UnsupportedMetric。"""
        asset_class, kind = AssetClass.FUND_ETF, SubjectKind.ETF
        if not self._registry.is_sort_allowed(
            asset_class=asset_class, subject_kind=kind, sort_by=sort_by
        ):
            raise UnsupportedMetric(
                f"ETF 不支持排序字段 {sort_by.value}",
                detail={"available": [f.value for f in self._registry.available_sort_fields(
                    asset_class=asset_class, subject_kind=kind)]},
            )
        td = trade_date or self._resolve_obs_trade_date(asset_class, SubjectLevel.INSTRUMENT)
        if td is None:
            raise NoDataForDate("无任何已采集 ETF 观测", detail={"asset_class": asset_class.value})
        value_type = ValueType.INTRADAY_SNAPSHOT
        subject_ids = [
            s["subject_id"] for s in self._subject.list_by(
                asset_class=asset_class, level=SubjectLevel.INSTRUMENT, subject_kind=kind)
        ]
        slot = self._obs.latest_slot_for_subjects(
            subject_ids=subject_ids, trade_date=td, value_type=value_type)
        if slot is None:
            raise NoDataForDate(f"{td} 无 ETF 观测", detail={"trade_date": td})
        rows = self._obs.ranking_snapshot(
            subject_ids=subject_ids, trade_date=td, value_type=value_type,
            minute_slot=slot, sort_by=sort_by, top_n=top_n, descending=True,
        )
        items = [_obs_to_item(r) for r in rows]
        prov = DataProvenance(
            trade_date=td, source_type=SourceType(value_type.value),
            source_id=self._source_id, captured_at=slot,
            is_stale=td < (self._cal.latest_trading_day(self._cal_today()) or td),
        )
        # ETF 恒为单榜（无 inflow/outflow 概念），mode 强制 unipolar
        return _assemble_ranking(
            items, mode="unipolar", sort_by=sort_by, asset_class=asset_class,
            level=SubjectLevel.INSTRUMENT, subject_kind=kind, prov=prov,
            has_five_tier=False, full=full, top_n=top_n,
        )

    def get_capability(
        self, *, asset_class: AssetClass, subject_kind: SubjectKind,
        caliber: Caliber = Caliber.EASTMONEY,
    ) -> SubjectCapabilityView:
        """主体×指标能力自描述（FR-004，人/agent 问能力不问身份）。"""
        return SubjectCapabilityView(
            asset_class=asset_class, subject_kind=subject_kind,
            has_five_tier=self._registry.has_five_tier(
                asset_class=asset_class, subject_kind=subject_kind),
            supported_metrics=self._registry.supported_metrics(
                asset_class=asset_class, subject_kind=subject_kind),
            available_sort_fields=self._registry.available_sort_fields(
                asset_class=asset_class, subject_kind=subject_kind),
            series_granularity=_SERIES_GRANULARITY.get(subject_kind, "5min"),
        )

    def get_subjects(
        self, *, asset_class: AssetClass, level: SubjectLevel | None = None,
        subject_kind: SubjectKind | None = None,
    ) -> list[SubjectRef]:
        """主体清单（下钻寻址）。跨品种不混比——按 asset_class 过滤。"""
        rows = self._subject.list_by(
            asset_class=asset_class, level=level, subject_kind=subject_kind)
        return [
            SubjectRef(
                subject_id=r["subject_id"], source_symbol=r["source_symbol"],
                display_name=r["display_name"], asset_class=AssetClass(r["asset_class_code"]),
                level=SubjectLevel(r["level"]), subject_kind=SubjectKind(r["subject_kind"]),
            )
            for r in rows
        ]

    def get_subject_series(
        self, *, subject_id: int, metric: SortField = SortField.MAIN_NET,
        trade_date: str | None = None, granularity: str = "intraday",
    ) -> SubjectSeriesResult:
        """单主体单指标时序回看。

        granularity='daily'：跨交易日的日线历史（daily_final，回填数据），ts=trade_date。
        granularity='intraday'（默认）：某交易日的分钟/小时序列（排除 LATEST，超窗 OutOfWindow）。
        """
        subject = self._subject.get(subject_id)
        if subject is None:
            raise SubjectNotFound(
                f"主体 {subject_id} 不存在", detail={"subject_id": subject_id})

        if granularity == "daily":
            return self._daily_series(subject_id, subject, metric)

        td = trade_date or self._subject_latest_series_date(subject_id)
        if td is None:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无时序数据", detail={"subject_id": subject_id})
        # 保留窗门禁：超 retention_trade_days 天 → OutOfWindow（数据已清理，宪章 II 不造假）
        today = self._cal.latest_trading_day(self._cal_today()) or td
        if _date_diff_days(today, td) > self._retention:
            raise OutOfWindow(
                f"{td} 超出 {self._retention} 天保留窗",
                detail={"trade_date": td, "retention_days": self._retention})

        rows = self._obs.series(subject_id=subject_id, trade_date=td)
        points: list[SeriesPoint] = []
        gap_count = 0
        granularities: set[str] = set()
        for r in rows:
            value = _metric_value(r, metric)
            if value is None:
                gap_count += 1
            granularities.add(r.granularity)
            points.append(SeriesPoint(
                ts=r.minute_slot, value=value, granularity=r.granularity,
                source_type=SourceType(r.value_type)))
        # 主粒度：有小时点即 hourly（降采后），否则取首点粒度
        series_granularity = ("hourly" if "hourly" in granularities
                              else (next(iter(granularities)) if granularities else "1min"))
        prov = DataProvenance(
            trade_date=td, source_type=SourceType(rows[0].value_type if rows else "intraday_snapshot"),
            source_id=self._source_id, captured_at=td,
            is_stale=td < today,
        )
        return SubjectSeriesResult(
            subject_id=subject_id, source_symbol=subject["source_symbol"],
            display_name=subject["display_name"], metric=metric.value, trade_date=td,
            granularity=series_granularity, provenance=prov, points=points, gap_count=gap_count,
        )

    def _subject_latest_series_date(self, subject_id: int) -> str | None:
        row = self._conn.execute(
            """SELECT MAX(trade_date) FROM observation
               WHERE subject_id=? AND value_type!='intraday_latest'""",
            (subject_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def _daily_series(self, subject_id, subject, metric: SortField) -> SubjectSeriesResult:
        """跨交易日日线历史（daily_final 回填）。ts=trade_date，一天一点。"""
        rows = self._obs.series_daily(subject_id=subject_id, days=self._retention)
        if not rows:
            raise NoDataForDate(
                f"主体 {subject['display_name']} 无日线历史（需回填 quantchive-collect --target backfill）",
                detail={"subject_id": subject_id})
        points: list[SeriesPoint] = []
        gap_count = 0
        for r in rows:
            value = _metric_value(r, metric)
            if value is None:
                gap_count += 1
            points.append(SeriesPoint(
                ts=r.trade_date, value=value, granularity="daily",
                source_type=SourceType(r.value_type)))
        last_td = rows[-1].trade_date
        today = self._cal.latest_trading_day(self._cal_today()) or last_td
        prov = DataProvenance(
            trade_date=last_td, source_type=SourceType("daily_final"),
            source_id=self._source_id, captured_at=last_td, is_stale=last_td < today)
        return SubjectSeriesResult(
            subject_id=subject_id, source_symbol=subject["source_symbol"],
            display_name=subject["display_name"], metric=metric.value, trade_date=last_td,
            granularity="daily", provenance=prov, points=points, gap_count=gap_count)

    def get_market_overview(
        self, *, asset_class: AssetClass = AssetClass.A_SHARE,
        trade_date: str | None = None, as_of: str | None = None,
    ) -> MarketOverviewResult:
        """大盘资金全景（读采集层求和的 is_derived 大盘点）。"""
        market_id = self._subject.find_id(
            asset_class=asset_class, level=SubjectLevel.MARKET, source_symbol="__MARKET__")
        if market_id is None:
            raise NoDataForDate(
                self._market_gate_reason(), detail={"asset_class": asset_class.value})
        row = self._obs.latest_market_point(
            market_subject_id=market_id, trade_date=trade_date)
        if row is None:
            raise NoDataForDate(
                self._market_gate_reason(), detail={"asset_class": asset_class.value})

        coverage_pct = None
        if row.constituent_count is not None and row.expected_count:
            coverage_pct = (Decimal(row.constituent_count) / Decimal(row.expected_count)
                            * Decimal(100)).quantize(Decimal("0.01"))
        prov = DataProvenance(
            trade_date=row.trade_date,
            source_type=SourceType(row.value_type),
            source_id=self._source_id, captured_at=row.minute_slot,
            is_stale=row.trade_date < (
                as_of or self._cal.latest_trading_day(self._cal_today()) or row.trade_date),
        )
        return MarketOverviewResult(
            asset_class=asset_class, trade_date=row.trade_date, provenance=prov,
            main_net=_dec(row.main_net_cents),
            super_large_net=_dec(row.super_large_net_cents),
            large_net=_dec(row.large_net_cents), medium_net=_dec(row.medium_net_cents),
            small_net=_dec(row.small_net_cents),
            constituent_count=row.constituent_count, expected_count=row.expected_count,
            coverage_pct=coverage_pct, is_derived=True,
        )

    def _resolve_obs_trade_date(
        self, asset_class: AssetClass, level: SubjectLevel
    ) -> str | None:
        row = self._conn.execute(
            """SELECT MAX(o.trade_date) FROM observation o
               JOIN subject s ON s.subject_id=o.subject_id
               WHERE s.asset_class_code=? AND s.level=?""",
            (asset_class.value, level.value),
        ).fetchone()
        return row[0] if row and row[0] else None

    def _market_gate_reason(self) -> str:
        """读最近一次个股采集的覆盖率审计，给出大盘缺失的**真实原因**（非裸 404）。"""
        import json
        row = self._conn.execute(
            """SELECT aggregate_coverage FROM ingestion_run
               WHERE sector_scope='all_stocks' AND aggregate_coverage IS NOT NULL
               ORDER BY run_id DESC LIMIT 1""").fetchone()
        if not row or not row[0]:
            return "大盘尚无数据——需先采集个股（quantchive-collect --target stock）"
        try:
            cov = json.loads(row[0])
        except (ValueError, TypeError):
            return "大盘尚无求和数据"
        if cov.get("written"):
            return "大盘尚无数据"
        pct = round(float(cov.get("coverage", 0)) * 100, 2)
        return (f"覆盖率 {pct}%（{cov.get('constituent')}/{cov.get('expected')}）< 门禁阈值，"
                f"大盘未落（宁缺勿假；停牌股多的交易日可下调 market_coverage_threshold）")

    def _cal_today(self) -> str:
        """真实自然日（Asia/Shanghai）。绝不取 MAX(trade_calendar)——日历含全年未来交易日，
        会把"今天"派生成未来日期，污染保留窗判定与 as_of 历史/当日分类（宪章 II 前视）。"""
        return self._clock.now().astimezone(SHANGHAI_TZ).date().isoformat()

    def health(self) -> HealthResult:
        """运维健康：最新观测交易日 + 最近一次采集运行 + 各源采集状态。"""
        latest = self._conn.execute(
            "SELECT MAX(trade_date) FROM observation").fetchone()
        latest_td = latest[0] if latest and latest[0] else None
        run = self._conn.execute(
            """SELECT source_code, run_type, status, trade_date, minute_slot,
                      subjects_ok, subjects_failed, finished_at
               FROM ingestion_run ORDER BY run_id DESC LIMIT 1""").fetchone()
        last_run = dict(run) if run else None
        # per-source 最近一次成功采集时间
        per_source = {
            r["source_code"]: r["last_ok"]
            for r in self._conn.execute(
                """SELECT source_code, MAX(finished_at) AS last_ok FROM ingestion_run
                   WHERE status='success' GROUP BY source_code""").fetchall()
        }
        status = "ok" if latest_td is not None else "no_data"
        return HealthResult(
            status=status, latest_trade_date=latest_td,
            last_run={"latest": last_run, "per_source_last_ok": per_source},
        )

