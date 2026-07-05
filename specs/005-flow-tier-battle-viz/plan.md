# Implementation Plan: 资金档位博弈可视化 + 多源数据层增强

**Branch**: `005-flow-tier-battle-viz` | **Date**: 2026-07-05 | **Spec**: [spec.md](./spec.md)

**Input**: [spec.md](./spec.md)（含 2026-07-05 两轮 Clarifications）

## Summary

把 QuantChive 的核心差异化价值做出来——**看各资金档位（超大/大/中/小单）流入流出博弈如何驱动价格**。分三块：

1. **数据层**：observation 加 4 档 gross 列（解锁流入流出）；三层存储分级（分钟/小时/日）；多源能力路由（新浪主 + 百度校验探针 + Tushare预留 + 东财盘中净额）；三级校验只标记不改数。
2. **后端**：多档对齐时序端点（一次返四档+主力+价+累计）；流入流出/累计（全程绝对累计）在后端算；板块四档由成分求和派生；盘中每分钟归档东财净额。
3. **前端**：ECharts 复合主图（价格+四档双向堆叠柱+累计主力/散户线）+ 热力条带 + 逐分钟回放 + 对抗图；简单方向背离自动高亮；红涨绿跌；金额只画不算。

分阶段：**P1 历史四档+可视化(MVP，数据现成)** → P2 盘中净额回放+多源校验 → P3 板块派生。

## Technical Context

**Language/Version**: Python 3.12（宪章）；前端原生 JS（无框架）。

**Primary Dependencies**: 现有 FastAPI + Pydantic v2 + SQLite + 新浪源。**新增（走 uv add）**：`playwright`（已装，百度无头浏览器校验探针）。前端引入 **ECharts**（CDN/vendored 全量包 ~330KB gzip，无打包步骤）。Tushare 仅预留不装。

**Storage**: SQLite。observation 加 4 gross 列（个股）；三层 granularity（1min/hourly/daily）已有枚举，扩降采；板块四档派生（is_derived）。3 月保留（分钟7天、小时7天~3月、日线放宽），全市场约 1-4GB。

**Testing**: pytest；源适配/gross推导/降采/校验/端点/前端逻辑 mock 可测不联网；百度无头浏览器注入 fake page。基线 213 passed。

**Target Platform**: 本地单机（SQLite 单写者，采集锁）。

**Project Type**: 单体 src-layout + web/ 静态前端。

**Performance Goals**: 多档端点一次返对齐序列（避免4次请求）；ECharts 60天×4档/240分钟×4档数百~千点，无压力。

**Constraints**: 盘中只净额（东财实时无gross，物理天花板）；各源口径差异正常不求相等；金额整数分禁float；流入流出=(gross±net)/2 恒整数；金额算术全后端。

**Scale/Scope**: 个股5596+板块991+ETF1521；四档 gross 仅个股（板块派生、ETF无）。

## Constitution Check

*GATE: Phase 0 前通过，Phase 1 后复检。*

| 原则 | 遵守方式 | 状态 |
|---|---|---|
| **I 可复现** | 源/http_get/clock/浏览器page 可注入；降采/累计确定性；校验幂等 | ✅ |
| **II 无前视** | 历史真实交易日、盘中当日累计、降采取小时末真实快照、不插值 | ✅ |
| **III 精度** | 金额整数分；流入流出=(gross±net)/2 数学恒整数；累计后端整数求和；禁float；多源归一保标度 | ✅ |
| **IV 测试优先** | 源/推导/降采/校验/端点/前端全 mock 可测不联网；百度注入 fake page | ✅ |
| **V 审计** | 校验分歧/恒等式失败/盘中采集 记 ingestion_run 三态；只标记不改数 | ✅ |

**技术约束**：playwright 走 uv add（已装）；ECharts 前端引入（无重量级后端依赖）。无违反项。

## Project Structure

### Documentation
```
specs/005-flow-tier-battle-viz/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/{tiers-series-api.md, source-adapter.md, validation.md}
└── tasks.md（/speckit-tasks 生成）
```

### Source Code
```
src/quantchive/
├── dao/
│   ├── schema.sql            # 加 4 gross 列 + "四档gross全有或全无"CHECK
│   ├── db_init.py            # 幂等 ALTER 加 gross 列（重建走 spec004 流程）
│   └── observation_dao.py    # tiers_series 多档对齐查询；写入带 gross
├── core/money.py             # 复用 to_cents；加 tier_inflow/outflow 派生纯函数
├── datasource/
│   ├── sina_flow_src.py      # 改：解析并归一 gross（r0-r3）→ 各档 gross_cents
│   ├── baidu_flow_src.py     # 新：无头浏览器校验探针（page.evaluate fetch，可注入 fake）
│   ├── tushare_flow_src.py   # 新：预留适配器（能力声明 is_active=0，不实现取数）
│   ├── metric_source.py      # 扩：gross 指标的源优先级
│   └── validate.py           # 新：三级校验（恒等式/跨源方向量级/记审计不改数）
├── service/
│   ├── ingest_service.py     # 改：sina 回填写 gross；盘中东财净额分钟归档；板块四档成分求和派生
│   ├── flow_query_service.py # 新：多档对齐时序 + 流入流出 + 全程累计 + 简单方向背离识别
│   └── (retention downsample)# 扩：分钟→小时降采处理各档(取小时末累计快照)
├── api/routers/
│   └── flow.py               # 新：GET /api/subjects/{id}/tiers_series（多档对齐+累计+背离标记）
└── scheduler/jobs.py         # 扩：盘中分钟归档 job（东财净额）

web/
├── index.html                # 引 ECharts
├── app.js                    # 替换 sparkline→ECharts 组件；四视图
├── charts/                   # 新：主图A/热力B/回放D/对抗C 的 ECharts option 构造
└── style.css
docs/
└── flow-terms-guide.md       # 新：术语+看图指南（交付用户，不进产品UI）
```

## 关键设计决策（详见 research.md）

- **D1 gross 存储**：4 档 gross 列（个股），流入流出查询算不落库；"四档gross全有或全无"CHECK。
- **D2 流入流出/累计**：`inflow=(gross+net)//2`（整除，数学恒整）；累计=全程绝对累计（数据最早日起，后端整数求和一次）。
- **D3 多源能力路由**：复用 ObservationSource+选源工厂+metric_source；源声明能力(gross/历史/独立后端/启用)；新浪主、百度探针、Tushare预留、东财盘中。
- **D4 百度无头浏览器**：Playwright 开一次浏览器 → page.evaluate 内 fetch 多股（绕 Acs-Token）；page 可注入 fake 做 mock 测试。
- **D5 三层降采**：分钟→小时取"小时末累计快照"（非求和/平均，因是累计值）；盘后每天跑；日线放宽。
- **D6 校验**：恒等式自检(拒入库) + 跨源方向/量级抓异常(默认3倍) + 只标记记审计不改数。
- **D7 背离识别**：简单方向背离——窗口价净跌+主力累计净升→吸筹；反之派发。后端算，可测。
- **D8 前端 ECharts**：单库，复合多pane主图 + dataZoom联动 + 十字光标同步 + timeline回放；红涨绿跌。

## Phased Approach（对齐 spec 优先级）

- **Phase A（P1·MVP，数据现成最快见效）**：schema 加 gross + sina 回填 gross + 多档端点(流入流出/累计/背离) + ECharts 主图A。**独立可交付**：个股四档博弈 + 价格背离看得见。
- **Phase B（P1）**：热力条带B。同数据、信息密度提升。
- **Phase C（P2）**：盘中东财净额分钟归档 + 逐分钟回放D。
- **Phase D（P2）**：三级校验(恒等式+百度对表) + 前端来源角标。
- **Phase E（P3）**：板块四档成分派生 + 对抗图C(象限散点选股)。
- **Phase F**：术语文档 + Tushare 预留接口 + README。

每 Phase `uv run pytest` 全绿；不破坏现有 213 测试。

## Constitution Check（Phase 1 后复检）
数据模型（gross列/派生/降采）、契约（端点/校验/源适配）未引入违反项：流入流出整除恒整、累计后端整数、校验只标记不改数、盘中当日累计不前视。✅
