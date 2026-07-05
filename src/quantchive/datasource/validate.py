"""多源主备校验（spec005 US4）。三级：①恒等式自检 ②跨源方向/量级 ③只标记不改数。

宪章 V：校验分歧记审计、只标记不改数、绝不自动改数。口径差异（数值有差但方向量级合理）
不报——各源档位阈值定义不同，不能要求相等。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityResult:
    ok: bool
    reason: str = ""


def check_identity(gross_cents: int, net_cents: int) -> IdentityResult:
    """恒等式自检：流入=(gross+net)/2、流出=(gross-net)/2。

    要求 gross≥0 且 gross≥|net|（流入流出非负）。gross+net 为奇不算坏数据——
    源的 gross/net 各自独立舍入到元，1 分奇偶差是正常舍入（整除丢 0.5 分可接受），
    只在真正矛盾（成交额为负、净额超成交额）时拒绝。
    """
    if gross_cents < 0:
        return IdentityResult(False, "成交额为负")
    if abs(net_cents) > gross_cents:
        return IdentityResult(False, f"净额绝对值 {abs(net_cents)} 超成交额 {gross_cents}")
    return IdentityResult(True)


@dataclass(frozen=True)
class CrossSourceVerdict:
    status: str          # 'ok' | 'divergence'
    reason: str = ""


def cross_source_verdict(
    net_a: int, net_b: int, *, magnitude_ratio: float = 3.0,
) -> CrossSourceVerdict:
    """跨源比对（新浪 vs 百度当日同档位净额）：

    - 方向相反（一正一负，且都非零）→ divergence
    - 量级差 > magnitude_ratio 倍 → divergence
    - 数值有差但方向量级合理 → ok（口径差异，不报）
    """
    # 方向相反（都显著非零）
    if net_a > 0 > net_b or net_a < 0 < net_b:
        if abs(net_a) > 0 and abs(net_b) > 0:
            return CrossSourceVerdict("divergence", f"方向相反：{net_a} vs {net_b}")
    # 量级差
    hi, lo = max(abs(net_a), abs(net_b)), min(abs(net_a), abs(net_b))
    if lo == 0:
        if hi > 0:
            return CrossSourceVerdict("divergence", f"一源为零另源 {hi}")
        return CrossSourceVerdict("ok")   # 都为零
    if hi / lo > magnitude_ratio:
        return CrossSourceVerdict("divergence", f"量级差 {hi/lo:.1f}倍 > {magnitude_ratio}")
    return CrossSourceVerdict("ok")       # 口径差异，方向量级合理
