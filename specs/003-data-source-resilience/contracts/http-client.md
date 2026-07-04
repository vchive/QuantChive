# Contract: HTTP 客户端（节流 / 退避 / 缓存）

**Feature**: 003 | 决策 D1/D2/D3 见 [research.md](../research.md)

## `_http_client.py` · 通用取数底座（泛化自 _em_client）

```python
class HttpClient:
    def __init__(
        self, *,
        http_get: Callable[..., object] | None = None,   # 可注入（宪章 IV）；默认 requests.get 或 CachedSession.get
        sleep: Callable[[float], None] = time.sleep,      # 可注入
        rng: Callable[[float, float], float] = random.uniform,  # 可注入以确定化测试
        page_sleep: tuple[float, float] = (0.5, 1.5),     # 页间随机等待区间（D1）
        backoff_base: float = 3.0, backoff_cap: float = 20.0,
        backoff_jitter: float = 1.5,                      # 退避抖动（D1）
        max_retries: int = 3, timeout: float = 15.0,
    ): ...

    def get(self, url, *, params, headers, cache_policy: str = "realtime") -> Response: ...
    # cache_policy ∈ {'realtime'(短/不缓存), 'historical'(长TTL)} → 决定注入的 session TTL 分档
```

**行为契约（可 mock 测，不联网）**：
- 翻页/多请求循环中，**相邻请求间**调用 `sleep(rng(*page_sleep))`（US1 AC1）。
- 重试退避 = `min(base*2**attempt, cap) + rng(0, jitter)`；**同 attempt 多次退避时长不恒等**（US1 AC2，注入固定 rng 可断言含抖动项）。
- 非 200 → `DataSourceError(error_type='rate_limited')`；沿用双域名 fallback。
- `cache_policy='historical'` 的 URL 二次请求命中缓存、不发真实请求（US1 AC3，注入内存后端 CachedSession 断言 http_get 未被二次调用）。

## `_em_client.py` · 改造（薄壳委托，迁移铁律）

- `EmClient` 保留现有对外签名（现有源不破，144 测试不改）；内部翻页循环加页间 sleep、退避加 jitter，或委托 `HttpClient`。
- 现有注入点 `http_get`/`sleep` 不变——测试可注入内存 CachedSession 验缓存、注入固定 rng 验抖动。

## 限流降级识别（D3，US1 AC4 / SC-001）

```python
def is_degraded(*, got: int, expected_min: int) -> bool: ...
# 请求全量却只回 got < expected_min 条 → True（限流降级）
```
- 采集/回填据此：降级 → 不落残缺数据、`ingestion_run.degraded=1`、审计记原因（**零静默降级硬门**）。
- 泛化 spec002 backfill 已有的 `min_days_guard`。纯函数，mock 精确可测。

## 缓存注入（D2）

- 生产：`http_get = requests_cache.CachedSession(backend='sqlite', urls_expire_after={...}).get`。
- 测试：`http_get = requests_cache.CachedSession(backend='memory', ...).get` 或 fake 计数器 http_get（断言二次不打）。
- **禁** `install_cache()` 全局补丁（与 akshare 争 Session）——用显式实例。
