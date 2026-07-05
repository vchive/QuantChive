"""T027: 百度校验探针（注入 fake page，解析四档 gross+net，不联网）（spec005 US4）。"""

from __future__ import annotations

import json
from decimal import Decimal

from quantchive.datasource.baidu_flow_src import BaiduFlowSource


class _FakePage:
    """假 page：evaluate 返固定百度 JSON，不联网。"""

    def __init__(self, bodies: dict[str, str]) -> None:
        self._bodies = bodies

    def goto(self, url, **kw):
        return None

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, expr, codes):
        return {c: {"status": 200, "body": self._bodies.get(c, "")} for c in codes}


def _baidu_body(super_in, super_out, super_net):
    """构造百度 fundFlowSpread 响应体。"""
    def grp(i, o, n):
        return {"turnoverIn": i, "turnoverOut": o, "netTurnover": n}
    return json.dumps({"Result": {"content": {"fundFlowSpread": {"result": {
        "superGrp": grp(super_in, super_out, super_net),
        "largeGrp": grp(100, 100, 0),
        "mediumGrp": grp(50, 50, 0),
        "littleGrp": grp(20, 20, 0),
    }}}}})


def test_probe_parses_four_tiers() -> None:
    bodies = {"600519": _baidu_body("1000", "600", "400")}
    src = BaiduFlowSource(page_factory=lambda: _FakePage(bodies))
    probes = src.probe(["600519"])
    assert len(probes) == 1
    p = probes[0]
    assert p.code == "600519"
    # 超大单：流入1000+流出600=成交额1600、净=400
    assert p.tiers["super_large"]["gross"] == Decimal("1600")
    assert p.tiers["super_large"]["net"] == Decimal("400")
    assert set(p.tiers) == {"super_large", "large", "medium", "small"}


def test_probe_skips_unparseable() -> None:
    """坏 body 的股跳过，不炸。"""
    bodies = {"600519": _baidu_body("1000", "600", "400"), "000001": "garbage"}
    src = BaiduFlowSource(page_factory=lambda: _FakePage(bodies))
    probes = src.probe(["600519", "000001"])
    assert [p.code for p in probes] == ["600519"]


def test_probe_no_network() -> None:
    """全程注入 fake page，不触真实 Playwright/网络。"""
    src = BaiduFlowSource(page_factory=lambda: _FakePage({}))
    assert src.probe(["600519"]) == []
