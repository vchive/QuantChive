# Quickstart: 通用市场观测平台验证指南

**Feature**: 002 | 前置 [plan.md](./plan.md) · [contracts/](./contracts/)

> 验证/运行指南，不含实现代码。

## 前置

```bash
uv sync                       # 沿用 spec001 依赖(requests/fastapi/pydantic 已装)
# 删旧库重建(FR-024 丢弃重采): rm -f data/quantchive.db
```

## 验证场景（对应 US1-US5 + 关键裁定）

### 场景 1 — 大盘全景（US1 / SC-001 / D2）

```bash
uv run quantchive-collect --asset-class a_share --collect-tier coarse   # 采全市场个股→求和大盘
uv run uvicorn quantchive.app:app
curl 'http://localhost:8000/api/market/overview?asset_class=a_share'
```
**预期**：返回大盘主力净额（个股求和）、constituent_count/expected_count、覆盖率≥95%；打开首页 5 秒看到大盘冷热。覆盖率<95% 时该点不落（不冒充全市场）。

### 场景 2 — 板块下钻（US2 / SC-010 迁移不回退）

```bash
curl 'http://localhost:8000/api/sectors/ranking?caliber=eastmoney&sector_type=industry&sort_by=main_net&top_n=5'
```
**预期**：与 spec001 板块榜逐字段相等（双榜/五档/排序切换）；PCB/液冷等排名靠后板块也在（翻页拿全 991）。

### 场景 3 — 板块内个股下钻（US3 / D7）

```bash
curl 'http://localhost:8000/api/sectors/{sector_id}/stocks?sort_by=main_net&top_n=20'
curl 'http://localhost:8000/api/sectors/{sector_id}/stocks?as_of=<8天前>'
```
**预期**：当日下钻返回板块内个股五档排行；as_of 历史无成分 → 422 OUT_OF_WINDOW（不静默用当前成分）。

### 场景 4 — 基金/ETF（US4 / SC-004 能力自描述 / D8）

```bash
curl 'http://localhost:8000/api/etf/ranking?sort_by=change_pct&top_n=10'
curl 'http://localhost:8000/api/funds/110022/nav?days=30'
curl 'http://localhost:8000/api/meta/capability?asset_class=fund_etf&subject_kind=etf&caliber=eastmoney'
```
**预期**：ETF 榜有价/涨跌/量但 has_five_tier=false；对 ETF 请求五档排序 → 422 UNSUPPORTED_METRIC（不伪造 0）；基金 nav 实时返回带 note="not persisted"（不入库）。

### 场景 5 — 时序回看 + 降采（US5 / D3 / 用户降采决策）

```bash
curl 'http://localhost:8000/api/subjects/{id}/series?metric=main_net'
```
**预期**：近 7 天分钟序列；断点 value=null 不连线；超 7 天窗口点为小时级（hourly_rollup）；超 30 天 → OUT_OF_WINDOW。大盘序列为 5min 粒度（非伪 1min）。

### 场景 6 — 换源不改上层（SC-008）

用 fake ObservationSource 替换东财，不改 service/api 跑通排行。

## 测试

```bash
uv run pytest tests/unit          # 标度换算(cents/micro/bp)/排序稳定/断点/as-of/大盘求和覆盖率门禁
uv run pytest tests/contract      # datasource形状/DTO/错误码/双榜mode/能力自描述
uv run pytest tests/integration   # 迁移板块对拍(SC-010零回退)/collect幂等/大盘求和==个股汇总
```

**关键断言**：
- spec001 板块榜迁移后逐字段相等（SC-010）。
- 大盘求和覆盖率<95% 不写行。
- ETF 五档排序 → UNSUPPORTED_METRIC。
- 价格 ×1e6 / 涨跌幅 ×1e4 无精度损失。
- 个股 5535 只翻页 56 页拿全（不静默截 100）。
