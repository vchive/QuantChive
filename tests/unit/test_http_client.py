"""T011: HTTP 客户端——退避抖动、is_degraded、缓存工厂、EmClient 页间节流（spec003 US1）。"""

from __future__ import annotations

import requests

from quantchive.datasource._em_client import EmClient
from quantchive.datasource._http_client import (
    backoff_delay,
    build_cached_session,
    is_degraded,
)


# ---- 退避抖动（D1/FR-002）----

def test_backoff_delay_has_jitter() -> None:
    # 注入固定 rng 返回抖动上限 → 退避 = 基数 + 抖动项
    d0 = backoff_delay(0, base=3.0, cap=20.0, jitter=1.5, rng=lambda a, b: b)
    assert d0 == 3.0 + 1.5                     # min(3*1,20)+1.5
    d1 = backoff_delay(1, base=3.0, cap=20.0, jitter=1.5, rng=lambda a, b: b)
    assert d1 == 6.0 + 1.5                     # min(3*2,20)+1.5
    # 封顶
    d9 = backoff_delay(9, base=3.0, cap=20.0, jitter=1.5, rng=lambda a, b: 0.0)
    assert d9 == 20.0


def test_backoff_jitter_varies() -> None:
    # 不同抖动值 → 退避不恒等（打散指纹）
    seq = iter([0.1, 0.9])
    a = backoff_delay(0, rng=lambda lo, hi: next(seq))
    b = backoff_delay(0, rng=lambda lo, hi: next(seq))
    assert a != b


# ---- 限流降级识别（D3/FR-005/SC-001）----

def test_is_degraded() -> None:
    assert is_degraded(got=1, expected_min=30) is True       # 请求30却回1 → 降级
    assert is_degraded(got=30, expected_min=30) is False      # 达标
    assert is_degraded(got=45, expected_min=30) is False      # 超额
    assert is_degraded(got=1, expected_min=0) is False        # 全集未知 → 不判降级


# ---- 缓存工厂（D2/FR-003/004）----

def test_build_cached_session_policy() -> None:
    s = build_cached_session(historical_ttl=3600, realtime_ttl=0, backend="memory",
                             cache_name="test_policy")
    exp = s.settings.urls_expire_after
    # 历史端点长 TTL、实时端点不缓存（差异化，FR-004）
    assert exp["*push2his*"] == 3600
    assert exp["*baostock*"] == 3600
    from requests_cache import DO_NOT_CACHE
    assert exp["*push2delay*"] == DO_NOT_CACHE


class _CountingAdapter(requests.adapters.HTTPAdapter):
    """计数发送 + 返回罐装 200（含 urllib3 raw，供 requests-cache 缓存），验命中不联网。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def send(self, request, **kw):
        import io

        from urllib3 import HTTPResponse
        self.calls += 1
        body = b'{"data":{"total":1,"diff":[{"f12":"x"}]}}'
        raw = HTTPResponse(
            body=io.BytesIO(body), status=200, preload_content=False,
            headers={"Content-Type": "application/json"}, request_url=request.url)
        return self.build_response(request, raw)


def test_cache_hit_historical_not_refetched() -> None:
    """历史端点二次请求命中缓存，底层 transport 只被打一次（FR-003）。"""
    s = build_cached_session(historical_ttl=3600, realtime_ttl=0, backend="memory",
                             cache_name="test_hit")
    adapter = _CountingAdapter()
    s.mount("https://push2his.eastmoney.com", adapter)
    url = "https://push2his.eastmoney.com/api/qt/stock/x"
    s.get(url, params={"a": "1"})
    s.get(url, params={"a": "1"})              # 同 URL 二次
    assert adapter.calls == 1                   # 二次命中缓存，未再打 transport


# ---- EmClient 页间节流（D1/FR-001）----

class _Resp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p


def test_em_client_sleeps_between_pages() -> None:
    """翻页 total=250 需 3 页 → 页间 sleep 被调用 2 次（相邻请求间节流）。"""
    total = 250
    rows = [{"f12": f"{i}", "f14": f"n{i}"} for i in range(total)]

    def get(url, params=None, **k):
        pn, pz = int(params["pn"]), int(params["pz"])
        return _Resp({"data": {"total": total, "diff": rows[(pn - 1) * pz: pn * pz]}})

    sleeps: list[float] = []
    c = EmClient(http_get=get, page_size=100, sleep=lambda s: sleeps.append(s),
                 rng=lambda lo, hi: 0.7)   # 固定节流 0.7
    got, tot = c.fetch_with_total(fs="m:90 t:2", fields="f12,f14")
    assert len(got) == 250 and tot == 250
    assert sleeps == [0.7, 0.7]                 # 3 页 → 页间 sleep 2 次，每次 0.7
