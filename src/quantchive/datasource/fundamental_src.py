"""东财基本面适配（阶段D）。业绩报表 + 三大报表(资产负债/利润/现金流),akshare 逆向封装。

锁版本收口:akshare 列名硬编在 _MAPS(改版只此处改)。可注入 fetch_df 测试(不联网,宪章IV)。
标度:金额→分(×100)、比率/同比→基点(×100)、每股→微元(×1e6);缺值→None(不冒充0,宪章III)。
report_period 期末日 'YYYYMMDD' → '2024Q4' 形态;announce_date 做 PIT 可见时点。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

from quantchive.datasource.base import DataSourceError

# 报表 → (akshare 函数名, [(列名, 项码, 单位)])。单位 cents/bp/micro 决定标度。
_MAPS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "performance": ("stock_yjbb_em", [
        ("每股收益", "eps", "micro"),
        ("营业总收入-营业总收入", "revenue", "cents"),
        ("营业总收入-同比增长", "revenue_yoy", "bp"),
        ("净利润-净利润", "net_profit", "cents"),
        ("净利润-同比增长", "net_profit_yoy", "bp"),
        ("每股净资产", "bps", "micro"),
        ("净资产收益率", "roe", "bp"),
        ("销售毛利率", "gross_margin", "bp"),
        ("每股经营现金流量", "ocfps", "micro"),
    ]),
    "balance": ("stock_zcfz_em", [
        ("资产-货币资金", "cash", "cents"),
        ("资产-应收账款", "receivable", "cents"),
        ("资产-存货", "inventory", "cents"),
        ("资产-总资产", "total_assets", "cents"),
        ("资产-总资产同比", "total_assets_yoy", "bp"),
        ("负债-总负债", "total_liab", "cents"),
        ("负债-总负债同比", "total_liab_yoy", "bp"),
        ("资产负债率", "debt_ratio", "bp"),
        ("股东权益合计", "equity", "cents"),
    ]),
    "income": ("stock_lrb_em", [
        ("净利润", "net_profit", "cents"),
        ("净利润同比", "net_profit_yoy", "bp"),
        ("营业总收入", "revenue", "cents"),
        ("营业总收入同比", "revenue_yoy", "bp"),
        ("营业总支出-销售费用", "sell_expense", "cents"),
        ("营业总支出-管理费用", "admin_expense", "cents"),
        ("营业总支出-财务费用", "fin_expense", "cents"),
        ("营业利润", "op_profit", "cents"),
        ("利润总额", "total_profit", "cents"),
    ]),
    "cashflow": ("stock_xjll_em", [
        ("净现金流-净现金流", "net_cash", "cents"),
        ("净现金流-同比增长", "net_cash_yoy", "bp"),
        ("经营性现金流-现金流量净额", "ocf", "cents"),
        ("投资性现金流-现金流量净额", "icf", "cents"),
        ("融资性现金流-现金流量净额", "fcf", "cents"),
    ]),
}
_CODE_COL = "股票代码"
_ANNOUNCE_COLS = ("最新公告日期", "公告日期")   # yjbb 用前者,三大报表用后者


@dataclass(frozen=True)
class RawFundItem:
    """单股单报告期单报表单行项(归一后,整数标度)。"""

    code: str
    report_period: str
    announce_date: str | None
    statement: str
    item: str
    value_int: int | None
    unit: str


def period_to_yyyymmdd(report_period: str) -> str:
    """'2024Q4'→'20241231'(季末日,akshare date 参数)。"""
    y, q = report_period.split("Q")
    return {"1": f"{y}0331", "2": f"{y}0630", "3": f"{y}0930", "4": f"{y}1231"}[q]


def _scale(unit: str, raw: object) -> int | None:
    """按单位标度成整数;缺值/非数 → None(不冒充0)。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "nan", "None", "False", "--"):
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    factor = {"cents": 100, "bp": 100, "micro": 1_000_000}[unit]
    return int((d * factor).to_integral_value())


class EastMoneyFundamentalSource:
    """东财基本面源。fetch_df 可注入(测试传 fake,不联网)。"""

    def __init__(self, fetch_df: Callable[[str, str], object] | None = None) -> None:
        self._fetch_df = fetch_df or self._real_fetch

    @staticmethod
    def _real_fetch(fn_name: str, date: str):
        import akshare as ak
        return getattr(ak, fn_name)(date=date)

    def fetch_statement(self, *, statement: str, report_period: str) -> list[RawFundItem]:
        """取某报告期某报表全市场行项。缺列跳过,缺值 None,异常包 DataSourceError。"""
        if statement not in _MAPS:
            raise DataSourceError(f"未知报表: {statement}", error_type="empty_or_dash")
        fn_name, cols = _MAPS[statement]
        date = period_to_yyyymmdd(report_period)
        try:
            df = self._fetch_df(fn_name, date)
        except Exception as exc:  # noqa: BLE001 → 结构化(供 failover)
            raise DataSourceError(
                f"东财{statement}失败: {type(exc).__name__}", error_type="rate_limited") from exc
        if df is None or getattr(df, "empty", True):
            raise DataSourceError(f"东财{statement}空", error_type="empty_or_dash")

        colset = set(df.columns)
        ann_col = next((c for c in _ANNOUNCE_COLS if c in colset), None)
        out: list[RawFundItem] = []
        for _, row in df.iterrows():
            code = str(row.get(_CODE_COL) or "").strip()
            if not code:
                continue
            ann = None
            if ann_col is not None:
                av = row.get(ann_col)
                ann = None if av is None else str(av).strip()[:10] or None
            for col, item, unit in cols:
                if col not in colset:
                    continue
                v = _scale(unit, row.get(col))
                if v is None:
                    continue                # 缺值不落(宁缺勿假)
                out.append(RawFundItem(
                    code=code, report_period=report_period, announce_date=ann,
                    statement=statement, item=item, value_int=v, unit=unit))
        return out
