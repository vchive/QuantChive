"""T042: ETF circ_mktcap(f21) 命名不冒充 aum（诚实自描述）。"""

from __future__ import annotations

from decimal import Decimal

from quantchive.datasource.base import FetchSpec
from quantchive.datasource.em_etf_src import EastMoneyEtfSource
from quantchive.models.enums import AssetClass, SortField, SubjectKind, SubjectLevel


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._p = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._p


def _spec() -> FetchSpec:
    return FetchSpec(fs="b:MK0021", level=SubjectLevel.INSTRUMENT,
                     asset_class=AssetClass.FUND_ETF, subject_kind=SubjectKind.ETF)


def test_etf_f21_named_circ_mktcap_not_aum() -> None:
    rows = [{"f12": "512880", "f14": "证券ETF", "f2": 1.109, "f3": 2.35,
             "f5": 123456, "f6": 9990000, "f21": 8500000000}]
    src = EastMoneyEtfSource(http_get=lambda *a, **k: _Resp({"data": {"diff": rows}}))
    obs = src.fetch_observations(_spec())
    assert len(obs) == 1
    o = obs[0]
    # f21 进 metrics 用键名 circ_mktcap，绝不叫 aum/规模（口径不同，不冒充）
    assert "circ_mktcap" in o.metrics
    assert "aum" not in o.metrics
    assert o.metrics["circ_mktcap"] == Decimal("8500000000")


def test_etf_has_no_five_tier() -> None:
    rows = [{"f12": "512880", "f14": "证券ETF", "f2": 1.109, "f3": 2.35, "f5": 1, "f6": 1, "f21": 1}]
    src = EastMoneyEtfSource(http_get=lambda *a, **k: _Resp({"data": {"diff": rows}}))
    o = src.fetch_observations(_spec())[0]
    # ETF 无五档资金流
    assert o.main_net is None and o.super_large_net is None
    assert o.price == Decimal("1.109")


def test_etf_capability_honest() -> None:
    src = EastMoneyEtfSource(http_get=lambda *a, **k: _Resp({"data": {"diff": []}}))
    cap = src.capability(_spec())
    assert cap.has_five_tier is False
    assert "circ_mktcap" in cap.supported_metrics
    assert SortField.MAIN_NET not in cap.available_sort_fields   # 无五档排序
    assert SortField.CHANGE_PCT in cap.available_sort_fields
    assert "aum" not in cap.notes.lower() or "非" in cap.notes    # 明示非 aum
