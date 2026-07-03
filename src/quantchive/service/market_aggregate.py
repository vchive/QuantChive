"""大盘求和聚合（T030，D2）。采集层求和个股→写一条 is_derived 大盘 observation。

铁律（D2/宪章 I,III）：
- Python int 求和分（禁 float），断点静默少算会破 SC-007 可复现。
- 覆盖率 constituent_count/expected_count ≥ 95% 才写；<95% 不落（宁缺勿假，不冒充全市场）。
- 复用个股那次采集的 batch_slot（禁重取 clock.now()）。
- 成分集合哈希落审计供重放。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from quantchive.dao.observation_dao import ObservationDao, ObservationRow
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel, ValueType

MARKET_SYMBOL = "__MARKET__"
_DEFAULT_THRESHOLD = 0.95


@dataclass(frozen=True)
class MarketAggregate:
    """求和结果（纯计算，无 DB）。written=False 表示覆盖率不足未落。"""

    written: bool
    coverage: float
    constituent_count: int
    expected_count: int
    main_net_cents: int | None
    five_tier_cents: dict[str, int] | None
    component_hash: str | None
    reason: str | None = None


_TIER_ATTRS = (
    ("main_net_cents", lambda r: r.main_net_cents),
    ("super_large_net_cents", lambda r: r.super_large_net_cents),
    ("large_net_cents", lambda r: r.large_net_cents),
    ("medium_net_cents", lambda r: r.medium_net_cents),
    ("small_net_cents", lambda r: r.small_net_cents),
)


def aggregate_stock_cents(
    rows: list[ObservationRow], *, expected_count: int, threshold: float = _DEFAULT_THRESHOLD,
) -> MarketAggregate:
    """纯求和：Python int 累加五档分，覆盖率门禁，成分哈希。不碰 DB、不用 float。"""
    # 只统计五档齐全的个股（main_net_cents 非空即五档齐，schema all-or-nothing 保证）
    valid = [r for r in rows if r.main_net_cents is not None]
    constituent = len(valid)
    # 成分哈希：参与求和的 source_symbol 排序后 sha256（可复现审计）
    symbols = sorted(r.source_symbol for r in valid)
    component_hash = hashlib.sha256("\n".join(symbols).encode("utf-8")).hexdigest()

    # expected_count<=0 表全集未知（源未报 total / 翻页截断）——覆盖率无从校验，
    # 一律不落（宁缺勿假，fail-closed；绝不用 max(expected,constituent) 自证 100%）。
    if expected_count <= 0:
        return MarketAggregate(
            written=False, coverage=0.0, constituent_count=constituent,
            expected_count=expected_count, main_net_cents=None, five_tier_cents=None,
            component_hash=component_hash, reason="expected 未知(源未报 total)，覆盖率不可校验",
        )
    # expected 取源报告的真实全集；不与 constituent 取 max（否则截断被自证掩盖）
    coverage = constituent / expected_count

    if coverage < threshold:
        return MarketAggregate(
            written=False, coverage=coverage, constituent_count=constituent,
            expected_count=expected_count, main_net_cents=None, five_tier_cents=None,
            component_hash=component_hash,
            reason=f"覆盖率 {coverage:.4f} < {threshold}",
        )

    five_tier: dict[str, int] = {}
    for col, getter in _TIER_ATTRS:
        # int 求和；缺档按 0（valid 已保证五档齐，此处稳妥兜底）
        five_tier[col] = sum(int(getter(r) or 0) for r in valid)
    return MarketAggregate(
        written=True, coverage=coverage, constituent_count=constituent,
        expected_count=expected_count, main_net_cents=five_tier["main_net_cents"],
        five_tier_cents=five_tier, component_hash=component_hash,
    )


class MarketAggregator:
    """读个股 LATEST 行 → 求和 → 写大盘 observation（覆盖率门禁）。"""

    def __init__(self, conn, *, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self._obs = ObservationDao(conn)
        self._subject = SubjectDao(conn)
        self._threshold = threshold

    def market_subject_id(self, *, source_code: str) -> int:
        sid, _ = self._subject.upsert(
            asset_class=AssetClass.A_SHARE, level=SubjectLevel.MARKET,
            subject_kind=SubjectKind.MARKET_TOTAL, source_code=source_code,
            source_symbol=MARKET_SYMBOL, display_name="A股大盘", collect_tier="derived",
        )
        return sid

    def compute_and_write(
        self, *, trade_date: str, batch_slot: str, observed_at: str, expected_count: int,
        source_code: str, ingestion_run_id: int, created_at: str,
        stock_value_type: ValueType = ValueType.INTRADAY_LATEST, stock_slot: str = "LATEST",
    ) -> MarketAggregate:
        """求和个股 LATEST 行写大盘点。复用传入 batch_slot（禁 clock.now()）。"""
        rows = self._obs.stock_rows_for(
            trade_date=trade_date, value_type=stock_value_type, minute_slot=stock_slot)
        agg = aggregate_stock_cents(
            rows, expected_count=expected_count, threshold=self._threshold)
        if not agg.written:
            return agg  # <95% 不落
        market_id = self.market_subject_id(source_code=source_code)
        # 大盘点：is_derived=1、粒度对齐个股 5min、minute_slot=个股批次 slot（大盘时序）
        self._obs.upsert(
            subject_id=market_id, source_code=source_code, trade_date=trade_date,
            minute_slot=batch_slot, value_type=ValueType.INTRADAY_SNAPSHOT, granularity="5min",
            observed_at=observed_at, net_amount_cents=agg.main_net_cents,
            five_tier=agg.five_tier_cents, constituent_count=agg.constituent_count,
            expected_count=agg.expected_count, source_unit="yuan", is_derived=True,
            ingestion_run_id=ingestion_run_id, created_at=created_at,
        )
        return agg
