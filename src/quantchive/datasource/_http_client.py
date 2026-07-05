"""通用取数底座（spec003 D1/D2/D3 · 限流治理 + 缓存 + 降级识别）。

- backoff_delay：指数退避 + 随机抖动（打散重试指纹，D1）。
- is_degraded：限流降级识别（请求全量却只回极少条，D3/零静默降级硬门）。
- build_cached_session：requests-cache 透明缓存工厂，按端点 glob 差异化 TTL（D2）——
  历史/日终(push2his/baostock)长 TTL、实时(push2delay)不缓存。
纯函数 + 工厂，均可 mock 测不联网（宪章 IV）。
"""

from __future__ import annotations

import random
from functools import lru_cache
from typing import Callable

# 端点分类（缓存/节流策略分档，FR-004）
HISTORICAL_URL_GLOBS = ("*push2his*", "*baostock*", "*fflow*")
REALTIME_URL_GLOBS = ("*push2delay*", "*push2.eastmoney*")


def backoff_delay(
    attempt: int, *, base: float = 3.0, cap: float = 20.0, jitter: float = 1.5,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    """第 attempt 次重试的退避时长 = min(base*2**attempt, cap) + 随机抖动[0,jitter]。

    抖动打散并发/重试指纹（D1）。注入固定 rng 可在测试断言含抖动项。
    """
    return min(base * (2 ** attempt), cap) + rng(0, jitter)


def is_degraded(*, got: int, expected_min: int) -> bool:
    """限流降级识别：请求全量却只回 got < expected_min 条 → True（D3）。

    expected_min<=0（未知全集）时不判降级（交由上层的 min_days_guard/coverage 处理）。
    纯函数，零静默降级硬门（SC-001）的可测核心。
    """
    return expected_min > 0 and got < expected_min


def build_cached_session(
    *, historical_ttl: int = 86400, realtime_ttl: int = 0,
    backend: str = "sqlite", cache_name: str = "quantchive_http",
):
    """构造 requests-cache CachedSession，按端点差异化 TTL（D2/FR-003/004）。

    - 历史/日终端点（push2his/baostock/fflow）→ historical_ttl（收盘不变，长缓存）。
    - 实时端点（push2delay）→ realtime_ttl；0 表不缓存（DO_NOT_CACHE）。
    返回的 session.get 可注入进各 client 的 http_get（宪章 IV）。测试用 backend='memory'。
    **勿用全局 install_cache**（与 akshare 争 Session）——用此显式实例。
    """
    from requests_cache import DO_NOT_CACHE, CachedSession

    rt = realtime_ttl if realtime_ttl > 0 else DO_NOT_CACHE
    urls_expire_after: dict[str, object] = {}
    for g in HISTORICAL_URL_GLOBS:
        urls_expire_after[g] = historical_ttl
    for g in REALTIME_URL_GLOBS:
        urls_expire_after[g] = rt
    urls_expire_after["*"] = rt   # 兜底：未分类端点默认按实时（保守，不误缓存）
    return CachedSession(
        cache_name=cache_name, backend=backend,
        urls_expire_after=urls_expire_after, allowable_methods=("GET",),
    )


@lru_cache
def default_http_get():
    """生产用共享 http_get：按 settings 注入 requests-cache CachedSession（缓存落 data/ 旁）。

    cache_enabled=False 时回落裸 requests.get。lru_cache 保单例（一个 sqlite 缓存文件）。
    源构造时传入此值即获透明缓存（历史长 TTL、实时不缓存）。
    """
    from quantchive.core.settings import get_settings
    s = get_settings()
    if not s.cache_enabled:
        import requests
        return requests.get
    from pathlib import Path
    cache_path = str(Path(s.db_path).with_name("http_cache"))
    return build_cached_session(
        historical_ttl=s.cache_ttl_historical, realtime_ttl=s.cache_ttl_realtime,
        backend="sqlite", cache_name=cache_path).get
