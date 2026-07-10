"""基本面信号检测（阶段D×B 融合,纯函数,无DB,防前视)。

事件在报告期序列上逐期检测(只用当期及之前),可见时点=**法定披露截止日**(保守 PIT):
akshare announce_date 实测不可靠(重述致跨期不一致),故用证监会规定的披露截止日。
Q1→4/30、半年→8/31、Q3→10/31、年报(Q4)→次年4/30。
"""

from __future__ import annotations

from dataclasses import dataclass

# 信号类型
PROFIT_TURN_POSITIVE = "profit_turn_positive"   # 净利增速由负转正
PROFIT_ACCELERATE = "profit_accelerate"         # 净利增速较上期显著提升
ROE_JUMP = "roe_jump"                           # ROE 较上期跳升
REVENUE_ACCELERATE = "revenue_accelerate"       # 营收增速较上期显著提升

FUND_SIGNAL_KINDS = (
    PROFIT_TURN_POSITIVE, PROFIT_ACCELERATE, ROE_JUMP, REVENUE_ACCELERATE)


@dataclass(frozen=True)
class FundamentalSignalHit:
    """单股单报告期基本面信号(可见日=法定披露截止日)。"""

    report_period: str
    visible_date: str          # 法定披露截止日(保守 PIT 可见时点)
    kind: str
    strength: int | None = None   # 跳变幅度(基点)


def report_period_deadline(period: str) -> str:
    """报告期 → 法定披露截止日 'YYYY-MM-DD'。Q1→4/30 H1→8/31 Q3→10/31 年报→次年4/30。

    ⚠️ 局限(审计M4):对**逾期披露**公司(多为ST/问题股,少数尾部),实际公开日晚于
    法定截止日,按截止日入场构成有界前视。对按时/提前披露的多数样本严格保守无泄漏。
    """
    y, q = period.split("Q")
    return {
        "1": f"{y}-04-30", "2": f"{y}-08-31", "3": f"{y}-10-31",
        "4": f"{int(y) + 1}-04-30",
    }[q]


def ytd_to_single_quarter(series: list[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
    """累计(YTD)口径 → 单季口径:同年内 Q_n = 累计Q_n − 累计Q_{n-1},Q1 原样,跨年重置。

    审计F3:yjbb 的 ROE 等是年内累计值,直接跨期比较会在 Q3→Q4 机械触发假信号。
    任一端缺值 → 该季 None(不冒充)。series 按报告期升序。
    """
    out: list[tuple[str, int | None]] = []
    prev_period: str | None = None
    prev_cum: int | None = None
    for period, cum in series:
        y, q = period.split("Q")
        if q == "1" or prev_period is None or prev_period.split("Q")[0] != y:
            sq = cum                                   # Q1 或跨年首期:累计=单季
        else:
            sq = None if (cum is None or prev_cum is None) else cum - prev_cum
        out.append((period, sq))
        prev_period, prev_cum = period, cum
    return out


def detect_fundamental_signals(
    series: list[tuple[str, int | None]], *, kind: str, jump_bp: int = 2000,
) -> list[FundamentalSignalHit]:
    """检测某类基本面信号。series=按报告期升序的 (period, value_int(基点)) —— 对应指标:
    净利/营收增速 series 传 net_profit_yoy/revenue_yoy(bp);roe 传 roe(bp)。

    防前视:第 i 期只比较 [i-1, i]。jump_bp=加速/跳升阈值(默认 +20pp=2000bp)。
    """
    out: list[FundamentalSignalHit] = []
    prev: int | None = None
    for period, cur in series:
        if cur is not None and prev is not None:
            hit = None
            if kind == PROFIT_TURN_POSITIVE and prev < 0 <= cur:
                hit = cur - prev
            elif kind in (PROFIT_ACCELERATE, REVENUE_ACCELERATE) and (cur - prev) >= jump_bp:
                hit = cur - prev
            elif kind == ROE_JUMP and (cur - prev) >= jump_bp:
                hit = cur - prev
            if hit is not None:
                out.append(FundamentalSignalHit(
                    report_period=period, visible_date=report_period_deadline(period),
                    kind=kind, strength=hit))
        if cur is not None:
            prev = cur
    return out
