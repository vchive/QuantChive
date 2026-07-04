"""新浪财经个股历史资金流源（spec003 多源扩展 · main_net 历史）。

新浪 `vip.stock.finance.sina.com.cn` MoneyFlow 接口——**独立于东财后端**，实测可取
个股**逐日历史资金流**（约 8 年深，沪深皆可），补东财 fflow 限流时的资金流历史空缺。

字段(实测)：opendate/trade/netamount + r0_net(超大)/r1_net(大)/r2_net(中)/r3_net(小)，元级。
归一：main_net = r0_net+r1_net（超大+大单，与东财主力口径一致）；五档齐全。免费无 token。
http_get 可注入（宪章 IV，测试 fake 不联网）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Sequence

from quantchive.core.logging import get_logger
from quantchive.datasource.base import DataSourceError
from quantchive.datasource.dto import RawObservation
from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)

_log = get_logger(__name__)

_SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "MoneyFlow.ssl_qsfx_lscjfb")
_SINA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn/",
}


def secid_for_sina(code: str, exchange: str | None) -> str:
    """新浪 daima：沪 sh / 深 sz / 北 bj（按交易所或代码前缀）。"""
    if exchange == "SSE":
        return f"sh{code}"
    if exchange == "SZSE":
        return f"sz{code}"
    if exchange == "BSE":
        return f"bj{code}"
    p = code[:1]
    return f"sh{code}" if p == "6" else f"bj{code}" if p in ("4", "8") else f"sz{code}"


def _dec(raw: object) -> Decimal | None:
    t = str(raw).strip()
    if t in {"", "-", "--", "None", "nan", "null"}:
        return None
    try:
        return Decimal(t)
    except (ArithmeticError, ValueError):
        return None


class SinaFlowSource:
    """新浪个股历史资金流源（HistorySource 形状：fetch_price_history 返资金流五档历史）。

    虽名为 price_history 接口签名，实际按 metric 归属 money_flow——上层回填 metric='money_flow'
    时选它。返回带真实 trade_date 的五档净额观测。
    """

    source_id = "sina_flow"
    adapter_version = "sina-moneyflow-v1"

    def __init__(self, *, http_get: Callable[..., object] | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        if http_get is None:
            from quantchive.datasource._http_client import default_http_get
            http_get = default_http_get()
        self._get = http_get
        if sleep is None:
            import time
            sleep = time.sleep
        self._sleep = sleep

    def fetch_flow_history(
        self, *, symbol: str, exchange: str | None,
        start_date: str, end_date: str, max_rows: int = 800,
    ) -> Sequence[RawObservation]:
        """取个股逐日历史资金流，过滤到 [start_date, end_date]，归一为五档净额观测。"""
        daima = secid_for_sina(symbol, exchange)
        try:
            resp = self._get(
                _SINA_URL, params={"page": 1, "num": max_rows, "sort": "opendate",
                                   "asc": 0, "daima": daima},
                headers=_SINA_HEADERS, timeout=15)
            status = getattr(resp, "status_code", 200)
            if status != 200:
                raise DataSourceError(f"HTTP {status}", error_type="rate_limited")
            data = resp.json()
        except DataSourceError:
            raise
        except Exception as exc:  # noqa: BLE001 → 结构化错误（供 failover）
            raise DataSourceError(
                f"新浪资金流失败: {type(exc).__name__}", error_type="rate_limited") from exc
        if not isinstance(data, list):
            raise DataSourceError("新浪返回非数组", error_type="empty_or_dash")
        out: list[RawObservation] = []
        for row in data:
            d = str(row.get("opendate") or "").strip()
            if not d or d < start_date or d > end_date:
                continue
            r0, r1 = _dec(row.get("r0_net")), _dec(row.get("r1_net"))
            r2, r3 = _dec(row.get("r2_net")), _dec(row.get("r3_net"))
            main = None if (r0 is None or r1 is None) else r0 + r1   # 主力=超大+大单
            out.append(RawObservation(
                source_symbol=symbol, display_name=symbol, asset_class=AssetClass.A_SHARE,
                level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK,
                caliber=Caliber.EASTMONEY,       # 口径同东财主力，复用标度
                main_net=main, super_large_net=r0, large_net=r1,
                medium_net=r2, small_net=r3,
                price=_dec(row.get("trade")),
                change_pct=(None if _dec(row.get("changeratio")) is None
                            else _dec(row.get("changeratio")) * 100),  # 比率→百分数
                exchange=exchange, source_unit=AmountUnit.YUAN, trade_date=d))
        return out
