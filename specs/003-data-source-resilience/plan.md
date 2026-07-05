# Implementation Plan: 数据层韧性重构（多源可插拔 + 限流治理 + 冷热分离 + 缓存）

**Branch**: `003-data-source-resilience` | **Date**: 2026-07-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-data-source-resilience/spec.md`（含 2026-07-04 Clarifications）

## Summary

把 QuantChive「数据怎么进来」这一层从单源东财重构为「主动节流 + 本地缓存 + 冷热分离 + 多源分流」，**不改上层观测模型/查询/API/可视化**。四层能力分优先级落地：

- **P0 止血**（HTTP 客户端层）：翻页页间随机 sleep、退避加 jitter、注入 requests-cache 透明缓存、限流降级识别（零静默降级为硬门 SC-001）。
- **P1 冷热分离**：引入 baostock 作历史行情回填源（独立于东财限流）；建「主体×指标 已存区间」表驱动增量补缺。
- **P2 多源**：按 source_id 选源工厂 + 缺失优雅降级；资金流→东财(主)/同花顺(备)双活；归一层统一多源字段/标度。
- **P3 纪律**：抽采集基类固化限流纪律（低并发+请求间 sleep+失败分轮重取+完整性校验）。

技术路径已由 Clarifications 定：**baostock（历史）+ 同花顺 10jqka（资金流备源，先验 hexin-v 头）+ requests-cache（缓存）**。

## Technical Context

**Language/Version**: Python 3.12（宪章约束）

**Primary Dependencies**: 现有 FastAPI + Pydantic v2 + requests + akshare。**新增（走 uv add）**：`baostock`（历史行情源，免 token）、`requests-cache`（透明 HTTP 缓存）。同花顺经 akshare 已有的 `stock_fund_flow.py` 那组函数（无需新依赖，但需验证 hexin-v 头）。

**Storage**: SQLite（WAL）。本期新增 1 张表 `coverage_range`（主体×指标已存区间）+ 扩展 `ingestion_run` 记「实际使用的源」；观测数据模型（subject/observation）**不动**。稳定历史列式落盘（parquet/DuckDB）为演进方向，本期不做。

**Testing**: pytest；所有源适配/节流/缓存/故障切换/回填经可注入 http_get + 内存缓存后端 mock，**不联网**（宪章 IV）。当前基线 144 passed。

**Target Platform**: 本地单机（单 worker，SQLite 单写者约束）。

**Project Type**: 单体（src-layout `src/quantchive/`）。

**Performance Goals**: 采集不因高频触发东财 IP 冷却（SC-001 零静默降级为硬门）；二次历史回填网络请求量大幅下降（仅补缺口，SC-003）。

**Constraints**: 单写者；限流阈值无官方基线（~1000/天为社区轶事下界，节流预算按保守下界）；同花顺 hexin-v 头依赖 JS 生成（接入前小流量验证）；金额整数最小单位、禁 float（宪章 III）。

**Scale/Scope**: 主体量级沿用 spec002（板块 991 + 个股 5535 + ETF 1521）；历史回填深度配置化，默认对齐保留窗（30 交易日）。

## Constitution Check

*GATE: 必须在 Phase 0 前通过，Phase 1 后复检。*

| 原则 | 本 feature 的遵守方式 | 状态 |
|---|---|---|
| **I 可复现** | http_get / sleep / clock 全可注入；缓存后端可注入；回填幂等（据已存区间补缺，重跑不产生重复/不破坏已存）。 | ✅ |
| **II 禁前视** | 历史回填只写真实交易日期的历史值；已存区间边界含左不含右，不重叠误写；不用未来值回填空档。 | ✅ |
| **III 精度** | 多源归一层把各源单位（元/亿元、手/股）统一到内部整数最小单位；禁 Decimal(float)；跨源拼接数值抽样核对（SC-007）。 | ✅ |
| **IV 测试优先** | 节流/退避/缓存/降级识别/选源切换/归一/回填全部 mock 可测不联网；先写失败测试。 | ✅ |
| **V 审计** | ingestion_run 记三态 + 实际使用的源 + 限流降级标记；禁无声吞异常（降级显式记录，SC-001）。 | ✅ |

**技术约束**：新增 baostock / requests-cache 走 `uv add`（不手改 uv.lock）——已在 plan 显式论证（对应宪章「引入新依赖须在 plan 论证」）。无违反项，无 Complexity Tracking 需要。

## Project Structure

### Documentation (this feature)

```text
specs/003-data-source-resilience/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策 D1–D8
├── data-model.md        # Phase 1：coverage_range 表 + 源路由配置 + 归一约定
├── quickstart.md        # Phase 1：端到端验证场景
├── contracts/
│   ├── source-protocol.md   # ObservationSource 扩展 + HistorySource + 选源工厂契约
│   ├── http-client.md       # 节流/退避/缓存客户端契约
│   └── cli.md               # backfill / collect CLI 契约
└── tasks.md             # Phase 2（/speckit-tasks 生成，非本命令）
```

### Source Code (repository root)

```text
src/quantchive/
├── datasource/
│   ├── base.py              # 扩展：HistorySource Protocol、SourceCapability 分类
│   ├── _http_client.py      # 新：通用节流/退避/缓存 HTTP 底座（泛化自 _em_client）
│   ├── _em_client.py        # 改：页间 sleep + 退避 jitter，委托 _http_client
│   ├── _collector_base.py   # 新：采集基类（低并发+分轮重取+完整性校验，US4）
│   ├── registry.py          # 新：按 source_id 选源工厂 + 缺失降级（US3）
│   ├── routing.py           # 新：指标类型→主/备源 路由配置（US3）
│   ├── normalize.py         # 新：多源字段/单位/复权归一层（US3/FR-015）
│   ├── baostock_src.py      # 新：baostock 历史行情源（US2）
│   ├── ths_flow_src.py      # 新：同花顺 10jqka 资金流备源（US3，先验 hexin-v）
│   ├── em_*_src.py, fund_src.py, em_kline_src.py  # 现有，微调接入路由/客户端
│   └── dto.py               # 微调：历史行情 DTO（若需）
├── dao/
│   ├── coverage_dao.py      # 新：coverage_range 已存区间读写（US2/FR-007）
│   ├── schema.sql           # 改：新增 coverage_range 表；ingestion_run 加 used_source 列
│   └── db_init.py           # 改：建表 + seed 新源（baostock/ths_flow）
├── service/
│   ├── ingest_service.py    # 改：回填走 baostock + 增量补缺；采集接入选源/降级识别
│   └── ...                  # 查询层不动
├── core/settings.py         # 改：节流/缓存 TTL/源路由/回填深度 配置
└── cli/collect.py           # 改：backfill --source baostock；降级审计透出

tests/
├── unit/                    # 节流/退避/缓存/归一/降级识别/选源 单测
├── integration/             # 回填增量补缺 + 主备切换 + 换源不改上层 集成测
└── contract/                # HistorySource / 选源工厂 契约测
```

**结构决策**：把 `_em_client` 里的节流/退避/缓存下沉为通用 `_http_client`（各源共用底座），选源/路由/归一/采集纪律各成独立模块——对齐调研的 vnpy（选源工厂）/qlib（采集基类）/adata（分源）/BarOverview（已存区间）设计。旧 `_em_client` 保留薄壳委托新底座，现有源不破（迁移铁律）。

## Phased Approach（对齐 spec 用户故事优先级）

- **Phase A（P0 止血，US1）**：`_http_client` 节流+jitter+requests-cache 注入；`_em_client` 委托改造；限流降级识别（长度/条数校验）+ 审计。**独立可交付**：高频采集不再打限流、零静默降级可测。
- **Phase B（P1 冷热分离，US2）**：`baostock_src` + `coverage_range` 表 + `coverage_dao` + `ingest_service` 增量回填。**独立可交付**：历史时序有真实深度、二次回填仅补缺。
- **Phase C（P2 多源，US3）**：`registry` 选源工厂 + `routing` 主备 + `normalize` 归一 + `ths_flow_src`（先小流量验 hexin-v，验不过降级预留）。**独立可交付**：主源限流自动切备源、换源不改上层。
- **Phase D（P3 纪律，US4）**：`_collector_base` 抽象 + 各源子类化。**独立可交付**：统一采集纪律。

每 Phase 结束 `uv run pytest` 全绿；不破坏现有 144 测试（迁移铁律：新旧并存、薄壳委托）。

## Constitution Check（Phase 1 后复检）

Phase 1 设计（data-model/contracts）未引入违反项：coverage_range 幂等区间不前视；归一层保精度；选源工厂缺失降级不崩且记审计；新依赖已论证。✅ 通过。
