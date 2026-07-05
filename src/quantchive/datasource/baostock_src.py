"""baostock 历史行情源（spec003 D4，冷热分离核心）。

baostock 自有专用 API（非爬东财前端）、无 IP 限流、有前/后复权、日线深至 1990——
历史彻底离开东财高频路径。**无资金流**（能力诚实自描述）。

字段（实测 query_history_k_data_plus）：date/open/high/low/close/volume(股)/amount(元)/turn(%)/pctChg(%)。
归一：close→price_micro、pctChg→change_pct_bp、volume(股)÷100→手、amount→turnover_cents。禁 float。
baostock 模块可注入（宪章 IV，测试用 fake 不联网）；bs.login/logout 生命周期串行（单 worker）。
"""

from __future__ import annotations

import contextlib
import io
from decimal import Decimal
from typing import Sequence

from quantchive.core.logging import get_logger
from quantchive.core.money import AmountParseError
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


def _quiet(fn):
    """baostock login/logout 无条件 print 到 stdout——抑制，避免刷屏。"""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()

# adjust → baostock adjustflag（'1'后复权 '2'前复权 '3'不复权）
_ADJUST = {"none": "3", "qfq": "2", "hfq": "1"}
# frequency 映射（baostock: d/w/m/5/15/30/60）
_FREQ = {"daily": "d", "weekly": "w", "monthly": "m", "5min": "5", "1min": "5"}
_FIELDS = "date,code,open,high,low,close,volume,amount,turn,pctChg"


def secid_for_baostock(code: str, exchange: str | None) -> str:
    """baostock secid：沪 sh. / 深 sz. / 北 bj.（按交易所或代码前缀）。"""
    if exchange == "SSE":
        return f"sh.{code}"
    if exchange == "SZSE":
        return f"sz.{code}"
    if exchange == "BSE":
        return f"bj.{code}"
    # 无 exchange 时按代码前缀兜底（6=沪，0/3=深，4/8=北）
    p = code[:1]
    return f"sh.{code}" if p == "6" else f"bj.{code}" if p in ("4", "8") else f"sz.{code}"


def _num_or_none(raw: object) -> Decimal | None:
    text = str(raw).strip()
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    try:
        return Decimal(text)
    except (ArithmeticError, ValueError):
        raise AmountParseError(f"non-numeric: {text!r}")


class BaostockSource:
    """baostock 历史行情源（HistorySource）。无资金流。"""

    source_id = "baostock"
    adapter_version = "baostock-v1"

    def __init__(self, *, bs_module=None) -> None:
        # bs_module 可注入（测试用 fake，不联网）；默认真实 baostock
        if bs_module is None:
            import baostock as bs_module
        self._bs = bs_module
        self._in_session = False        # 会话内复用登录，避免每股 login/logout

    @contextlib.contextmanager
    def session(self):
        """整批回填登录一次：`with src.session(): ...`——省去每股 login/logout（刷屏+慢）。"""
        _quiet(self._bs.login)
        self._in_session = True
        try:
            yield self
        finally:
            self._in_session = False
            _quiet(self._bs.logout)

    def fetch_price_history(
        self, *, symbol: str, exchange: str | None,
        start_date: str, end_date: str, granularity: str = "daily",
        adjust: str = "qfq",
    ) -> Sequence[RawObservation]:
        """取 [start_date, end_date] 历史行情，归一为带真实 trade_date 的日终观测。"""
        secid = secid_for_baostock(symbol, exchange)
        freq = _FREQ.get(granularity, "d")
        adjustflag = _ADJUST.get(adjust, "2")
        own = not self._in_session        # 会话外单次调用才自管登录（向后兼容）
        if own:
            _quiet(self._bs.login)
        try:
            rs = self._bs.query_history_k_data_plus(
                secid, _FIELDS, start_date=start_date, end_date=end_date,
                frequency=freq, adjustflag=adjustflag)
            if getattr(rs, "error_code", "0") != "0":
                raise DataSourceError(
                    f"baostock 错误 {rs.error_code}: {rs.error_msg}", error_type="schema_drift")
            rows: list[list[str]] = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
        finally:
            if own:
                _quiet(self._bs.logout)
        return [o for o in (self._row_to_obs(r, symbol, exchange) for r in rows) if o is not None]

    def _row_to_obs(self, row, symbol, exchange) -> RawObservation | None:
        # row = [date, code, open, high, low, close, volume, amount, turn, pctChg]
        if len(row) < 10 or not row[0]:
            return None
        try:
            close = _num_or_none(row[5])
            volume_shares = _num_or_none(row[6])
            amount = _num_or_none(row[7])
            pct = _num_or_none(row[9])
        except AmountParseError:
            return None
        if close is None and volume_shares is None:
            return None  # 空壳（停牌日无量价）跳过
        # 量归一：baostock 股 ÷100 → 手（与东财口径一致）
        vol_hands = int(volume_shares / Decimal(100)) if volume_shares is not None else None
        return RawObservation(
            source_symbol=symbol, display_name=symbol, asset_class=AssetClass.A_SHARE,
            level=SubjectLevel.INSTRUMENT, subject_kind=SubjectKind.STOCK, caliber=Caliber.EASTMONEY,
            # 无资金流五档（baostock 不提供）
            price=close, change_pct=pct, volume=Decimal(vol_hands) if vol_hands is not None else None,
            turnover=amount, exchange=exchange, source_unit=AmountUnit.YUAN,
            trade_date=row[0],   # 真实交易日，回填层据此写 daily_final
        )
