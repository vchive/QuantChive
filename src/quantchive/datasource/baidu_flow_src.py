"""百度资金流校验探针（spec005 US4）。当日四档 gross+流入流出，独立后端。

定位=校验探针（非数据主力）：收盘后随机抽样几十只跟新浪对比。
反爬：Playwright 无头浏览器 page.evaluate 内 fetch（百度 JS 自动带 Acs-Token）。
可测：page_factory 可注入 fake page（evaluate 返固定 JSON），不联网（宪章 IV）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Protocol


class _Page(Protocol):
    def goto(self, url: str, **kw: object) -> object: ...
    def wait_for_timeout(self, ms: int) -> object: ...
    def evaluate(self, expr: str, arg: object) -> object: ...


_FETCH_JS = """async (codes) => {
  const out = {};
  for (const code of codes) {
    try {
      const u = 'https://finance.pae.baidu.com/vapi/v1/fundflow?finance_type=stock&type=stock&market=ab&code='
        + code + '&belongs=stocklevelone&finClientType=pc';
      const r = await fetch(u, {headers: {'Referer': 'https://finance.baidu.com/'}});
      out[code] = {status: r.status, body: r.status === 200 ? await r.text() : ''};
    } catch (e) { out[code] = {err: String(e)}; }
    await new Promise(s => setTimeout(s, 700));
  }
  return out;
}"""

# 百度四档 → 内部档位名
_GRP = {"superGrp": "super_large", "largeGrp": "large",
        "mediumGrp": "medium", "littleGrp": "small"}


@dataclass(frozen=True)
class BaiduTierProbe:
    """单股当日四档 gross+net（元 Decimal），供跨源校验。"""
    code: str
    tiers: dict[str, dict[str, Decimal]]   # {'super_large': {'gross':.., 'net':..}, ...}


def _parse(body: str) -> dict[str, dict[str, Decimal]] | None:
    try:
        result = json.loads(body)["Result"]["content"]["fundFlowSpread"]["result"]
    except (KeyError, ValueError, TypeError):
        return None
    out: dict[str, dict[str, Decimal]] = {}
    for grp, tier in _GRP.items():
        g = result.get(grp)
        if not g:
            return None
        try:
            inflow = Decimal(str(g["turnoverIn"]))
            outflow = Decimal(str(g["turnoverOut"]))
            net = Decimal(str(g["netTurnover"]))
        except (KeyError, ValueError, TypeError):
            return None
        out[tier] = {"gross": inflow + outflow, "net": net}
    return out


class BaiduFlowSource:
    """百度校验探针。page_factory() → 一个已就绪的 page（真实用 Playwright，测试注入 fake）。"""

    def __init__(self, page_factory: Callable[[], _Page]) -> None:
        self._page_factory = page_factory

    def probe(self, codes: list[str]) -> list[BaiduTierProbe]:
        """一次浏览器，page.evaluate 内连续 fetch 多股，解析四档。不可解析的股跳过。"""
        page = self._page_factory()
        page.goto("https://gushitong.baidu.com/stock/ab-" + (codes[0] if codes else "600519"),
                  wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(3000)
        raw = page.evaluate(_FETCH_JS, codes)
        out: list[BaiduTierProbe] = []
        for code in codes:
            r = raw.get(code) or {}
            if r.get("status") != 200 or not r.get("body"):
                continue
            tiers = _parse(r["body"])
            if tiers is not None:
                out.append(BaiduTierProbe(code=code, tiers=tiers))
        return out
