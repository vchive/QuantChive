# Contract: FastAPI 端点（本期全只读）

**Feature**: 001-sector-money-flow | 决策 D9 见 [research.md](../research.md)

## 端点清单

| 路径 | 方法 | 入参 | 响应模型 |
|---|---|---|---|
| `/api/sectors/ranking` | GET | `caliber, sector_type, sort_by=main_net, top_n=10, full=false, trade_date?` (query) | `SectorRankingResult` |
| `/api/sectors/{sector_name}/series` | GET | path `sector_name`; query `caliber, trade_date?` | `SectorSeriesResult` |
| `/api/calibers` | GET | — | `list[CaliberMeta]` |
| `/api/calibers/{caliber}` | GET | path `caliber` | `CaliberMeta` |
| `/api/sectors` | GET | `caliber, sector_type` | `list[str]` |
| `/api/health` | GET | — | `HealthResult` |

## 路由规则

- **薄路由**：Pydantic 校验入参 → 调 QueryService → 统一 `exception_handler` 映射 `QueryError.code → HTTP`。路由内**无 SQL / 排序 / 聚合**。
- 所有金额字段序列化为**字符串**（Decimal，SC-006）。
- 错误响应统一体：`{"error":{"code","message","detail"}}`。

## 端点 → 需求映射

| 端点 | 覆盖需求 |
|---|---|
| `/api/sectors/ranking` | FR-004(排行/排序/双榜/全量) · US1 · US2 |
| `/api/sectors/{name}/series` | FR-006/007/008(分钟序列/回看/断点) · US3 |
| `/api/calibers*` | FR-002/003(双口径元信息/五档能力自描述) · US2 |
| `/api/health` | FR-015(采集审计可查/last_run 暴露) · 宪章 V |

## 静态可视化（`web/`，D9）

- `app.mount("/", StaticFiles(directory="web", html=True))`。
- `web/index.html` + `app.js`（`fetch` 调 `/api`，Chart.js CDN，断点 `net_amount=null` 不连线，金额字符串直接展示不做 JS 数值运算）+ `style.css`。
- 人端页面与未来 agent Tool 消费**同一 JSON 契约**（FR-012 对称性）。

## 契约测试要点

- 每个端点：正常 200 + 结构化错误（如不支持的 sort_by → 422 `METRIC_NOT_SUPPORTED`；超窗口 → 422 `OUT_OF_RETENTION_WINDOW`）。
- 金额字段在 JSON 中是字符串（`"12345678901"`）而非 number。
- 休市访问：`provenance.is_stale=true` + 标注最近交易日。
