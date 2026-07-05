---
description: "Task list for 002-market-observation-platform implementation"
---

# Tasks: 通用市场观测平台

**Input**: Design documents from `specs/002-market-observation-platform/`

**Prerequisites**: [plan.md](./plan.md) · [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

**Tests**: 包含测试任务 —— 宪章原则 IV 测试优先；重构迁移必须有板块能力对拍回归（SC-010 零回退）。

**Organization**: 按用户故事分组，叠加 research.md 的 7 步安全增量迁移主线（每步旧测不破、Step7 才 drop 旧表）。

**⚠️ 迁移铁律**：本期是重构迁移，不是从零建。现有 48 测试全绿。**扩展路线**——新增通用表与旧 `sector_*` 表并存，最后一步一次性收敛。任何阶段结束时 `uv run pytest` 必须全绿。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: US1-US5
- 每个任务含精确文件路径

## Path Conventions

单项目 src-layout：`src/quantchive/`、`tests/`、`web/`。

---

## Phase 1: Setup（Step 0 — 迁移前置）

**Purpose**: 迁移前置，确认基线绿

- [X] T001 确认 spec001 基线：`uv run pytest` 全绿（48 测试），记录基线；本 spec 全程以此为回归基准
- [X] T002 [P] 扩展 `pyproject.toml`（如需）：确认 requests 可用（东财直连，已随 akshare）；无新增运行依赖
- [X] T003 [P] 在 `src/quantchive/core/settings.py` 追加分层采集与保留配置：`retention_trade_days=30`、`downsample_after_days=7`、各 tier 间隔（板块 1min/个股 ETF 5min）、热点股 Top-N（默认 100）

---

## Phase 2: Foundational（Step 1-3 — 通用地基，纯增量不破旧）

**Purpose**: 通用枚举/标度/新表，与旧表并存

**⚠️ CRITICAL**: 完成前用户故事不能开始；本阶段全程旧测保持全绿

### Step 1：枚举 + 标度换算 + DTO（纯增量）

- [X] T004 [P] 扩展 `src/quantchive/models/enums.py`：新增 `AssetClass`/`SubjectLevel`/`SubjectKind`/`CollectTier`/`MetricValueKind`；`SortField` 移除 `NET_AMOUNT` 或别名 `main_net`（东财 net_amount=main_net 恒等）；保留 `SectorType`（board 子类）
- [X] T005 [P] 扩展 `src/quantchive/core/money.py`：新增 `to_micro`(×1e6 价格/净值)、`to_basis_points`(×1e4 百分比)，均 `Decimal(str(x))`+ROUND_HALF_EVEN 禁 float
- [X] T006 [P] 单元测试标度换算 in `tests/unit/test_scales.py`：价格 ×1e6、涨跌幅 ×1e4 基点、0/负/NaN 边界（先失败）
- [X] T007 [P] 定义 `RawObservation` dataclass in `src/quantchive/datasource/dto.py`（+price/volume/metrics/level/asset_class，与旧 `RawSectorFlow` 并存）

### Step 3：新表 schema + 通用 DAO（追加不改旧表）

- [X] T008 扩展 `src/quantchive/dao/schema.sql`：追加 asset_class/subject/subject_membership/metric_def/metric_applicability/observation/observation_metric 表 + 索引 + CHECK per [data-model.md](./data-model.md)；**不改 sector_*/instrument/sector_constituent 旧表**
- [X] T009 扩展 `src/quantchive/dao/db_init.py`：seed asset_class（a_share/fund_etf populated，其余预留）、metric_def（9 指标标度）、metric_applicability、data_source（+em_fund）
- [X] T010 [P] 单元测试新 schema in `tests/unit/test_schema_v2.py`：uq_observation 幂等、五档 all-or-nothing、至少一指标非 NULL CHECK、FK、metric_def seed（先失败）
- [X] T011 [P] 实现 `src/quantchive/dao/subject_dao.py`：upsert(asset_class,level,subject_kind,...)、find_id(asset_class,level,symbol)（加 asset_class+level 防跨主体误命中）、children_of(parent,as_of)
- [X] T012 [P] 实现 `src/quantchive/dao/metric_dao.py`：upsert_metric、按能力查 metric_applicability
- [X] T013 实现 `src/quantchive/dao/observation_dao.py`：幂等 upsert（含价格/量列）、upsert_metric、`ranking_snapshot`（下推 ORDER BY..LIMIT top_n，5535 股禁全量物化）、`series`（排除 intraday_latest、排序过滤 LATEST 防字符串排序假点）per [data-model.md](./data-model.md)（依赖 T011）
- [X] T014 [P] 泛化 `src/quantchive/dao/run_dao.py` 列名：sectors_ok→subjects_ok、sector_scope→subject_scope、+aggregate_coverage、run_type +market_aggregate/retention_downsample（与旧调用兼容）

**Checkpoint**: 新旧表并存，旧测全绿，通用 DAO 就绪

---

## Phase 3: US2 - 板块下钻迁移 (Priority: P1) 🎯 迁移核心

> 先做 US2（板块）——它是 spec001 已有能力的迁移，是"零回退"的验证基准，风险最低。US1 大盘依赖个股采集，放 US2 后。

**Goal**: 板块资金流迁移进通用模型（subject level='sector' + observation），能力与 spec001 逐字段相等。

**Independent Test**: `get_sector_ranking(level='sector')` 与 spec001 旧路径同 mock 双榜/五档/排序逐字段相等（SC-010）。

### Step 2：Source 参数化（兼容 shim）

- [X] T015 [US2] 泛化 `src/quantchive/datasource/base.py`：定义 `ObservationSource` Protocol + `FetchSpec` + `SubjectCapability`；**保留旧 `SectorFlowSource`** per [contracts/datasource.md](./contracts/datasource.md)
- [X] T016 [US2] 抽取 `src/quantchive/datasource/_em_client.py`：把现有 clist 翻页/重试/header 逻辑抽出；**page_size 500→100（东财硬顶）、翻页上限 20→ceil(total/100) 动态**（5535 需 56 页，否则静默截断）
- [X] T017 [US2] 泛化 `src/quantchive/datasource/em_sector_src.py`（从 akshare_src.py 拆）：参数化 fs，board 特例；旧 `fetch_snapshot(caliber,sector_type)` shim 转调，`test_eastmoney_adapter` 5 例不改仍绿
- [X] T018 [P] [US2] 契约测试 board Source 迁移 in `tests/contract/test_em_sector_migrated.py`：翻页拿全 991、f-code 映射、shim 兼容（先失败）

### Step 4-5：Service 泛化 + 板块对拍回归

- [X] T019 [P] [US2] 定义对外 Pydantic 模型 in `src/quantchive/service/dto.py`：SubjectRef/ObservationItem/RankingResult(+mode bipolar/unipolar)/SubjectCapability，与旧模型并存
- [X] T020 [P] [US2] 扩展 `src/quantchive/service/errors.py`：+SubjectNotFound/AssetClassNotProvisioned/OutOfWindow/UnsupportedMetric + code→HTTP
- [X] T021 [US2] 实现 `src/quantchive/service/capability_registry.py`：主体×指标能力真源，available_sort_fields 按 (asset_class,subject_kind) 隔离（板块拒价格排序）
- [X] T022 [US2] 泛化 `src/quantchive/service/query_service.py` 加 `get_sector_ranking(level='sector')`：双榜 mode 隔离、tiebreaker、provenance 取真 source_id（删 f"akshare:.." 谎报）per [contracts/service-api.md](./contracts/service-api.md)（依赖 T013/T019/T021）
- [X] T023 [US2] **板块能力对拍回归测试** in `tests/integration/test_sector_migration_parity.py`：get_sector_ranking 与 spec001 旧 get_ranking 同 mock 数据双榜/五档/排序逐字段相等（证 SC-010 零回退，先失败）
- [X] T024 [US2] 通用 collect 迁移板块 in `src/quantchive/service/ingest_service.py`：泛化 collect_once 支持 subject 层级；**斩断 RunType(value_type.value) 跨枚举直转改显式映射**；板块走 subject_dao+observation_dao
- [X] T025 [US2] 扩展 `src/quantchive/api/routers/sectors.py`：`/api/sectors/ranking` 走通用 QueryService（响应结构与 spec001 兼容）

**Checkpoint**: 板块能力迁移进通用模型，对拍证明零回退，新旧路径并存

---

## Phase 4: US1 - 大盘资金全景 (Priority: P1)

**Goal**: 采全市场个股 → 采集层求和 → 大盘 observation（覆盖率门禁），首屏展示大盘冷热。

**Independent Test**: `/api/market/overview` 返回大盘主力净额（个股求和）、constituent_count/expected、覆盖率≥95%；<95% 该点不落。

### Tests for US1

- [X] T026 [P] [US1] 契约测试 `/api/market/overview` in `tests/contract/test_api_market.py`（先失败）
- [X] T027 [P] [US1] 单元测试大盘求和覆盖率门禁 in `tests/unit/test_market_aggregate.py`：Python int 求和禁 float、<95% 不写行、复用 batch_slot、成分哈希（先失败）
- [X] T028 [P] [US1] 集成测试大盘求和==个股汇总 in `tests/integration/test_market_sum.py`（先失败）

### Implementation for US1

- [X] T029 [US1] 泛化 `src/quantchive/datasource/em_stock_src.py`：fs=全市场个股，翻页 56 页，f2/f3/f5/f6 价量映射，重试限流（串行低并发）
- [X] T030 [US1] 实现 `src/quantchive/service/market_aggregate.py`：采完个股后 Python int 求和 main_net、覆盖率≥95% 写大盘 observation(is_derived=1, run_type=market_aggregate, 记 constituent_count/expected/哈希)、<95% 不写（使 T027/T028 通过，依赖 T029/T013）
- [X] T031 [US1] 定义 `MarketOverviewResult` in `src/quantchive/service/dto.py` + `get_market_overview` in query_service.py
- [X] T032 [US1] 实现 `src/quantchive/api/routers/market.py`：`/api/market/overview`（使 T026 通过）
- [X] T033 [US1] collect 增大盘子步：`stock_5min` JOB 采完触发 market_aggregate（复用 batch_slot）in `src/quantchive/service/ingest_service.py`

**Checkpoint**: 大盘全景可用，求和门禁防冒充

---

## Phase 5: US3 - 板块内个股下钻 (Priority: P2)

**Goal**: 板块下钻到板块内个股资金流排行（正向 clist 成员，as-of 历史预留）。

**Independent Test**: `/api/sectors/{id}/stocks` 当日返回板块内个股五档；as_of 历史无成分 → 422 OUT_OF_WINDOW。

### Tests for US3

- [X] T034 [P] [US3] 契约测试 `/api/sectors/{id}/stocks` + as_of 超窗 422 in `tests/contract/test_api_stocks.py`（先失败）
- [X] T035 [P] [US3] 单元测试 subject_membership as-of 查询无前视 in `tests/unit/test_membership_asof.py`（先失败）

### Implementation for US3

- [X] T036 [US3] 实现 `fetch_members(fs=b:BK{code})` in `src/quantchive/datasource/em_sector_src.py`：板块成员五档
- [X] T037 [US3] 启用 `src/quantchive/dao/constituent_dao.py`（subject_membership as-of 查询/闭合 effective_to）
- [X] T038 [US3] 实现 `get_stocks_in_sector` in query_service.py：正向下钻；as_of<today 无成分抛 OutOfWindow（不静默用当前成分）（使 T034/T035 通过）
- [X] T039 [US3] 实现 `src/quantchive/api/routers/stocks.py`：`/api/sectors/{id}/stocks`

**Checkpoint**: 板块→个股下钻可用（当日），历史 as-of 预留

---

## Phase 6: US4 - 基金/ETF (Priority: P2)

**Goal**: ETF 全采进盘中采集（价/量/规模proxy），开放式基金按需实时查净值。

**Independent Test**: ETF 榜有价/涨跌/量但 has_five_tier=false；ETF 五档排序→422 UNSUPPORTED_METRIC；基金 nav 实时返回 note="not persisted"。

### Tests for US4

- [X] T040 [P] [US4] 契约测试 ETF 榜 + 能力自描述 + 五档排序 422 in `tests/contract/test_api_etf.py`（先失败）
- [X] T041 [P] [US4] 契约测试基金按需查（旁路不入库）in `tests/contract/test_api_funds.py`（先失败）
- [X] T042 [P] [US4] 单元测试 ETF circ_mktcap(f21) 命名不冒充 aum in `tests/unit/test_etf_metric_naming.py`（先失败）

### Implementation for US4

- [X] T043 [US4] 实现 `src/quantchive/datasource/em_etf_src.py`：fs=b:MK00xx 全采 1521，money_flow=NULL，价/量固定列，f21→circ_mktcap 入 KV 附表
- [X] T044 [P] [US4] 实现 `src/quantchive/datasource/fund_src.py`：lsjz 按需（**带 Referer: fund.eastmoney.com**），FSRQ/DWJZ/JZZZL，净值 Decimal 字符串（不入 registry）
- [X] T045 [P] [US4] 实现 `src/quantchive/service/fund_lookup_service.py`：物理隔离（不持 dao 写句柄、不进调度），get_fund_nav 返回 note="not persisted"
- [X] T046 [US4] 实现 `get_etf_ranking`(unipolar mode) + `get_capability` in query_service.py（ETF 五档排序→UnsupportedMetric）
- [X] T047 [US4] 实现 `src/quantchive/api/routers/etf.py` + `funds.py` + `meta.py`（capability/subjects）（使 T040/T041 通过）

**Checkpoint**: 双品种可用，能力诚实自描述

---

## Phase 7: US5 - 时序回看 + 降采 (Priority: P3)

**Goal**: 单主体分钟序列 + 7 天回看；8-30 天降采小时级。

**Independent Test**: 近 7 天分钟序列断点 null 不连线；超 7 天为小时级；超 30 天 OUT_OF_WINDOW。

### Tests for US5

- [X] T048 [P] [US5] 契约测试 `/api/subjects/{id}/series` 断点 + 超窗 in `tests/contract/test_api_series.py`（先失败）
- [X] T049 [P] [US5] 单元测试保留降采口径 in `tests/unit/test_downsample.py`：小时聚合（资金流取末值/价取收盘/量累计）、降采后回看连续（先失败）

### Implementation for US5

- [X] T050 [US5] 实现 `get_subject_series` in query_service.py：排除 intraday_latest、断点 null、超 7 天返小时点、超 30 天 OutOfWindow
- [X] T051 [US5] 实现 `src/quantchive/api/routers/subjects.py`：`/api/subjects/{id}/series`
- [X] T052 [US5] 实现保留降采任务 in `src/quantchive/service/ingest_service.py`：retention_downsample（7 天前分钟→小时 rollup、删原行）+ retention_cleanup（删 30 天前）

**Checkpoint**: 时序回看 + 分级保留可用

---

## Phase 8: 迁移收敛（Step 7 — 唯一破坏性，放最后）

**Purpose**: 确认全稳定后一次性 drop 旧表旧代码

**⚠️ 仅在 US1-US5 全部通过、对拍回归绿之后执行**

- [X] T053 从 `src/quantchive/dao/schema.sql` 移除 sector_money_flow/sector/sector_constituent 旧表段
- [X] T054 删除 `src/quantchive/dao/flow_dao.py`、`sector_dao.py` 旧板块专用（已被 observation_dao/subject_dao 取代）
- [X] T055 删除 datasource/akshare_src.py 中旧 SectorFlowSource 薄适配 shim + 旧 RawSectorFlow（若无引用）
- [X] T056 迁移/删除 spec001 旧测试（test_schema.py 旧表用例、test_api_ranking.py 旧路径），保留已迁移到通用模型的等价测试
- [X] T057 `rm -f data/quantchive.db` 删旧库，`init_db` 建新 schema（FR-024 丢弃重采）

---

## Phase 9: 调度器 + 可视化（跨故事集成）

**Purpose**: 三频率自动采集 + 下钻可视化

- [X] T058 [P] 实现 `src/quantchive/scheduler/interval.py`：AdaptiveInterval（AIMD，沿用 spec001 思路）
- [X] T059 实现 `src/quantchive/scheduler/jobs.py` + `runner.py`：三频率错峰 JOB（板块 1min 相位0 / 个股 5min 相位20 / ETF 5min 相位40）、热点股选取、交易时段判断、EOD 补全、盘后降采+清理 per [contracts/ingestion.md](./contracts/ingestion.md)
- [X] T060 接入 FastAPI lifespan in `src/quantchive/app.py`：启动拉起调度、注册全部路由、异常处理器
- [X] T061 实现 `/api/health` in `src/quantchive/api/routers/health.py`：latest_trade_date、last_run、per-tier 采集状态
- [X] T062 [P] 泛化可视化 `web/index.html` + `web/app.js` + `web/style.css`：品种 Tab → 大盘卡片 → 板块双榜 → 板块内个股 → 个股详情栈式下钻；能力驱动渲染（不支持指标灰字告知）；断点不连线；金额用后端 Decimal 字符串 per [research.md](./research.md) D9
- [X] T063 扩展 `src/quantchive/cli/collect.py`：+--asset-class/--collect-tier

---

## Phase 10: Polish & Cross-Cutting

- [X] T064 [P] 契约测试换源不改上层 in `tests/contract/test_source_swappable_v2.py`：fake ObservationSource 替换东财跑通排行（SC-008）
- [X] T065 [P] 更新 [README.md](../../README.md)：平台定位、运行方式、多品种采集、单 worker 约束
- [ ] T066 运行 [quickstart.md](./quickstart.md) 全部 6 场景端到端验证
- [ ] T067 采一周真数据实测存储量（含降采后 ~1.5-2.5GB）+ 5535 只全采耗时 + VACUUM/checkpoint（research.md 待验证项 2/4/6）
- [X] T068 全量 `uv run pytest` 全绿（宪章 IV 合并关卡）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup(1)** → **Foundational(2)** 阻塞所有故事
- **US2 板块迁移(3)** 先做——最低风险、零回退基准
- **US1 大盘(4)** 依赖 US1 个股采集（em_stock_src）+ Foundational
- **US3(5) US4(6) US5(7)** 依赖 Foundational，可在 US1/US2 后并行
- **迁移收敛(8)** 依赖 US1-US5 全绿——唯一破坏性放最后
- **调度器+可视化(9)** 依赖各故事 collect/query 就绪
- **Polish(10)** 最后

### 迁移安全性（贯穿全程）

- Phase 2-7 全程新旧表并存，每 Phase 结束 `uv run pytest` 全绿
- T023 板块对拍回归是 SC-010 零回退的证明，必须通过才继续
- Phase 8 是唯一破坏性阶段，仅在全稳定后执行

### Parallel Opportunities

- Setup T002/T003 [P]
- Foundational T004-T007 [P]、T010-T012/T014 [P]
- 各故事测试 [P] 先写
- US3/US4/US5 在 Foundational 后可并行（不同文件）
- 注意：em_sector_src.py(T017/T036)、query_service.py(T022/T031/T038/T046/T050)、ingest_service.py(T024/T033/T052) 同文件需串行

---

## Implementation Strategy

### 迁移优先（先证零回退）

1. Setup + Foundational（通用地基，旧测不破）
2. **US2 板块迁移 + 对拍回归**（证明 SC-010 零回退）← 迁移信心的关键
3. US1 大盘（个股采集 + 求和门禁）
4. US3/US4/US5 增量叠加
5. **STOP & VALIDATE**：全故事绿 + 对拍绿
6. Phase 8 迁移收敛（drop 旧表）
7. 调度器 + 可视化 → 全自动

### Notes

- [P] = 不同文件无依赖；同文件（em_sector_src/query_service/ingest_service）串行
- 全程旧测全绿；Phase 8 前不 drop 任何旧表
- 数值全整数标度，JSON 金额字符串，前端零 float
- 合并关卡（宪章）：无前视 / 数值有测试 / 依赖经 uv add / 结构化审计 / pytest 全绿
