"""采集用例（contracts/ingestion.md D4/D6）——通用 subject×observation 模型。

- 全依赖注入，函数体零 fastapi/akshare 符号。
- 批次时间戳统一：本批所有主体共用一个 minute_slot（禁重取 clock.now()）。
- 断点=缺行；三态 status；achieved_interval 落审计。
- 金额换算按源单位（东财元/同花顺亿）→ 整数分。
- 板块/个股/ETF 采集 + 大盘求和 + 保留降采/清理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from quantchive.core.money import to_basis_points, to_cents, to_micro
from quantchive.core.trading_calendar import SHANGHAI_TZ, Clock, SystemClock
from quantchive.dao.constituent_dao import ConstituentDao
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.base import DataSourceError, ObservationSource
from quantchive.models.enums import (
    Caliber, RunStatus, RunType, SectorType, SubjectKind, ValueType,
)

if TYPE_CHECKING:
    from quantchive.service.market_aggregate import MarketAggregator


@dataclass(frozen=True)
class CollectRequest:
    caliber: Caliber
    source_code: str
    sector_types: tuple[SectorType, ...] = (SectorType.INDUSTRY, SectorType.CONCEPT)
    value_type: ValueType = ValueType.INTRADAY_SNAPSHOT
    target_interval_sec: int | None = None
    adapter_version: str = "unknown"
    code_version: str | None = None


@dataclass
class CollectResult:
    run_id: int
    caliber: Caliber
    trade_date: str
    batch_slot: str
    value_type: str
    status: RunStatus
    rows_written: int
    sectors_requested: int
    sectors_failed: int
    failures: list[dict] = field(default_factory=list)


def _slot(now: datetime, value_type: ValueType) -> str:
    if value_type is ValueType.DAILY_FINAL:
        return "EOD"
    local = now.astimezone(SHANGHAI_TZ)
    return f"{local.hour:02d}:{local.minute:02d}"


# value_type → run_type 显式映射（斩断 RunType(value_type.value) 跨枚举直转的脆弱性，
# critique 裁定：两枚举同值仅是巧合，未来任一漂移即错配）
_VALUE_TYPE_TO_RUN_TYPE = {
    ValueType.INTRADAY_SNAPSHOT: RunType.INTRADAY_SNAPSHOT,
    ValueType.INTRADAY_LATEST: RunType.INTRADAY_SNAPSHOT,
    ValueType.DAILY_FINAL: RunType.EOD_BACKFILL,
    ValueType.HOURLY_ROLLUP: RunType.RETENTION_DOWNSAMPLE,
}


def _status(ok: int, requested: int, failures: list) -> RunStatus:
    if not failures:
        return RunStatus.SUCCESS
    if ok == 0:
        return RunStatus.FAILED
    return RunStatus.PARTIAL


# ============================================================================
# 板块采集（subject×observation）
# ============================================================================

_SECTOR_TYPE_TO_KIND_ING = {
    SectorType.INDUSTRY: SubjectKind.INDUSTRY,
    SectorType.CONCEPT: SubjectKind.CONCEPT,
    SectorType.REGION: SubjectKind.REGION,
}
_EM_SECTOR_FS = {
    SectorType.INDUSTRY: "m:90 t:2",
    SectorType.CONCEPT: "m:90 t:3",
}


def collect_sector_observations_once(
    req: "CollectRequest",
    *,
    source: "ObservationSource",
    observation_dao: "ObservationDao",
    run_dao: RunDao,
    subject_dao: "SubjectDao",
    clock: Clock | None = None,
) -> CollectResult:
    """板块观测采集（写 subject + observation）。

    幂等键 (subject, trade_date, value_type, minute_slot)；本批统一 minute_slot；
    断点=缺行；三态 status。金额按源单位换算整数分（禁 float）。
    """
    from quantchive.datasource.base import FetchSpec
    from quantchive.models.enums import AmountUnit, AssetClass, SubjectLevel

    clock = clock or SystemClock()
    now = clock.now()
    trade_date = now.astimezone(SHANGHAI_TZ).date().isoformat()
    batch_slot = _slot(now, req.value_type)
    observed_at = datetime.now(timezone.utc).isoformat()
    granularity = "1min" if req.value_type is ValueType.INTRADAY_SNAPSHOT else "daily"

    run_id = run_dao.start(
        source_code=req.source_code, run_type=_VALUE_TYPE_TO_RUN_TYPE[req.value_type],
        caliber=req.caliber, trade_date=trade_date, minute_slot=batch_slot,
        adapter_version=req.adapter_version, target_interval_sec=req.target_interval_sec,
        code_version=req.code_version,
        subject_scope=",".join(t.value for t in req.sector_types),
        asset_class_code=AssetClass.A_SHARE.value,
    )

    rows_written = 0
    requested = 0
    failures: list[dict] = []
    unit = AmountUnit.YUAN if req.caliber is Caliber.EASTMONEY else AmountUnit.YI

    for st in req.sector_types:
        kind = _SECTOR_TYPE_TO_KIND_ING[st]
        spec = FetchSpec(
            fs=_EM_SECTOR_FS[st], level=SubjectLevel.SECTOR,
            asset_class=AssetClass.A_SHARE, subject_kind=kind, sector_type=st,
        )
        try:
            observations = source.fetch_observations(spec)
        except DataSourceError as exc:
            failures.append({"sector_type": st.value, "error_type": exc.error_type,
                             "detail": exc.detail})
            continue
        for o in observations:
            requested += 1
            try:
                subject_id, _ = subject_dao.upsert(
                    asset_class=o.asset_class, level=o.level, subject_kind=o.subject_kind,
                    source_code=req.source_code, source_symbol=o.source_symbol,
                    display_name=o.display_name, caliber=req.caliber.value,
                    em_board_code=o.em_board_code,
                )
                five_tier = None
                net_cents = None
                if o.main_net is not None:
                    five_tier = {
                        "main_net_cents": to_cents(o.main_net, unit),
                        "super_large_net_cents": to_cents(o.super_large_net, unit),
                        "large_net_cents": to_cents(o.large_net, unit),
                        "medium_net_cents": to_cents(o.medium_net, unit),
                        "small_net_cents": to_cents(o.small_net, unit),
                    }
                    net_cents = five_tier["main_net_cents"]  # 东财总净额=主力
                observation_dao.upsert(
                    subject_id=subject_id, source_code=req.source_code, trade_date=trade_date,
                    minute_slot=batch_slot, value_type=req.value_type, granularity=granularity,
                    observed_at=observed_at, net_amount_cents=net_cents, five_tier=five_tier,
                    source_unit=unit.value, ingestion_run_id=run_id, created_at=observed_at,
                )
                rows_written += 1
            except Exception as exc:  # 单主体失败不影响其他（断点=缺行）
                failures.append({"subject": o.source_symbol, "error": str(exc)})

    status = _status(rows_written, requested, failures)
    run_dao.finish(run_id, status=status, subjects_ok=rows_written,
                   subjects_failed=len(failures),
                   error_type=(failures[0].get("error_type") if failures else None),
                   error_detail={"failures": failures} if failures else None)

    return CollectResult(
        run_id=run_id, caliber=req.caliber, trade_date=trade_date, batch_slot=batch_slot,
        value_type=req.value_type.value, status=status, rows_written=rows_written,
        sectors_requested=requested, sectors_failed=len(failures), failures=failures,
    )


@dataclass
class StockCollectResult(CollectResult):
    """个股采集 + 大盘求和结果。aggregate 为 None 表示覆盖率不足未落大盘。"""

    aggregate: object | None = None


def collect_stock_observations_once(
    *,
    source: "ObservationSource",
    observation_dao: "ObservationDao",
    run_dao: RunDao,
    subject_dao: "SubjectDao",
    aggregator: "MarketAggregator | None" = None,
    source_code: str = "eastmoney",
    caliber: Caliber = Caliber.EASTMONEY,
    fs: str = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    adapter_version: str = "unknown",
    clock: Clock | None = None,
) -> "StockCollectResult":
    """全市场个股采集：写 intraday_latest(LATEST 覆盖) + 触发大盘求和（复用 batch_slot，D2/D4）。"""
    from quantchive.datasource.base import FetchSpec
    from quantchive.models.enums import AmountUnit, AssetClass, SubjectLevel

    clock = clock or SystemClock()
    now = clock.now()
    trade_date = now.astimezone(SHANGHAI_TZ).date().isoformat()
    batch_slot = _slot(now, ValueType.INTRADAY_SNAPSHOT)  # 大盘点时序用真实 HH:MM
    observed_at = datetime.now(timezone.utc).isoformat()
    unit = AmountUnit.YUAN if caliber is Caliber.EASTMONEY else AmountUnit.YI

    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.INTRADAY_SNAPSHOT, caliber=caliber,
        trade_date=trade_date, minute_slot="LATEST", adapter_version=adapter_version,
        subject_scope="all_stocks", asset_class_code=AssetClass.A_SHARE.value,
    )

    spec = FetchSpec(fs=fs, level=SubjectLevel.INSTRUMENT, asset_class=AssetClass.A_SHARE,
                     subject_kind=SubjectKind.STOCK)
    rows_written = 0
    requested = 0
    failures: list[dict] = []
    try:
        observations = source.fetch_observations(spec)
    except DataSourceError as exc:
        run_dao.finish(run_id, status=RunStatus.FAILED, subjects_ok=0, subjects_failed=1,
                       error_type=exc.error_type, error_detail={"detail": exc.detail})
        return StockCollectResult(
            run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot="LATEST",
            value_type=ValueType.INTRADAY_LATEST.value, status=RunStatus.FAILED,
            rows_written=0, sectors_requested=0, sectors_failed=1,
            failures=[{"error_type": exc.error_type}], aggregate=None)

    for o in observations:
        requested += 1
        try:
            subject_id, _ = subject_dao.upsert(
                asset_class=o.asset_class, level=o.level, subject_kind=o.subject_kind,
                source_code=source_code, source_symbol=o.source_symbol,
                display_name=o.display_name, caliber=caliber.value, exchange=o.exchange,
                collect_tier="coarse",
            )
            five_tier = None
            net_cents = None
            if o.main_net is not None:
                five_tier = {
                    "main_net_cents": to_cents(o.main_net, unit),
                    "super_large_net_cents": to_cents(o.super_large_net, unit),
                    "large_net_cents": to_cents(o.large_net, unit),
                    "medium_net_cents": to_cents(o.medium_net, unit),
                    "small_net_cents": to_cents(o.small_net, unit),
                }
                net_cents = five_tier["main_net_cents"]
            metric_cols = dict(
                net_amount_cents=net_cents, five_tier=five_tier,
                price_micro=(None if o.price is None else to_micro(o.price)),
                change_pct_bp=(None if o.change_pct is None else to_basis_points(o.change_pct)),
                volume=(None if o.volume is None else int(o.volume)),
                turnover_cents=(None if o.turnover is None else to_cents(o.turnover, unit)),
            )
            # ① LATEST 覆盖行（当日每股 1 行，供当前排行 + 大盘求和；D4 ①）
            observation_dao.upsert(
                subject_id=subject_id, source_code=source_code, trade_date=trade_date,
                minute_slot="LATEST", value_type=ValueType.INTRADAY_LATEST, granularity="5min",
                observed_at=observed_at, source_unit=unit.value, ingestion_run_id=run_id,
                created_at=observed_at, **metric_cols,
            )
            # ② 5min 稀疏历史点（真实 HH:MM，供个股趋势曲线；D4 ②，series 只读此类）
            observation_dao.upsert(
                subject_id=subject_id, source_code=source_code, trade_date=trade_date,
                minute_slot=batch_slot, value_type=ValueType.INTRADAY_SNAPSHOT, granularity="5min",
                observed_at=observed_at, source_unit=unit.value, ingestion_run_id=run_id,
                created_at=observed_at, **metric_cols,
            )
            rows_written += 1
        except Exception as exc:
            failures.append({"subject": o.source_symbol, "error": str(exc)})

    status = _status(rows_written, requested, failures)
    # expected 必须是东财报告的真实全集 total（覆盖率门禁的分母）。缺失/为 0 时**不能**
    # 回退到 requested（已取数），否则 coverage=fetched/fetched=100% 恒真、翻页截断也落假
    # 大盘（fail-open，违反 D2 宁缺勿假）。用 -1 哨兵表未知 → aggregate 覆盖率 0 → 不落。
    last_total = getattr(source, "last_total", 0)
    expected = last_total if last_total and last_total > 0 else -1

    # 大盘求和子步（复用 batch_slot，禁 clock.now()；D2 覆盖率门禁）
    agg = None
    if aggregator is not None and rows_written > 0:
        agg = aggregator.compute_and_write(
            trade_date=trade_date, batch_slot=batch_slot, observed_at=observed_at,
            expected_count=expected, source_code=source_code, ingestion_run_id=run_id,
            created_at=observed_at,
        )

    run_dao.finish(
        run_id, status=status, subjects_ok=rows_written, subjects_failed=len(failures),
        error_type=(failures[0].get("error") if failures else None),
        error_detail={"failures": failures} if failures else None,
        aggregate_coverage=(
            {"coverage": agg.coverage, "constituent": agg.constituent_count,
             "expected": agg.expected_count, "written": agg.written,
             "component_hash": agg.component_hash} if agg else None),
    )
    return StockCollectResult(
        run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot=batch_slot,
        value_type=ValueType.INTRADAY_LATEST.value, status=status, rows_written=rows_written,
        sectors_requested=requested, sectors_failed=len(failures), failures=failures,
        aggregate=agg,
    )


def collect_sector_members_once(
    *,
    sector_subject_id: int,
    source: "ObservationSource",
    observation_dao: "ObservationDao",
    subject_dao: "SubjectDao",
    constituent_dao: "ConstituentDao",
    run_dao: RunDao,
    source_code: str = "eastmoney",
    caliber: Caliber = Caliber.EASTMONEY,
    adapter_version: str = "unknown",
    clock: Clock | None = None,
) -> CollectResult:
    """板块成分个股下钻采集（US3）：写成员个股观测(LATEST) + 记归属(effective_from=当日)。

    无前视：成分退出闭合 effective_to（close_absent）。成员观测与全市场个股同为 LATEST 覆盖。
    """
    from quantchive.datasource.base import SubjectRef
    from quantchive.models.enums import AmountUnit, AssetClass, SubjectLevel

    clock = clock or SystemClock()
    now = clock.now()
    trade_date = now.astimezone(SHANGHAI_TZ).date().isoformat()
    observed_at = datetime.now(timezone.utc).isoformat()
    unit = AmountUnit.YUAN if caliber is Caliber.EASTMONEY else AmountUnit.YI

    sector = subject_dao.get(sector_subject_id)
    if sector is None:
        raise ValueError(f"板块主体 {sector_subject_id} 不存在")
    parent = SubjectRef(
        source_symbol=sector["source_symbol"], display_name=sector["display_name"],
        asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR,
        subject_kind=SubjectKind(sector["subject_kind"]), em_board_code=sector["em_board_code"],
    )

    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.INTRADAY_SNAPSHOT, caliber=caliber,
        trade_date=trade_date, minute_slot="LATEST", adapter_version=adapter_version,
        subject_scope=f"members:{parent.source_symbol}",
        asset_class_code=AssetClass.A_SHARE.value,
    )

    rows_written = 0
    requested = 0
    failures: list[dict] = []
    present_child_ids: list[int] = []
    try:
        members = source.fetch_members(parent)
    except DataSourceError as exc:
        run_dao.finish(run_id, status=RunStatus.FAILED, subjects_ok=0, subjects_failed=1,
                       error_type=exc.error_type, error_detail={"detail": exc.detail})
        return CollectResult(
            run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot="LATEST",
            value_type=ValueType.INTRADAY_LATEST.value, status=RunStatus.FAILED,
            rows_written=0, sectors_requested=0, sectors_failed=1,
            failures=[{"error_type": exc.error_type}])

    for o in members:
        requested += 1
        try:
            child_id, _ = subject_dao.upsert(
                asset_class=o.asset_class, level=o.level, subject_kind=o.subject_kind,
                source_code=source_code, source_symbol=o.source_symbol,
                display_name=o.display_name, caliber=caliber.value, exchange=o.exchange,
                collect_tier="coarse",
            )
            present_child_ids.append(child_id)
            constituent_dao.record(
                parent_subject_id=sector_subject_id, child_subject_id=child_id,
                effective_from=trade_date, source_code=source_code,
                ingestion_run_id=run_id, created_at=observed_at,
            )
            five_tier = None
            net_cents = None
            if o.main_net is not None:
                five_tier = {
                    "main_net_cents": to_cents(o.main_net, unit),
                    "super_large_net_cents": to_cents(o.super_large_net, unit),
                    "large_net_cents": to_cents(o.large_net, unit),
                    "medium_net_cents": to_cents(o.medium_net, unit),
                    "small_net_cents": to_cents(o.small_net, unit),
                }
                net_cents = five_tier["main_net_cents"]
            observation_dao.upsert(
                subject_id=child_id, source_code=source_code, trade_date=trade_date,
                minute_slot="LATEST", value_type=ValueType.INTRADAY_LATEST, granularity="5min",
                observed_at=observed_at, net_amount_cents=net_cents, five_tier=five_tier,
                price_micro=(None if o.price is None else to_micro(o.price)),
                change_pct_bp=(None if o.change_pct is None else to_basis_points(o.change_pct)),
                volume=(None if o.volume is None else int(o.volume)),
                turnover_cents=(None if o.turnover is None else to_cents(o.turnover, unit)),
                source_unit=unit.value, ingestion_run_id=run_id, created_at=observed_at,
            )
            rows_written += 1
        except Exception as exc:
            failures.append({"subject": o.source_symbol, "error": str(exc)})

    # 成分退出闭合 effective_to（无前视：本次未出现的旧成分闭合于当日）
    if rows_written > 0:
        constituent_dao.close_absent(
            parent_subject_id=sector_subject_id, present_child_ids=present_child_ids,
            effective_to=trade_date)

    status = _status(rows_written, requested, failures)
    run_dao.finish(run_id, status=status, subjects_ok=rows_written,
                   subjects_failed=len(failures),
                   error_type=(failures[0].get("error") if failures else None),
                   error_detail={"failures": failures} if failures else None)
    return CollectResult(
        run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot="LATEST",
        value_type=ValueType.INTRADAY_LATEST.value, status=status, rows_written=rows_written,
        sectors_requested=requested, sectors_failed=len(failures), failures=failures,
    )


def collect_all_sector_members(
    conn,
    *,
    source: "ObservationSource",
    source_code: str = "eastmoney",
    caliber: Caliber = Caliber.EASTMONEY,
    adapter_version: str = "unknown",
    clock: Clock | None = None,
    limit: int | None = None,
    progress: "object | None" = None,
) -> dict:
    """遍历全部板块采集其成分个股 + 归属（US3 下钻数据源）。

    对每个有 em_board_code 的板块调 collect_sector_members_once。单板块失败不阻塞其他。
    limit 限制板块数（调试用）；progress(done,total,name) 可选回调。
    """
    from quantchive.dao.constituent_dao import ConstituentDao
    from quantchive.models.enums import AssetClass, SubjectLevel

    subject_dao = SubjectDao(conn)
    obs_dao = ObservationDao(conn)
    con_dao = ConstituentDao(conn)
    run_dao = RunDao(conn)
    boards = [
        s for s in subject_dao.list_by(asset_class=AssetClass.A_SHARE, level=SubjectLevel.SECTOR)
        if s.get("em_board_code")
    ]
    if limit is not None:
        boards = boards[:limit]

    boards_ok = boards_failed = members_written = 0
    for i, b in enumerate(boards):
        try:
            res = collect_sector_members_once(
                sector_subject_id=b["subject_id"], source=source, observation_dao=obs_dao,
                subject_dao=subject_dao, constituent_dao=con_dao, run_dao=run_dao,
                source_code=source_code, caliber=caliber, adapter_version=adapter_version,
                clock=clock)
            # collect_sector_members_once 内部吞 DataSourceError 并返回 status=FAILED，
            # 故须查 result.status 而非仅靠异常判定板块成败。
            if res.status is RunStatus.FAILED:
                boards_failed += 1
            else:
                boards_ok += 1
                members_written += res.rows_written
        except Exception:  # noqa: BLE001 意外异常也隔离，单板块失败不阻塞其他
            boards_failed += 1
        if progress is not None:
            progress(i + 1, len(boards), b["display_name"])
    return {"boards_total": len(boards), "boards_ok": boards_ok,
            "boards_failed": boards_failed, "members_written": members_written}


def backfill_daily_flow(
    conn,
    *,
    source,
    scope: str = "sector",
    days: int = 30,
    source_code: str = "eastmoney",
    caliber: Caliber = Caliber.EASTMONEY,
    limit: int | None = None,
    sleep_sec: float = 0.0,
    min_days_guard: int = 2,
    progress: "object | None" = None,
) -> dict:
    """历史日线资金流回填：为板块/个股写 daily_final 观测（每交易日一行）。

    历史五档净额由东财 fflow/daykline 提供，回溯约 120 日。source 需有 fetch_daily_flow。
    幂等：daily_final + minute_slot='EOD'，同主体同日覆盖。

    **限流防护**：sleep_sec 在主体间节流（东财 fflow 对高频请求会**静默降级只返当日 1 条**，
    看似成功实则丢历史）。min_days_guard：请求多日却只回 <该值 天时判为限流，跳过不写
    （宁缺勿假，避免把限流的 1 天当历史落库）。throttled 计数经 summary 上报。
    """
    import time as _time
    from datetime import datetime as _dt, timezone as _tz

    from quantchive.core.money import to_cents
    from quantchive.datasource.em_kline_src import secid_for_board, secid_for_stock
    from quantchive.models.enums import AmountUnit, AssetClass, SubjectKind, SubjectLevel

    subject_dao = SubjectDao(conn)
    obs_dao = ObservationDao(conn)
    run_dao = RunDao(conn)
    now_iso = _dt.now(_tz.utc).isoformat()
    unit = AmountUnit.YUAN if caliber is Caliber.EASTMONEY else AmountUnit.YI

    if scope == "sector":
        subjects = [
            s for s in subject_dao.list_by(asset_class=AssetClass.A_SHARE,
                                           level=SubjectLevel.SECTOR)
            if s.get("em_board_code")
        ]
        def _secid(s): return secid_for_board(s["em_board_code"])
    elif scope == "stock":
        subjects = subject_dao.list_by(
            asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
            subject_kind=SubjectKind.STOCK)
        def _secid(s): return secid_for_stock(s["source_symbol"], s.get("exchange"))
    else:
        raise ValueError(f"未知 scope={scope}（须 sector|stock）")
    if limit is not None:
        subjects = subjects[:limit]

    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.EOD_BACKFILL, caliber=caliber,
        trade_date=now_iso[:10], minute_slot="EOD", adapter_version="fflow-daykline-v1",
        subject_scope=f"backfill:{scope}", asset_class_code=AssetClass.A_SHARE.value)

    subjects_ok = subjects_failed = rows_written = throttled = 0
    for i, s in enumerate(subjects):
        try:
            points = source.fetch_daily_flow(secid=_secid(s), days=days)
            # 限流降级探测：请求多日却只回极少（<guard）→ 判为被节流，跳过不写脏历史
            if days > min_days_guard and len(points) < min_days_guard:
                throttled += 1
                subjects_failed += 1
            else:
                for p in points:
                    obs_dao.upsert(
                        subject_id=s["subject_id"], source_code=source_code,
                        trade_date=p.trade_date, minute_slot="EOD",
                        value_type=ValueType.DAILY_FINAL, granularity="daily",
                        observed_at=now_iso,
                        net_amount_cents=to_cents(p.main_net, unit),
                        five_tier={
                            "main_net_cents": to_cents(p.main_net, unit),
                            "super_large_net_cents": to_cents(p.super_large_net, unit),
                            "large_net_cents": to_cents(p.large_net, unit),
                            "medium_net_cents": to_cents(p.medium_net, unit),
                            "small_net_cents": to_cents(p.small_net, unit),
                        },
                        source_unit=unit.value, ingestion_run_id=run_id, created_at=now_iso)
                    rows_written += 1
                subjects_ok += 1
        except Exception:  # noqa: BLE001 单主体失败隔离
            subjects_failed += 1
        if sleep_sec > 0:
            _time.sleep(sleep_sec)          # 主体间节流，避免触发东财限流
        if progress is not None:
            progress(i + 1, len(subjects), s["display_name"])

    run_dao.finish(run_id, status=RunStatus.SUCCESS, subjects_ok=subjects_ok,
                   subjects_failed=subjects_failed)
    return {"scope": scope, "subjects_total": len(subjects), "subjects_ok": subjects_ok,
            "subjects_failed": subjects_failed, "rows_written": rows_written,
            "throttled": throttled, "days": days}


def collect_etf_observations_once(
    *,
    source: "ObservationSource",
    observation_dao: "ObservationDao",
    subject_dao: "SubjectDao",
    run_dao: RunDao,
    source_code: str = "eastmoney",
    caliber: Caliber = Caliber.EASTMONEY,
    fs: str = "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
    adapter_version: str = "unknown",
    clock: Clock | None = None,
) -> CollectResult:
    """ETF 采集（US4）：写 intraday_snapshot 价/量 + circ_mktcap 稀疏附表。无五档。"""
    from quantchive.datasource.base import FetchSpec
    from quantchive.models.enums import AmountUnit, AssetClass, SubjectLevel

    clock = clock or SystemClock()
    now = clock.now()
    trade_date = now.astimezone(SHANGHAI_TZ).date().isoformat()
    batch_slot = _slot(now, ValueType.INTRADAY_SNAPSHOT)
    observed_at = datetime.now(timezone.utc).isoformat()
    unit = AmountUnit.YUAN if caliber is Caliber.EASTMONEY else AmountUnit.YI

    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.INTRADAY_SNAPSHOT, caliber=caliber,
        trade_date=trade_date, minute_slot=batch_slot, adapter_version=adapter_version,
        subject_scope="etf", asset_class_code=AssetClass.FUND_ETF.value,
    )

    spec = FetchSpec(fs=fs, level=SubjectLevel.INSTRUMENT, asset_class=AssetClass.FUND_ETF,
                     subject_kind=SubjectKind.ETF)
    rows_written = 0
    requested = 0
    failures: list[dict] = []
    try:
        observations = source.fetch_observations(spec)
    except DataSourceError as exc:
        run_dao.finish(run_id, status=RunStatus.FAILED, subjects_ok=0, subjects_failed=1,
                       error_type=exc.error_type, error_detail={"detail": exc.detail})
        return CollectResult(
            run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot=batch_slot,
            value_type=ValueType.INTRADAY_SNAPSHOT.value, status=RunStatus.FAILED,
            rows_written=0, sectors_requested=0, sectors_failed=1,
            failures=[{"error_type": exc.error_type}])

    for o in observations:
        requested += 1
        try:
            subject_id, _ = subject_dao.upsert(
                asset_class=o.asset_class, level=o.level, subject_kind=o.subject_kind,
                source_code=source_code, source_symbol=o.source_symbol,
                display_name=o.display_name, caliber=caliber.value, collect_tier="coarse",
            )
            obs_id = observation_dao.upsert(
                subject_id=subject_id, source_code=source_code, trade_date=trade_date,
                minute_slot=batch_slot, value_type=ValueType.INTRADAY_SNAPSHOT,
                granularity="5min", observed_at=observed_at,
                price_micro=(None if o.price is None else to_micro(o.price)),
                change_pct_bp=(None if o.change_pct is None else to_basis_points(o.change_pct)),
                volume=(None if o.volume is None else int(o.volume)),
                turnover_cents=(None if o.turnover is None else to_cents(o.turnover, unit)),
                source_unit=unit.value, ingestion_run_id=run_id, created_at=observed_at,
            )
            # circ_mktcap 进稀疏附表（诚实命名：流通市值 proxy，非 aum）
            circ = o.metrics.get("circ_mktcap")
            if circ is not None:
                observation_dao.upsert_metric(
                    observation_id=obs_id, metric_name="circ_mktcap",
                    value_int=to_cents(circ, unit))
            rows_written += 1
        except Exception as exc:
            failures.append({"subject": o.source_symbol, "error": str(exc)})

    status = _status(rows_written, requested, failures)
    run_dao.finish(run_id, status=status, subjects_ok=rows_written,
                   subjects_failed=len(failures),
                   error_type=(failures[0].get("error") if failures else None),
                   error_detail={"failures": failures} if failures else None)
    return CollectResult(
        run_id=run_id, caliber=caliber, trade_date=trade_date, batch_slot=batch_slot,
        value_type=ValueType.INTRADAY_SNAPSHOT.value, status=status, rows_written=rows_written,
        sectors_requested=requested, sectors_failed=len(failures), failures=failures,
    )


# ============================================================================
# 保留期分级降采 + 清理（US5，用户分级保留决策）
# ============================================================================


def retention_downsample(
    conn, *, as_of_date: str, downsample_after_days: int = 7,
    source_code: str = "eastmoney", caliber: Caliber = Caliber.EASTMONEY,
) -> dict:
    """把 (as_of - downsample_after_days) 天前的分钟点聚合成小时点，删原细粒度行。

    口径（data-model.md）：资金流/价格/量均取小时末值（东财累计快照末点=小时末=收盘；
    f5 成交量为当日累计，取末值即累计到小时末，绝不对累计序列求和）。
    小时点：value_type='hourly_rollup', granularity='hourly', minute_slot='HH:00'。
    删原行经 CASCADE 带走 observation_metric。幂等：原行删后重跑无候选。
    """
    from datetime import date as _date, timedelta

    obs_dao = ObservationDao(conn)
    run_dao = RunDao(conn)
    cutoff = (_date.fromisoformat(as_of_date) - timedelta(days=downsample_after_days)).isoformat()
    candidates = obs_dao.fine_grained_before(cutoff_date=cutoff)
    if not candidates:
        return {"downsampled_groups": 0, "rows_deleted": 0, "cutoff_date": cutoff}

    now_iso = datetime.now(timezone.utc).isoformat()
    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.RETENTION_DOWNSAMPLE, caliber=caliber,
        trade_date=cutoff, minute_slot=None, adapter_version="retention",
        subject_scope="downsample")

    # 按 (subject, trade_date, 小时) 分组
    groups: dict[tuple[int, str, str], list] = {}
    for r in candidates:
        hour = r.minute_slot[:2]  # 'HH:MM' → 'HH'
        groups.setdefault((r.subject_id, r.trade_date, hour), []).append(r)

    delete_ids: list[int] = []
    for (sid, td, hour), rows in groups.items():
        rows.sort(key=lambda x: x.minute_slot)
        last = rows[-1]  # 小时末（累计快照末点 = 小时末值 / 收盘价）
        # 东财 f5 成交量为**当日累计**（单调递增）——同小时的累计快照绝不能 sum（会 2-3 倍
        # 虚增，且与 turnover 取末值自相矛盾）。取小时末值=累计到小时末，口径一致。
        vol_last = int(last.volume) if last.volume is not None else None
        five_tier = None
        if last.main_net_cents is not None:
            five_tier = {
                "main_net_cents": last.main_net_cents,
                "super_large_net_cents": last.super_large_net_cents,
                "large_net_cents": last.large_net_cents,
                "medium_net_cents": last.medium_net_cents,
                "small_net_cents": last.small_net_cents,
            }
        obs_dao.upsert(
            subject_id=sid, source_code=source_code, trade_date=td,
            minute_slot=f"{hour}:00", value_type=ValueType.HOURLY_ROLLUP, granularity="hourly",
            observed_at=last.observed_at, net_amount_cents=last.net_amount_cents,
            five_tier=five_tier, price_micro=last.price_micro,
            change_pct_bp=last.change_pct_bp, volume=vol_last,
            turnover_cents=last.turnover_cents, source_unit="yuan",
            is_derived=bool(last.is_derived), ingestion_run_id=run_id, created_at=now_iso,
        )
        delete_ids.extend(r.observation_id for r in rows)

    deleted = obs_dao.delete_ids(delete_ids)
    run_dao.finish(run_id, status=RunStatus.SUCCESS, subjects_ok=len(groups), subjects_failed=0)
    return {"downsampled_groups": len(groups), "rows_deleted": deleted, "cutoff_date": cutoff}


def retention_cleanup(
    conn, *, as_of_date: str, retention_trade_days: int = 30,
    source_code: str = "eastmoney", caliber: Caliber = Caliber.EASTMONEY,
) -> dict:
    """删 trade_date < (as_of - retention_trade_days) 的观测（CASCADE 带走稀疏指标）。"""
    from datetime import date as _date, timedelta

    obs_dao = ObservationDao(conn)
    run_dao = RunDao(conn)
    cutoff = (_date.fromisoformat(as_of_date) - timedelta(days=retention_trade_days)).isoformat()
    run_id = run_dao.start(
        source_code=source_code, run_type=RunType.RETENTION_CLEANUP, caliber=caliber,
        trade_date=cutoff, minute_slot=None, adapter_version="retention",
        subject_scope="cleanup")
    deleted = obs_dao.delete_before_date(trade_date_exclusive=cutoff)
    run_dao.finish(run_id, status=RunStatus.SUCCESS, subjects_ok=deleted, subjects_failed=0)
    return {"rows_deleted": deleted, "cutoff_date": cutoff}
