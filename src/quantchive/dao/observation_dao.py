"""observation DAO（spec002 D2/D4，核心，泛化 flow_dao）。

- 幂等写入：INSERT ... ON CONFLICT(uq_observation) DO UPDATE。资金流五档 + 价/量固定列。
- ranking_snapshot：下推 ORDER BY..LIMIT top_n（5535 股禁全量物化，critique 裁定）。
- series：排除 intraday_latest；过滤 minute_slot='LATEST'（防 'LATEST' 混入按字符串
  排序产生假点，critique 裁定）。
- 稀疏指标（circ_mktcap/nav）走 observation_metric 附表。
- 金额全整数标度；无 float。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from quantchive.models.enums import SortField, ValueType

# SortField → observation 固定列。价/量为 spec002 unipolar 排序（ETF/个股）。
_SORT_COLUMN = {
    SortField.MAIN_NET: "main_net_cents",
    SortField.NET_AMOUNT: "main_net_cents",     # 东财 net_amount==main_net，别名同列
    SortField.SUPER_LARGE_NET: "super_large_net_cents",
    SortField.LARGE_NET: "large_net_cents",
    SortField.MEDIUM_NET: "medium_net_cents",
    SortField.SMALL_NET: "small_net_cents",
    SortField.CHANGE_PCT: "change_pct_bp",
    SortField.PRICE: "price_micro",
    SortField.VOLUME: "volume",
}

_OBS_COLS = (
    "o.observation_id, o.subject_id, s.source_symbol, s.display_name, o.source_code, "
    "o.trade_date, o.minute_slot, o.value_type, o.granularity, o.observed_at, "
    "o.net_amount_cents, o.main_net_cents, o.super_large_net_cents, o.large_net_cents, "
    "o.medium_net_cents, o.small_net_cents, "
    "o.super_large_gross_cents, o.large_gross_cents, o.medium_gross_cents, o.small_gross_cents, "
    "o.price_micro, o.change_pct_bp, o.volume, "
    "o.turnover_cents, o.turnover_pct_bp, o.constituent_count, o.expected_count, o.is_derived"
)


@dataclass(frozen=True)
class ObservationRow:
    observation_id: int
    subject_id: int
    source_symbol: str
    display_name: str
    source_code: str
    trade_date: str
    minute_slot: str
    value_type: str
    granularity: str
    observed_at: str
    net_amount_cents: int | None
    main_net_cents: int | None
    super_large_net_cents: int | None
    large_net_cents: int | None
    medium_net_cents: int | None
    small_net_cents: int | None
    super_large_gross_cents: int | None
    large_gross_cents: int | None
    medium_gross_cents: int | None
    small_gross_cents: int | None
    price_micro: int | None
    change_pct_bp: int | None
    volume: int | None
    turnover_cents: int | None
    turnover_pct_bp: int | None
    constituent_count: int | None = None
    expected_count: int | None = None
    is_derived: int = 0


class ObservationDao:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(
        self, *, subject_id: int, source_code: str, trade_date: str, minute_slot: str,
        value_type: ValueType, granularity: str, observed_at: str,
        net_amount_cents: int | None = None, five_tier: dict[str, int] | None = None,
        four_gross: dict[str, int] | None = None,
        price_micro: int | None = None, change_pct_bp: int | None = None,
        volume: int | None = None, turnover_cents: int | None = None,
        turnover_pct_bp: int | None = None, inflow_cents: int | None = None,
        outflow_cents: int | None = None, constituent_count: int | None = None,
        expected_count: int | None = None, source_unit: str, raw_value: str | None = None,
        is_derived: bool = False, ingestion_run_id: int, created_at: str,
    ) -> int:
        """幂等写一条观测。返回 observation_id。键 (subject,source,date,value_type,slot)。

        four_gross（spec005）：四档成交额 {super_large/large/medium/small_gross_cents}，
        新浪行全有、无gross源不传（全NULL）。
        """
        ft = five_tier or {}
        fg = four_gross or {}
        self._conn.execute(
            """INSERT INTO observation
               (subject_id, source_code, trade_date, minute_slot, value_type, granularity,
                observed_at, net_amount_cents, main_net_cents, super_large_net_cents,
                large_net_cents, medium_net_cents, small_net_cents, inflow_cents, outflow_cents,
                super_large_gross_cents, large_gross_cents, medium_gross_cents, small_gross_cents,
                price_micro, change_pct_bp, volume, turnover_cents, turnover_pct_bp,
                constituent_count, expected_count, source_unit, raw_value, is_derived,
                ingestion_run_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(subject_id, source_code, trade_date, value_type, minute_slot) DO UPDATE SET
                 net_amount_cents=excluded.net_amount_cents,
                 main_net_cents=excluded.main_net_cents,
                 super_large_net_cents=excluded.super_large_net_cents,
                 large_net_cents=excluded.large_net_cents,
                 medium_net_cents=excluded.medium_net_cents,
                 small_net_cents=excluded.small_net_cents,
                 super_large_gross_cents=excluded.super_large_gross_cents,
                 large_gross_cents=excluded.large_gross_cents,
                 medium_gross_cents=excluded.medium_gross_cents,
                 small_gross_cents=excluded.small_gross_cents,
                 inflow_cents=excluded.inflow_cents, outflow_cents=excluded.outflow_cents,
                 price_micro=excluded.price_micro, change_pct_bp=excluded.change_pct_bp,
                 volume=excluded.volume, turnover_cents=excluded.turnover_cents,
                 turnover_pct_bp=excluded.turnover_pct_bp,
                 constituent_count=excluded.constituent_count,
                 expected_count=excluded.expected_count,
                 observed_at=excluded.observed_at, is_derived=excluded.is_derived,
                 ingestion_run_id=excluded.ingestion_run_id""",
            (subject_id, source_code, trade_date, minute_slot, value_type.value, granularity,
             observed_at, net_amount_cents, ft.get("main_net_cents"),
             ft.get("super_large_net_cents"), ft.get("large_net_cents"),
             ft.get("medium_net_cents"), ft.get("small_net_cents"), inflow_cents, outflow_cents,
             fg.get("super_large_gross_cents"), fg.get("large_gross_cents"),
             fg.get("medium_gross_cents"), fg.get("small_gross_cents"),
             price_micro, change_pct_bp, volume, turnover_cents, turnover_pct_bp,
             constituent_count, expected_count, source_unit, raw_value, 1 if is_derived else 0,
             ingestion_run_id, created_at),
        )
        # cur.lastrowid 在 ON CONFLICT DO UPDATE 路径不更新（保留上一次 INSERT 的连接级
        # 陈旧值），据其判 INSERT/UPDATE 会返回错误 observation_id（跨主体污染）。
        # 无条件按幂等业务键回查，版本无关、恒正确。业务键含 source_code（多源共存）。
        row = self._conn.execute(
            """SELECT observation_id FROM observation
               WHERE subject_id=? AND source_code=? AND trade_date=? AND value_type=? AND minute_slot=?""",
            (subject_id, source_code, trade_date, value_type.value, minute_slot),
        ).fetchone()
        return int(row[0])

    def upsert_metric(self, *, observation_id: int, metric_name: str, value_int: int) -> None:
        """稀疏指标写附表（circ_mktcap/nav 等，按 metric_def.scale_factor 标度整数）。"""
        self._conn.execute(
            """INSERT INTO observation_metric (observation_id, metric_name, value_int)
               VALUES (?,?,?)
               ON CONFLICT(observation_id, metric_name) DO UPDATE SET value_int=excluded.value_int""",
            (observation_id, metric_name, value_int),
        )

    def latest_slot(
        self, *, trade_date: str, source_code: str, value_type: ValueType,
    ) -> str | None:
        """当日某源已采到的最新真实 minute_slot（排除 'LATEST' 覆盖标记）。"""
        row = self._conn.execute(
            """SELECT MAX(minute_slot) FROM observation
               WHERE trade_date=? AND source_code=? AND value_type=? AND minute_slot!='LATEST'""",
            (trade_date, source_code, value_type.value),
        ).fetchone()
        return row[0] if row and row[0] else None

    def latest_slot_for_subjects(
        self, *, subject_ids: list[int], trade_date: str, value_type: ValueType,
        source_code: str | None = None,
    ) -> str | None:
        """给定 subject 集内的最新真实 minute_slot。

        必须按 subject 集限定——多 target 共用 source_code='eastmoney' 且各自 batch_slot
        不同（板块 19:47、大盘求和 19:53…），若只按 source 取 MAX 会串味：板块排行会
        错用大盘的 slot 而查空。故排行的 slot 解析必须限定在被排主体集内。
        source_code（spec004）：多源共存后限定实时源，防跨源 slot 串味。
        """
        if not subject_ids:
            return None
        placeholders = ",".join("?" * len(subject_ids))
        src_clause = " AND source_code=?" if source_code else ""
        params = [*subject_ids, trade_date, value_type.value]
        if source_code:
            params.append(source_code)
        row = self._conn.execute(
            f"""SELECT MAX(minute_slot) FROM observation
               WHERE subject_id IN ({placeholders})
                 AND trade_date=? AND value_type=? AND minute_slot!='LATEST'{src_clause}""",
            tuple(params),
        ).fetchone()
        return row[0] if row and row[0] else None

    def ranking_snapshot(
        self, *, subject_ids: list[int], trade_date: str, value_type: ValueType,
        minute_slot: str, sort_by: SortField, top_n: int, descending: bool = True,
        source_code: str | None = None,
    ) -> list[ObservationRow]:
        """取给定 subject 集在某时点的观测，按 sort_by 排序，DB 层 LIMIT top_n。

        subject 集由 Service 先经 subject/membership 缩小（避免跨 8048 主体全表扫，
        critique 裁定）。ORDER BY..LIMIT 下推——5535 股不全量物化。
        source_code（spec004）：多源共存后限定源，防一股多源行重复排入榜。
        """
        if not subject_ids:
            return []
        col = _SORT_COLUMN[sort_by]
        direction = "DESC" if descending else "ASC"
        placeholders = ",".join("?" * len(subject_ids))
        src_clause = " AND o.source_code=?" if source_code else ""
        params = [*subject_ids, trade_date, value_type.value, minute_slot]
        if source_code:
            params.append(source_code)
        params.append(top_n)
        rows = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.subject_id IN ({placeholders})
                 AND o.trade_date=? AND o.value_type=? AND o.minute_slot=?{src_clause}
                 AND o.{col} IS NOT NULL
               ORDER BY o.{col} {direction}, o.subject_id ASC
               LIMIT ?""",
            tuple(params),
        ).fetchall()
        return [ObservationRow(*r) for r in rows]

    def series(
        self, *, subject_id: int, trade_date: str, source_code: str | None = None,
    ) -> list[ObservationRow]:
        """单主体某日分钟序列，升序。排除 intraday_latest 覆盖行、过滤 'LATEST' 槽

        （防 'LATEST' 字符串混入按 minute_slot 排序产生假点，critique 裁定）。
        source_code（spec004）：多源共存后限定源，防一 slot 多源行重复成点。
        """
        src_clause = " AND o.source_code=?" if source_code else ""
        params = [subject_id, trade_date]
        if source_code:
            params.append(source_code)
        rows = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.subject_id=? AND o.trade_date=?
                 AND o.value_type!='intraday_latest' AND o.minute_slot!='LATEST'{src_clause}
               ORDER BY o.minute_slot ASC""",
            tuple(params),
        ).fetchall()
        return [ObservationRow(*r) for r in rows]

    def series_daily(
        self, *, subject_id: int, days: int = 30,
        sources: list[str] | None = None, nonnull_column: str | None = None,
    ) -> list[ObservationRow]:
        """单主体跨交易日的日线序列（daily_final），按 trade_date 升序。历史回填读此。

        取最近 days 个交易日的 EOD 日终点——盘中快照攒不出的历史深度由日线回填提供。
        多源解析（spec004）：sources 给定源优先级 → 逐 trade_date 取首个「该指标列非空」的
        权威源行（窗口函数 ROW_NUMBER 按源优先级排序）。sources=None 则不限源（兼容旧行为）。
        """
        if not sources:
            rows = self._conn.execute(
                f"""SELECT * FROM (
                       SELECT {_OBS_COLS}
                       FROM observation o JOIN subject s ON s.subject_id = o.subject_id
                       WHERE o.subject_id=? AND o.value_type='daily_final'
                       ORDER BY o.trade_date DESC LIMIT ?
                   ) ORDER BY trade_date ASC""",
                (subject_id, days),
            ).fetchall()
            return [ObservationRow(*r) for r in rows]

        placeholders = ",".join("?" * len(sources))
        # 源优先级 CASE：优先源 rank 小。非空列参与排序（该指标有值的源排前）
        case_rank = " ".join(
            f"WHEN o.source_code='{sc}' THEN {i}" for i, sc in enumerate(sources))
        col = nonnull_column or "main_net_cents"
        rows = self._conn.execute(
            f"""SELECT * FROM (
                   SELECT {_OBS_COLS},
                          ROW_NUMBER() OVER (
                            PARTITION BY o.trade_date
                            ORDER BY (CASE WHEN o.{col} IS NULL THEN 1 ELSE 0 END),
                                     (CASE {case_rank} ELSE 999 END)) AS _rn
                   FROM observation o JOIN subject s ON s.subject_id = o.subject_id
                   WHERE o.subject_id=? AND o.value_type='daily_final'
                     AND o.source_code IN ({placeholders})
               ) WHERE _rn=1
               ORDER BY trade_date DESC LIMIT ?""",
            (subject_id, *sources, days),
        ).fetchall()
        # 去掉尾部 _rn 列，升序返回
        ordered = sorted((ObservationRow(*r[:-1]) for r in rows), key=lambda x: x.trade_date)
        return ordered

    def tiers_rows_daily(
        self, *, subject_id: int, days: int, sources: list[str],
    ) -> list["ObservationRow"]:
        """资金档位日线：逐日取资金流权威源行（含四档 net+gross）。复用 series_daily 源解析。"""
        return self.series_daily(subject_id=subject_id, days=days, sources=sources,
                                 nonnull_column="main_net_cents")

    def tiers_rows_intraday(self, *, subject_id: int) -> list["ObservationRow"]:
        """资金档位盘中：当日分钟净额序列（东财，无 gross），按 minute_slot 升序。"""
        rows = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.subject_id=? AND o.value_type='intraday_snapshot'
                 AND o.minute_slot!='LATEST'
               ORDER BY o.trade_date DESC, o.minute_slot ASC""",
            (subject_id,),
        ).fetchall()
        return [ObservationRow(*r) for r in rows]

    def earliest_trade_date(self) -> str | None:
        row = self._conn.execute("SELECT MIN(trade_date) FROM observation").fetchone()
        return row[0] if row and row[0] else None

    def fine_grained_before(self, *, cutoff_date: str) -> list[ObservationRow]:
        """取 cutoff_date（含）及之前的分钟级 intraday_snapshot 行——降采候选（US5）。"""
        rows = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.trade_date<=? AND o.value_type='intraday_snapshot'
                 AND o.granularity IN ('1min','5min')
               ORDER BY o.subject_id, o.trade_date, o.minute_slot""",
            (cutoff_date,),
        ).fetchall()
        return [ObservationRow(*r) for r in rows]

    def delete_ids(self, ids: list[int]) -> int:
        """按主键批量删（CASCADE 带走 observation_metric）。返回删除行数。

        分块 ≤900/批：单条 IN (N 占位符) 的 N 上限受 SQLITE_MAX_VARIABLE_NUMBER 限制
        （旧版 999），降采单日候选可达数万行，不分块必抛 'too many SQL variables'。
        """
        if not ids:
            return 0
        total = 0
        chunk = 900
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            placeholders = ",".join("?" * len(batch))
            cur = self._conn.execute(
                f"DELETE FROM observation WHERE observation_id IN ({placeholders})", batch)
            total += cur.rowcount
        return total

    def delete_before_date(self, *, trade_date_exclusive: str) -> int:
        """删 trade_date < 界（含 CASCADE）。保留清理用。返回删除行数。"""
        cur = self._conn.execute(
            "DELETE FROM observation WHERE trade_date < ?", (trade_date_exclusive,))
        return cur.rowcount

    def stock_rows_for(
        self, *, trade_date: str, value_type: ValueType, minute_slot: str,
        source_code: str | None = None,
    ) -> list[ObservationRow]:
        """取某时点全部个股观测（level='instrument' AND kind='stock'）——大盘求和用。

        source_code（spec004）：多源共存后限定源，防一股多源 LATEST 行被重复求和、
        component_hash 含重复符号。
        """
        src_clause = " AND o.source_code=?" if source_code else ""
        params = [trade_date, value_type.value, minute_slot]
        if source_code:
            params.append(source_code)
        rows = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.trade_date=? AND o.value_type=? AND o.minute_slot=?{src_clause}
                 AND s.level='instrument' AND s.subject_kind='stock'""",
            tuple(params),
        ).fetchall()
        return [ObservationRow(*r) for r in rows]

    def get_point(
        self, *, subject_id: int, trade_date: str, value_type: ValueType, minute_slot: str,
        source_code: str | None = None,
    ) -> ObservationRow | None:
        src_clause = " AND o.source_code=?" if source_code else ""
        params = [subject_id, trade_date, value_type.value, minute_slot]
        if source_code:
            params.append(source_code)
        row = self._conn.execute(
            f"""SELECT {_OBS_COLS}
               FROM observation o JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.subject_id=? AND o.trade_date=? AND o.value_type=? AND o.minute_slot=?{src_clause}""",
            tuple(params),
        ).fetchone()
        return ObservationRow(*row) if row else None

    def latest_market_point(
        self, *, market_subject_id: int, trade_date: str | None = None,
        source_code: str | None = None,
    ) -> ObservationRow | None:
        """大盘最新求和点（is_derived=1）。trade_date=None 取全期最新。

        source_code（spec004）：大盘 subject 本已按源建（market_subject_id 编码源），
        此参数为多源安全冗余，限定 derived 行的源。
        """
        src_clause = " AND o.source_code=?" if source_code else ""
        if trade_date is None:
            params = [market_subject_id] + ([source_code] if source_code else [])
            row = self._conn.execute(
                f"""SELECT {_OBS_COLS}
                   FROM observation o JOIN subject s ON s.subject_id = o.subject_id
                   WHERE o.subject_id=? AND o.is_derived=1{src_clause}
                   ORDER BY o.trade_date DESC, o.minute_slot DESC LIMIT 1""",
                tuple(params),
            ).fetchone()
        else:
            params = [market_subject_id, trade_date] + ([source_code] if source_code else [])
            row = self._conn.execute(
                f"""SELECT {_OBS_COLS}
                   FROM observation o JOIN subject s ON s.subject_id = o.subject_id
                   WHERE o.subject_id=? AND o.trade_date=? AND o.is_derived=1{src_clause}
                   ORDER BY o.minute_slot DESC LIMIT 1""",
                tuple(params),
            ).fetchone()
        return ObservationRow(*row) if row else None
