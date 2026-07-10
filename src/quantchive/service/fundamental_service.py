"""基本面查询（阶段D）。PIT 按 announce_date 取最新报告期,pivot KV → 分节 DTO。

无前视:as_of 给定则只见 announce_date<=as_of 的报告期(缺省=最新已披露)。
值按 unit 反标度成可读字符串(禁前端 float:整数→字符串)。

⚠️ announce_date 语义(实测):
- 业绩报表(performance/yjbb)公告日 ≈ 首次披露日(可用于 PIT)。
- 三大报表(balance/income/cashflow)公告日 ≈ 最新重述/更新日,常晚 1 年+
  → 对 PIT 是**保守方向**(数据可见时点被推迟,不泄漏未来,只是有损)。
- agent"当前基本面"(as_of=None)不受影响:取最新报告期即可。
- ML/回测严格 PIT:保守安全但有损,优先用业绩报表公告日。
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from quantchive.models.enums import SourceType
from quantchive.service.dto import DataProvenance, FundamentalResult
from quantchive.service.errors import NoDataForDate

_SRC = "eastmoney_fin"

# 报表码 → 中文节名
_STMT_LABEL = {
    "performance": "业绩", "balance": "资产负债", "income": "利润", "cashflow": "现金流",
}
# 项码 → (中文名, 展示单位)。cents→亿元、bp→%、micro→元。
_ITEM_LABEL = {
    "eps": ("每股收益", "元"), "revenue": ("营业总收入", "亿"), "revenue_yoy": ("营收同比", "%"),
    "net_profit": ("净利润", "亿"), "net_profit_yoy": ("净利同比", "%"),
    "bps": ("每股净资产", "元"), "roe": ("净资产收益率", "%"), "gross_margin": ("销售毛利率", "%"),
    "ocfps": ("每股经营现金流", "元"),
    "cash": ("货币资金", "亿"), "receivable": ("应收账款", "亿"), "inventory": ("存货", "亿"),
    "total_assets": ("总资产", "亿"), "total_assets_yoy": ("总资产同比", "%"),
    "total_liab": ("总负债", "亿"), "total_liab_yoy": ("总负债同比", "%"),
    "debt_ratio": ("资产负债率", "%"), "equity": ("股东权益", "亿"),
    "sell_expense": ("销售费用", "亿"), "admin_expense": ("管理费用", "亿"),
    "fin_expense": ("财务费用", "亿"), "op_profit": ("营业利润", "亿"),
    "total_profit": ("利润总额", "亿"),
    "net_cash": ("净现金流", "亿"), "net_cash_yoy": ("净现金流同比", "%"),
    "ocf": ("经营现金流净额", "亿"), "icf": ("投资现金流净额", "亿"), "fcf": ("筹资现金流净额", "亿"),
}


def _fmt(value_int: int, unit: str, disp: str) -> str:
    """整数标度 → 可读字符串(禁float:用 Decimal)。"""
    d = Decimal(value_int)
    if unit == "cents":
        yi = d / Decimal(100) / Decimal(100_000_000)   # 分→元→亿
        return f"{yi.quantize(Decimal('0.01'))}亿"
    if unit == "bp":
        return f"{(d / 100).quantize(Decimal('0.01'))}%"
    if unit == "micro":
        return f"{(d / 1_000_000).quantize(Decimal('0.0001'))}元"
    return str(d)


class FundamentalService:
    """基本面 PIT 查询(读 fundamental_item)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def latest(self, *, subject_id: int, as_of: str | None = None) -> FundamentalResult:
        subj = self._conn.execute(
            "SELECT source_symbol, display_name FROM subject WHERE subject_id=?",
            (subject_id,)).fetchone()
        if subj is None:
            raise NoDataForDate("主体不存在", detail={"subject_id": subject_id})

        # PIT:as_of 限 announce_date<=as_of;取最新报告期(报告期字符串可比:2024Q4>2024Q1)
        params: list = [subject_id]
        pit = ""
        if as_of:
            pit = " AND (announce_date IS NULL OR announce_date <= ?)"
            params.append(as_of)
        row = self._conn.execute(
            f"SELECT report_period FROM fundamental_item WHERE subject_id=?{pit} "
            f"ORDER BY report_period DESC LIMIT 1", params).fetchone()
        if row is None:
            raise NoDataForDate(
                f"{subj['display_name']} 无基本面数据(需回填 --target fundamentals)",
                detail={"subject_id": subject_id})
        period = row[0]

        items = self._conn.execute(
            "SELECT statement, item, value_int, unit, announce_date FROM fundamental_item "
            "WHERE subject_id=? AND report_period=?", (subject_id, period)).fetchall()
        sections: dict[str, dict[str, str]] = {}
        announce = None
        for it in items:
            announce = announce or it["announce_date"]
            if it["value_int"] is None:
                continue
            sec = _STMT_LABEL.get(it["statement"], it["statement"])
            name, _u = _ITEM_LABEL.get(it["item"], (it["item"], ""))
            sections.setdefault(sec, {})[name] = _fmt(it["value_int"], it["unit"], name)

        prov = DataProvenance(
            trade_date=announce or period, source_type=SourceType.DAILY_FINAL,
            source_id=_SRC, captured_at=announce or "报告期", is_stale=False)
        return FundamentalResult(
            subject_id=subject_id, source_symbol=subj["source_symbol"],
            display_name=subj["display_name"], report_period=period,
            announce_date=announce, sections=sections, provenance=prov)
