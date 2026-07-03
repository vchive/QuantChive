# Contract: Service 查询层 + API 端点

**Feature**: 002 | FR-017 人/agent 共用 | 决策见 [research.md](../research.md)

## QueryService（只读，人/agent 共用）(`service/query_service.py`)

```python
def get_market_overview(*, asset_class=A_SHARE, trade_date=None, as_of=None) -> MarketOverviewResult
def get_sector_ranking(*, caliber, sector_type, sort_by=MAIN_NET, top_n=10, full=False, trade_date=None, as_of=None) -> RankingResult
def get_stocks_in_sector(*, sector_subject_id, sort_by=MAIN_NET, top_n=20, full=False, trade_date=None, as_of=None) -> RankingResult
    # as_of<today 且无成分记录 → 抛 OutOfWindow(不静默用当前成分, 无前视)
def get_etf_ranking(*, sort_by=CHANGE_PCT, top_n=20, full=False, trade_date=None) -> RankingResult
def get_subject_series(*, subject_id, metric=MAIN_NET, trade_date=None) -> SubjectSeriesResult  # 排除 intraday_latest
def get_capability(*, asset_class, subject_kind, caliber) -> SubjectCapability
def health() -> HealthResult
```

**RankingResult 双榜/单榜 mode**（对抗审查最高 severity 裁定）：
- `mode='bipolar'`（main_net/change_pct 有正负）→ top_inflow/top_outflow 双榜（>0/<0 切分）。
- `mode='unipolar'`（price/volume 恒正）→ ranked_items 单榜。
- **PRICE/VOLUME 不进双榜 SortField 路径**；available_sort_fields 按 (asset_class, subject_kind) 隔离，板块榜拒绝价格排序（防 KeyError 打空板块双榜、破 SC-010）。

## FundLookupService（独立旁路）(`service/fund_lookup_service.py`)

```python
def get_fund_nav(*, fund_code, days=60) -> FundNavResult   # 实时 lsjz, 不入库, note="not persisted"
```
物理隔离：不持 dao 写句柄、不进调度 JOBS。

## 结构化错误 (`service/errors.py`)

| 异常 | code | HTTP |
|---|---|---|
| SubjectNotFound | SUBJECT_NOT_FOUND | 404 |
| AssetClassNotProvisioned | ASSET_CLASS_NOT_PROVISIONED | 422 |
| OutOfWindow | OUT_OF_WINDOW | 422 |
| UnsupportedMetric | UNSUPPORTED_METRIC | 422 |
| NoDataForDate | NO_DATA_FOR_DATE | 404 |

Service 从不裸抛内置异常。统一响应体 `{"error":{code,message,detail}}`。

## FastAPI 端点（本期只读）

| 端点 | 入参 | response_model | US |
|---|---|---|---|
| GET /api/market/overview | asset_class=a_share, trade_date? | MarketOverviewResult | US1 |
| GET /api/sectors/ranking | caliber, sector_type, sort_by=main_net, top_n(1-100)=10, full, trade_date? | RankingResult(bipolar) | US2 |
| GET /api/sectors/{id}/stocks | sort_by=main_net, top_n=20, full, trade_date?, as_of? | RankingResult | US3 |
| GET /api/etf/ranking | sort_by=change_pct, top_n=20, full, trade_date? | RankingResult(unipolar) | US4 |
| GET /api/funds/{code}/nav | days(1-365)=60 | FundNavResult(旁路,不入库) | US4 |
| GET /api/subjects/{id}/series | metric=main_net, trade_date? | SubjectSeriesResult | US5 |
| GET /api/meta/capability | asset_class, subject_kind, caliber | SubjectCapability | FR-004 |
| GET /api/meta/subjects | asset_class?, subject_kind? | list[SubjectRef] | 下钻寻址 |
| GET /api/health | — | HealthResult | 运维 |

薄路由，业务全在 Service。金额序列化为字符串。跨品种不可混比：无混合 asset_class 排行端点。

## agent-ready（FR-017/018）

人端路由与未来 agent Tool 消费同一 QueryService。只读(QueryService) 与动作(ActionService, 本期空 docstring 占位) 物理分层，出口层不挂 ActionService，human-in-the-loop 拦在 action 层入口。
