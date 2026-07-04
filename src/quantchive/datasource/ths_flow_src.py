"""同花顺 10jqka 资金流源（spec003 D6 · 资金流备源）。

同花顺 `data.10jqka.com.cn` 是**独立于东财的后端**——东财 IP 限流时可分流。经 akshare
`stock_fund_flow_individual` 取实时个股资金流快照。**只有主力净额，无五档**（诚实自描述）。
金额为中文单位串（'2.07亿'/'941.88万'）→ 解析归一到元 Decimal。hexin-v 头由 akshare 处理。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Sequence

from quantchive.core.logging import get_logger
from quantchive.datasource.base import DataSourceError, FetchSpec
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)

_log = get_logger(__name__)


def _cn_amount_to_yuan(text: object) -> Decimal | None:
    """中文单位金额串 → 元 Decimal：'2.07亿'→207000000、'941.88万'→9418800。"""
    t = str(text).strip()
    if t in {"", "-", "--", "None", "nan"}:
        return None
    mult = Decimal(1)
    if t.endswith("亿"):
        mult, t = Decimal("100000000"), t[:-1]
    elif t.endswith("万"):
        mult, t = Decimal("10000"), t[:-1]
    try:
        return Decimal(t) * mult
    except (ArithmeticError, ValueError):
        return None


def _pct(text: object) -> Decimal | None:
    t = str(text).strip().rstrip("%")
    if t in {"", "-", "--", "None", "nan"}:
        return None
    try:
        return Decimal(t)
    except (ArithmeticError, ValueError):
        return None


def _exchange_for(code: str) -> str:
    return "SSE" if code[:1] == "6" else "BSE" if code[:1] in ("4", "8") else "SZSE"


class ThsFlowSource:
    """同花顺资金流源（ObservationSource）。备源：只主力净额，无五档。"""

    source_id = "ths_flow"
    adapter_version = "ths-10jqka-v1"

    def __init__(self, *, records_fn: Callable[[str], list[dict]] | None = None) -> None:
        # records_fn 可注入（测试 fake，不联网）；默认 akshare 同花顺个股资金流 → records
        self._records_fn = records_fn

    def _fetch_records(self, symbol: str) -> list[dict]:
        if self._records_fn is not None:
            return self._records_fn(symbol)
        import akshare as ak
        df = ak.stock_fund_flow_individual(symbol=symbol)
        return df.to_dict("records")

    def capability(self, spec: FetchSpec):
        return None

    def fetch_subjects(self, spec: FetchSpec):
        return []

    def fetch_members(self, parent):
        return []

    def fetch_daily_final(self, spec: FetchSpec, trade_date):
        return []

    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]:
        """实时个股资金流快照（主力净额）。失败抛 DataSourceError（可触发 failover 回主源）。"""
        try:
            records = self._fetch_records("即时")
        except Exception as exc:  # noqa: BLE001 → 结构化错误，供路由判定
            raise DataSourceError(
                f"同花顺资金流失败: {type(exc).__name__}", error_type="rate_limited") from exc
        out: list[RawObservation] = []
        for r in records:
            code = str(r.get("股票代码") or "").strip().zfill(6)
            if not code or code == "000000":
                continue
            net = _cn_amount_to_yuan(r.get("净额"))
            out.append(RawObservation(
                source_symbol=code, display_name=str(r.get("股票简称") or code),
                asset_class=AssetClass.A_SHARE, level=SubjectLevel.INSTRUMENT,
                subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
                main_net=net,                       # 仅主力净额，无五档（诚实）
                price=_cn_amount_to_yuan(r.get("最新价")) if r.get("最新价") else None,
                change_pct=_pct(r.get("涨跌幅")),
                turnover=_cn_amount_to_yuan(r.get("成交额")),
                exchange=_exchange_for(code), source_unit=AmountUnit.YUAN))
        return out
