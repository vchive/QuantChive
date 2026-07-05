"""T018: baostock 历史行情源契约（fake baostock 模块，不联网）。"""

from __future__ import annotations

from decimal import Decimal

from quantchive.datasource.baostock_src import BaostockSource, secid_for_baostock
from quantchive.models.enums import AssetClass, SubjectKind, SubjectLevel


class _FakeRs:
    """模拟 baostock query 结果集（实测字段序）。"""

    fields = "date,code,open,high,low,close,volume,amount,turn,pctChg"

    def __init__(self, rows, error_code="0", error_msg="ok") -> None:
        self._rows = rows
        self._i = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self) -> bool:
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


class _FakeBs:
    """可注入 fake baostock 模块。"""

    def __init__(self, rows, error_code="0") -> None:
        self._rows = rows
        self._error_code = error_code
        self.logged_in = False
        self.query_args = None

    def login(self):
        self.logged_in = True
        return type("R", (), {"error_code": "0", "error_msg": "ok"})()

    def logout(self):
        self.logged_in = False
        return type("R", (), {"error_code": "0"})()

    def query_history_k_data_plus(self, secid, fields, **kw):
        self.query_args = {"secid": secid, "fields": fields, **kw}
        return _FakeRs(self._rows, error_code=self._error_code)


_ROWS = [
    # date,code,open,high,low,close,volume(股),amount(元),turn,pctChg
    ["2026-07-01", "sz.000725", "3.97", "4.02", "3.93", "3.97", "583140390", "2340737070.46", "1.60", "2.55"],
    ["2026-07-02", "sz.000725", "3.98", "4.10", "3.95", "4.05", "600000000", "2400000000.00", "1.70", "2.01"],
    ["2026-07-03", "sz.000725", "8.86", "9.09", "8.31", "8.38", "4144206667", "35597368805.43", "11.71", "-7.91"],
]


def test_secid_mapping() -> None:
    assert secid_for_baostock("000725", "SZSE") == "sz.000725"
    assert secid_for_baostock("600519", "SSE") == "sh.600519"
    assert secid_for_baostock("830799", "BSE") == "bj.830799"
    assert secid_for_baostock("600000", None) == "sh.600000"     # 前缀兜底


def test_fetch_price_history_normalized() -> None:
    bs = _FakeBs(_ROWS)
    src = BaostockSource(bs_module=bs)
    obs = src.fetch_price_history(
        symbol="000725", exchange="SZSE", start_date="2026-07-01", end_date="2026-07-03")
    assert len(obs) == 3
    o = obs[2]                                    # 07-03
    assert o.trade_date == "2026-07-03"
    assert o.price == Decimal("8.38")             # close
    assert o.change_pct == Decimal("-7.91")       # pctChg
    assert o.volume == Decimal(4144206667 // 100)  # 股÷100→手
    assert o.turnover == Decimal("35597368805.43")
    assert o.main_net is None                      # baostock 无资金流（诚实）
    assert o.level == SubjectLevel.INSTRUMENT and o.subject_kind == SubjectKind.STOCK
    assert o.asset_class == AssetClass.A_SHARE
    # secid + 复权口径正确传入
    assert bs.query_args["secid"] == "sz.000725"
    assert bs.query_args["adjustflag"] == "2"      # 默认 qfq 前复权
    assert bs.logged_in is False                   # 用后 logout


def test_login_logout_lifecycle() -> None:
    bs = _FakeBs(_ROWS)
    BaostockSource(bs_module=bs).fetch_price_history(
        symbol="000725", exchange="SZSE", start_date="2026-07-01", end_date="2026-07-03")
    assert bs.logged_in is False                   # 生命周期闭合


def test_error_raises() -> None:
    import pytest

    from quantchive.datasource.base import DataSourceError
    bs = _FakeBs([], error_code="10001")
    with pytest.raises(DataSourceError):
        BaostockSource(bs_module=bs).fetch_price_history(
            symbol="x", exchange="SSE", start_date="2026-07-01", end_date="2026-07-03")
