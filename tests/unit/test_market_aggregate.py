"""T027: 大盘求和覆盖率门禁单元测试（Python int 求和、<95% 不写、成分哈希、禁 float）。"""

from __future__ import annotations

from quantchive.dao.observation_dao import ObservationRow
from quantchive.service.market_aggregate import aggregate_stock_cents


def _stock(symbol: str, main: int | None) -> ObservationRow:
    """构造个股 ObservationRow；main=None 表示该股五档缺失（不计入求和）。"""
    tiers = (main, main, main, main, main) if main is not None else (None,) * 5
    return ObservationRow(
        observation_id=0, subject_id=0, source_symbol=symbol, display_name=symbol,
        source_code="eastmoney",
        trade_date="2026-07-03", minute_slot="LATEST", value_type="intraday_latest",
        granularity="5min", observed_at="t", net_amount_cents=main,
        main_net_cents=tiers[0], super_large_net_cents=tiers[1], large_net_cents=tiers[2],
        medium_net_cents=tiers[3], small_net_cents=tiers[4],
        super_large_gross_cents=None, large_gross_cents=None,
        medium_gross_cents=None, small_gross_cents=None,
        price_micro=None, change_pct_bp=None, volume=None, turnover_cents=None,
        turnover_pct_bp=None,
    )


def test_int_sum_no_float() -> None:
    rows = [_stock("A", 100), _stock("B", 200), _stock("C", -50)]
    agg = aggregate_stock_cents(rows, expected_count=3)
    assert agg.written is True
    assert agg.main_net_cents == 250            # 100+200-50, 纯 int
    assert isinstance(agg.main_net_cents, int)   # 不是 float
    assert agg.five_tier_cents["small_net_cents"] == 250
    assert agg.coverage == 1.0


def test_below_95_coverage_not_written() -> None:
    # 100 只应采，仅 90 只有效 → 覆盖率 90% < 95% → 不落
    rows = [_stock(f"S{i}", 10) for i in range(90)]
    agg = aggregate_stock_cents(rows, expected_count=100)
    assert agg.written is False
    assert agg.main_net_cents is None
    assert 0.89 < agg.coverage < 0.91
    assert "覆盖率" in agg.reason


def test_at_95_coverage_written() -> None:
    rows = [_stock(f"S{i}", 10) for i in range(95)]
    agg = aggregate_stock_cents(rows, expected_count=100)
    assert agg.written is True
    assert agg.main_net_cents == 950


def test_null_five_tier_stocks_excluded() -> None:
    # 五档缺失的个股不计入 constituent（宁缺勿假）；2/3 覆盖率 < 95% → 不落
    rows = [_stock("A", 100), _stock("B", None), _stock("C", 200)]
    agg = aggregate_stock_cents(rows, expected_count=3)
    assert agg.constituent_count == 2
    assert agg.written is False
    assert agg.main_net_cents is None


def test_null_excluded_but_high_coverage_sums_only_valid() -> None:
    # 100 只应采，98 只有效(2 只五档缺失) → 98% ≥ 95% → 落；求和只含有效股
    rows = [_stock(f"S{i}", 10) for i in range(98)] + [_stock("X", None), _stock("Y", None)]
    agg = aggregate_stock_cents(rows, expected_count=100)
    assert agg.constituent_count == 98
    assert agg.written is True
    assert agg.main_net_cents == 980           # 仅 98 只有效股求和


def test_component_hash_reproducible_and_order_independent() -> None:
    a = aggregate_stock_cents([_stock("A", 1), _stock("B", 2)], expected_count=2)
    b = aggregate_stock_cents([_stock("B", 2), _stock("A", 1)], expected_count=2)
    assert a.component_hash == b.component_hash   # 排序后哈希，与输入序无关
    c = aggregate_stock_cents([_stock("A", 1), _stock("X", 2)], expected_count=2)
    assert a.component_hash != c.component_hash    # 成分不同则哈希不同


def test_expected_zero_not_written() -> None:
    agg = aggregate_stock_cents([], expected_count=0)
    assert agg.written is False
    assert agg.coverage == 0.0
