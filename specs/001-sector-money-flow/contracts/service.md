# Contract: Service 查询层（人/agent 共用业务真源）

**Feature**: 001-sector-money-flow | FR-012 单一业务能力 | 决策见 [research.md](../research.md)

## QueryService (`service/query_service.py`)

QueryService **不持有 live source**；口径能力从 `data_source` 表 + 静态声明读。本期全部为只读查询。

```python
def get_ranking(caliber, sector_type, sort_by=SortField.MAIN_NET, top_n=10,
                full=False, trade_date=None, as_of=None) -> SectorRankingResult
    # 过去交易日→取 daily_final; 当日→最新 intraday_snapshot
    # sort_by 不被该口径支持→ MetricNotSupported; 排序恒加 tiebreaker sector_id ASC
    # 双榜方向: top_inflow / top_outflow 恒按 sort_by 字段符号与大小(最负 N 个=流出榜)

def get_sector_series(caliber, sector_name, trade_date=None) -> SectorSeriesResult
    # 超保留窗口→ OutOfRetentionWindow; 断点 net_amount=None 如实呈现 + gap_count
    # 默认只取 intraday

def get_caliber_meta(caliber) -> CaliberMeta
def list_calibers() -> list[CaliberMeta]
def list_sectors(caliber, sector_type) -> list[str]
```

入参纯值类型、出参纯 Pydantic、错误结构化——人端路由与未来 agent Tool 消费**同一实例**（FR-012）。

## 对外 Pydantic 模型 (`service/dto.py`)

- **SectorFlowItem**：`sector_name, caliber, sector_type, net_amount:Decimal, main_net/xl/lg/md/sm:Decimal|None`（None=不提供，非 0）。
- **DataProvenance**：`trade_date, source_type(INTRADAY_SNAPSHOT/DAILY_FINAL), source_id, captured_at, is_stale, is_approximate_final`（同花顺日终=True）。`is_stale` 由 `(trade_date, as_of, calendar)` 确定性派生。
- **SectorRankingResult**：`caliber, sort_by, provenance, top_inflow[], top_outflow[], all_items[]|None, total_sectors, has_five_tier`。
- **MinutePoint**：`ts, net_amount:Decimal|None(断点), main_net:Decimal|None, source_type`。
- **SectorSeriesResult**：`sector_name, caliber, trade_date, provenance, points[], gap_count`。
- **CaliberMeta**：`caliber, has_five_tier, has_daily_final, supported_sector_types[], provided_metrics[], available_sort_fields[]`（同花顺不含各单档排序字段；`provided_metrics` 为单一真源，其余派生）。
- **HealthResult**：`status, latest_trade_date, last_run, per_caliber_last_run`。
- 金额 `Decimal` → Pydantic v2 JSON **序列化为字符串**（已验证 2.13.4 行为），前端不进 JS number（SC-006）；契约测试锁定此序列化。

## 结构化错误 (`service/errors.py`)

```python
class QueryError(Exception): code: str; message: str; detail: dict | None
```

| 异常 | code | HTTP |
|---|---|---|
| `CaliberNotAvailable` | `CALIBER_DATA_UNAVAILABLE` | 503 |
| `MetricNotSupported` | `METRIC_NOT_SUPPORTED` | 422 |
| `OutOfRetentionWindow` | `OUT_OF_RETENTION_WINDOW` | 422 |
| `SectorNotFound` | `SECTOR_NOT_FOUND` | 404 |
| `NoDataForDate` | `NO_DATA_FOR_DATE` | 404 |

统一响应体 `{"error":{"code","message","detail"}}`；Tool 出口同源也带 `message`。Service **从不裸抛内置异常**（FR-007/SC-008）。

## agent-ready（FR-012 / FR-013 具体化）

- 本期不实现 agent。未来把 QueryService 方法包成 LangChain Tool 只需：方法已是纯业务 + 结构化 IO，Tool 层薄封装即可。
- **只读 vs 动作分层**：本期只有 `QueryService`（只读）。未来动作型能力放**独立** `action_service.py`（本期不创建），human-in-the-loop 人工确认拦在 action 层入口——只读查询永不经过确认，动作永远经过（结构上分离）。
