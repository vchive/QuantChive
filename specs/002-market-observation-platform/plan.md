# Implementation Plan: 通用市场观测平台

**Branch**: `002-market-observation-platform` | **Date**: 2026-07-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-market-observation-platform/spec.md`

## Summary

把 spec 001 的板块资金流泛化为通用「可观测主体 × 观测指标 × 时间序列」平台。核心：`subject`（统一大盘/板块/个股/ETF/基金）+ `observation`（核心高频指标固定列）+ `observation_metric`（稀疏指标键值附表）三表混合模型。本期填真数据：A股（大盘→板块→个股：资金流+价格+成交量）+ 基金/ETF；债/期/商品/外汇架构预留。采**扩展迁移路线**（新增通用表、保留 spec001 旧表旧测，最后一步收敛），spec001 板块能力零回退。

技术方案由 9 智能体并行设计 + 对抗审查收敛，详见 [research.md](./research.md)（决策 D1-D10 + 依据）、[data-model.md](./data-model.md)、[contracts/](./contracts/)。

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI + Pydantic v2、requests（东财直连，已随 akshare 装入）、uvicorn、pydantic-settings；dev: pytest、httpx（沿用 spec001）

**Storage**: SQLite（WAL）；金额 INTEGER 分、价格 ×1e6 微元、百分比 ×1e4 基点，全整数标度禁 float

**Testing**: pytest，tests/{contract,unit,integration}

**Target Platform**: 本地单机，uvicorn 单 worker

**Project Type**: 单项目 src-layout + 静态无构建前端

**Performance Goals**: 采集间隔**按主体分层**——大盘/板块/热点股 1min，全市场个股/ETF 5min（均 ≤10min 上限）；排行页 5 秒可读

**Constraints**: 数值精度 0 损失、可复现（大盘覆盖率<95%不写）、无前视（成分 as-of）、东财限流（5535 只翻页 56 页需低并发）

**Scale/Scope**: ~8048 主体（大盘1+板块991+个股5535+ETF1521）；分层采集后约 303 万行/周；本期只读 9 端点

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 满足方式 | 状态 |
|---|---|---|
| **I 可复现** | 大盘覆盖率<95%不写行（不冒充全市场）、成分集合哈希落审计供重放、断点=缺行、Decimal换算、缺失抛错→NULL不填0 | ✅ PASS |
| **II 无前视** | subject_membership as-of 查询、成分退出闭合 effective_to、历史下钻无成分抛 OutOfWindow 不用当前值、大盘 observed_at 如实记底层时刻 | ✅ PASS |
| **III 数值精度** | 全整数标度（cents×100/micro×1e6/bp×1e4）+ metric_def.scale_factor、禁 float 做金额、五档 all-or-nothing CHECK、基金净值 Decimal 字符串 | ✅ PASS |
| **IV 测试优先** | 扩展路线保旧表旧测全绿、Step5 板块对拍回归、新能力测试先行 | ✅ PASS |
| **V 可观测审计** | ingestion_run 三态+泛化列名+aggregate_coverage+market_aggregate run_type、constituent_count 真列 | ✅ PASS |

**Gate 结论**：全部通过。4 处复杂度需论证（见 Complexity Tracking），均为 spec 明文要求或已裁决非过度设计。

## Project Structure

### Documentation (this feature)

```text
specs/002-market-observation-platform/
├── plan.md · research.md · data-model.md · quickstart.md · tasks.md
└── contracts/{datasource.md, service.md, api.md, ingestion.md}
```

### Source Code (repository root)

```text
src/quantchive/
├── models/enums.py          [扩] +AssetClass/SubjectLevel/SubjectKind/CollectTier/MetricValueKind；SectorType 保留(board 子类)
├── core/                    [不变] settings db logging money(+to_micro/to_basis_points) trading_calendar
├── datasource/
│   ├── base.py              [泛化] ObservationSource Protocol + 保留 SectorFlowSource 薄适配
│   ├── dto.py               [泛化] RawObservation(+price/volume/metrics/level/asset_class)
│   ├── _em_client.py        [新增] 抽 clist 翻页/重试/header(翻页上限动态 ceil(total/100))
│   ├── em_*_src.py          [拆] sector/stock/etf 各 fs；直连 push2delay
│   ├── fund_src.py          [新增] lsjz 按需(带 Referer, 不入 registry)
│   └── registry.py          [扩] (asset_class,level)→source
├── dao/                     schema.sql[扩展新表] subject_dao/observation_dao(+metric+latest)/metric_dao/constituent_dao[启用]/run_dao[列名泛化]
├── service/
│   ├── dto.py errors.py capability_registry.py[新] query_service.py[泛化]
│   ├── market_aggregate.py  [新] 大盘求和(覆盖率门禁)
│   ├── fund_lookup_service.py [新] 基金按需(物理隔离,不持 dao 写句柄)
│   ├── action_service.py    [新-docstring占位] 动作分层(本期无方法)
│   └── ingest_service.py    [泛化] 通用 collect + RunType 显式映射
├── api/routers/             market.py stocks.py etf.py funds.py meta.py health.py sectors.py[扩]
├── scheduler/               [填充] jobs.py runner.py(三频率错峰+热点选取)
├── cli/collect.py           [扩] +--asset-class/--collect-tier
└── tools/                   [预留] 空
```

**Structure Decision**：单项目 src-layout + 静态无构建前端。**依赖方向严格向内**：`api|tools → service → dao → datasource → core → models`。人和 agent 共用同一 `QueryService`；只读(`QueryService`)与动作(`ActionService`)物理分层，出口层不挂 ActionService。`subject` 表取代 spec001 留空的 `instrument`（个股/ETF/基金/大盘/板块同构一张表）。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 混合指标模型（固定列+kv附表） | FR-004 明文要求；核心指标要性能与精度、稀疏指标要扩展性 | 纯 EAV 查询慢/精度失控；纯宽表加指标改 schema 违 FR-003 |
| 预留 4 品种（is_populated=0） | FR-007 要求；枚举预留低成本 | 未来加品种改 schema 破坏已有数据 |
| 多标度整数（100/1e6/1e4） | 金额=法定分、价格需小数余量、百分比匹配 0.01% 行情 | 单一标度无法兼顾金额精确与价格小数 |
| trade_calendar / 分层采集 tier 列 | 宪章 I 确定性 + 存储量 3.3GB 必解 | 全 1min 采集 966万行/周 SQLite 不可行 |
