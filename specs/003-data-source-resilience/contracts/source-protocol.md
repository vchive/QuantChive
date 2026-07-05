# Contract: 数据源 Protocol + 选源工厂 + 路由 + 归一

**Feature**: 003 | 决策 D4/D6/D7 见 [research.md](../research.md)

## 现有 ObservationSource（不改，SC-008 seam 已就位）

沿用 `datasource/base.py` 的 `ObservationSource` Protocol（`source_id` / `capability` / `fetch_observations` / `fetch_members` / `fetch_daily_final`）。新源实现它即可被上层零改动消费。

## HistorySource Protocol（新，历史行情，D4）

```python
@runtime_checkable
class HistorySource(Protocol):
    source_id: str
    def fetch_price_history(
        self, *, symbol: str, exchange: str | None,
        start_date: str, end_date: str, granularity: str = "daily",
        adjust: str = "none",   # 'none'|'qfq'(前复权)|'hfq'(后复权)
    ) -> Sequence[RawObservation]: ...   # 归一后的日终观测（价/量/额），带真实 trade_date
```

**baostock_src.BaostockSource 契约**：
- 实现 `HistorySource`；`bs.login()` 用前调用、用后 `bs.logout()`（生命周期管理，可注入 fake baostock 模块测试不联网）。
- 只提供价/量/额历史（**无资金流**——capability 诚实自描述）。
- 复权口径经 `adjust` 参数，历史价标注复权类型（不与不复权混存）。

## ThsFlowSource 契约（同花顺资金流备源，D6）

- 走 akshare `stock_fund_flow.py` 那组（`stock_fund_flow_individual/concept/industry`）。
- **接入第一步 = hexin-v 头小流量验证**（Phase C 首 task）：可稳定生成 → `data_source.is_active=1` 注册为资金流备源；否则 `is_active=0` 记审计（优雅降级，不阻塞）。
- 归一为 `RawObservation`（金额 to_cents 等）。

## 选源工厂（registry.py，D7）

```python
def get_source(source_id: str) -> ObservationSource | HistorySource: ...
# 已注册 → 返回实例；未注册/缺失依赖 → 返回 no-op 兜底源（不崩、log warning、fetch 返回空）
def get_history_source(source_id: str) -> HistorySource: ...
```
- 仿 vnpy `get_datafeed`：**鸭子类型基类兜底**（misconfig 返回空数据不抛，US3 AC3），非强制 ABC。

## 源路由 + 主备切换（routing.py，D7）

```python
METRIC_SOURCE_ROUTES: dict[str, list[str]]   # 见 data-model §4
def resolve_sources(metric_kind: str) -> list[str]: ...   # [主, 备...]

def fetch_with_failover(metric_kind, fetch_fn) -> tuple[result, used_source_code]: ...
# 依次试主→备；主源 DataSourceError(rate_limited/timeout) → 切下一个；返回结果 + 实际用源
```
**行为契约**：
- 主源限流/失败 → 自动切备源，成功产出（US3 AC1）；`ingestion_run.used_source_code` 记实际用源。
- 全部源失败 → 抛错 + 记审计（不静默返空当「无数据」，Edge Case）。
- 用两个 fake 源（主抛限流、备正常）mock 测切换；用全新 fake 源验换源不改上层（SC-005）。

## 归一层（normalize.py，D7/FR-015/SC-007）

```python
def normalize(raw_rows, *, source_code, metric_kind) -> Sequence[RawObservation]: ...
# 各源异构字段/单位/复权 → 内部约定（金额整数分、价×1e6、量统一、复权标注）
```
- 输出统一 `RawObservation`（不新增观测 DTO）——下游 DAO/查询零改动。
- 跨源同主体同指标数值口径一致（SC-007 抽样核对）；禁 float 参与货币。
