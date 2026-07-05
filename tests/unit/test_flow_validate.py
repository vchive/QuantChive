"""T026: 三级校验（恒等式自检 + 跨源方向/量级 + 口径差异不报）（spec005 US4）。"""

from __future__ import annotations

from quantchive.datasource.validate import check_identity, cross_source_verdict


def test_identity_ok() -> None:
    """流入900+流出1300=成交额2200、流入-流出=-400=净额。"""
    r = check_identity(gross_cents=2200, net_cents=-400)
    assert r.ok


def test_identity_rejects_net_exceeds_gross() -> None:
    """净额绝对值超成交额 → 坏数据拒绝。"""
    r = check_identity(gross_cents=100, net_cents=-500)
    assert not r.ok and "超成交额" in r.reason


def test_identity_odd_parity_accepted() -> None:
    """gross+net 为奇（源各自舍入到元的1分差）→ 正常，非坏数据（整除丢0.5分可接受）。"""
    r = check_identity(gross_cents=100, net_cents=1)   # 101 奇但方向量级正常
    assert r.ok


def test_identity_rejects_negative_gross() -> None:
    assert not check_identity(gross_cents=-1, net_cents=0).ok


def test_cross_source_direction_opposite() -> None:
    """方向相反 → 分歧。"""
    v = cross_source_verdict(net_a=1000, net_b=-800)
    assert v.status == "divergence" and "方向相反" in v.reason


def test_cross_source_magnitude_gap() -> None:
    """量级差 > 3 倍 → 分歧。"""
    v = cross_source_verdict(net_a=1000, net_b=100, magnitude_ratio=3.0)
    assert v.status == "divergence" and "量级差" in v.reason


def test_cross_source_caliber_diff_not_reported() -> None:
    """数值有差但方向量级合理（口径差异）→ 不报。"""
    v = cross_source_verdict(net_a=1000, net_b=800, magnitude_ratio=3.0)
    assert v.status == "ok"


def test_cross_source_both_zero_ok() -> None:
    assert cross_source_verdict(net_a=0, net_b=0).status == "ok"


def test_cross_source_one_zero_divergence() -> None:
    """一源为零另源显著 → 分歧。"""
    assert cross_source_verdict(net_a=0, net_b=5000).status == "divergence"
