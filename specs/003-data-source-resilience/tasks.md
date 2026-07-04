---
description: "Task list for 003-data-source-resilience implementation"
---

# Tasks: 数据层韧性重构（多源可插拔 + 限流治理 + 冷热分离 + 缓存）

**Input**: Design documents from `specs/003-data-source-resilience/`

**Prerequisites**: [plan.md](./plan.md) · [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

**Tests**: 含测试任务——宪章原则 IV 测试优先；所有节流/缓存/降级/选源/回填 mock 可测不联网。

**Organization**: 按用户故事分组，对齐 plan 的 Phase A–D（US1=P0 止血 / US2=P1 冷热分离 / US3=P2 多源 / US4=P3 纪律）。每个 US 独立可交付、可测。

**⚠️ 迁移铁律**：只重构「数据怎么进来」，不改上层观测模型/查询/API/可视化。旧 `_em_client`/现有源保留薄壳委托，**每阶段 `uv run pytest` 全绿（现基线 144）**。

## Format: `[ID] [P?] [Story] Description`

- **[P]**：可并行（不同文件、无未完成依赖）
- **[Story]**：US1–US4
- 每任务含精确文件路径

## Path Conventions

单项目 src-layout：`src/quantchive/`、`tests/`。

---

## Phase 1: Setup（依赖引入 + 基线）

**Purpose**: 引入新依赖、确认基线绿

- [X] T001 确认基线：`uv run pytest` 全绿（144），记录为回归基准
- [X] T002 [P] `uv add baostock`（历史行情源，免 token）——走 uv add 不手改 uv.lock
- [X] T003 [P] `uv add requests-cache`（透明 HTTP 缓存）——走 uv add
- [X] T004 [P] 在 `src/quantchive/core/settings.py` 追加数据层配置：`page_sleep_min/max`(默认0.5/1.5)、`backoff_jitter`、`cache_ttl_historical/realtime`、`backfill_default_days`、源路由覆盖项

---

## Phase 2: Foundational（阻塞所有故事的通用地基）

**Purpose**: 通用 HTTP 底座 + 新表 + 审计扩展，纯增量不破旧

**⚠️ CRITICAL**: 完成前用户故事不能开始；本阶段全程旧测保持全绿

- [X] T005 扩展 `src/quantchive/datasource/base.py`：新增 `HistorySource` Protocol（fetch_price_history）per [contracts/source-protocol.md](./contracts/source-protocol.md)；保留 `ObservationSource` 不改
- [X] T006 扩展 `src/quantchive/dao/schema.sql`：新增 `coverage_range` 表 + 索引 per [data-model.md](./data-model.md)；**不改 subject/observation 观测表**
- [X] T007 扩展 `src/quantchive/dao/db_init.py`：`coverage_range` 建表 + `ingestion_run` 幂等 ALTER 加 `used_source_code`/`degraded` 列 + seed 新源(baostock/ths_flow，ths_flow 初始 is_active=0)
- [X] T008 [P] 单元测试新 schema in `tests/unit/test_schema_003.py`：coverage_range UNIQUE/索引、ingestion_run 新列、新源 seed（先失败）
- [X] T009 [P] 实现 `src/quantchive/dao/coverage_dao.py`：`get_range`/`upsert_range`(区间合并)/`missing_gaps`(coverage_range 预判 + observation×calendar 权威缺日) per [data-model.md](./data-model.md)
- [X] T010 [P] 单元测试 coverage_dao in `tests/unit/test_coverage_dao.py`：区间合并幂等、缺口计算、含左不含右无前视（先失败）

**Checkpoint**: 新表 + HistorySource 抽象 + coverage DAO 就绪，旧测全绿

---

## Phase 3: US1 - 高频采集不再被限流封禁 (Priority: P0 止血) 🎯 MVP

**Goal**: HTTP 客户端层节流 + 退避抖动 + 透明缓存 + 零静默降级识别。

**Independent Test**: mock http_get/sleep/rng——断言页间 sleep 生效、退避含抖动、historical 端点二次命中缓存不打真实请求、`is_degraded(1,30)==True` 拒绝落库。

### Tests for US1（先写，先失败）

- [X] T011 [P] [US1] 单元测试 `tests/unit/test_http_client.py`：页间 sleep 被调用(次数=页-1)、退避=基数+抖动项、historical 二次命中缓存(计数 http_get 二次不打)、is_degraded 阈值判定
- [X] T012 [P] [US1] 单元测试限流降级识别 in `tests/unit/test_degrade_guard.py`：请求全量只回极少条→degraded、拒绝落残缺、ingestion_run.degraded=1（先失败）

### Implementation for US1

- [X] T013 [US1] 实现 `src/quantchive/datasource/_http_client.py`：`HttpClient`——页间 `sleep(rng(*page_sleep))`、退避 `min(base*2**a,cap)+rng(0,jitter)`、`cache_policy` 分档且 **page_sleep 可按 policy 覆盖**（默认统一，historical/realtime 可配不同节流预算，闭合 FR-004）、注入点(http_get/sleep/rng) per [contracts/http-client.md](./contracts/http-client.md)（使 T011 通过）
- [X] T014 [US1] 实现 `is_degraded(got, expected_min)` 纯函数 in `_http_client.py`（泛化 spec002 min_days_guard，使 T012 通过）
- [X] T015 [US1] 改造 `src/quantchive/datasource/_em_client.py`：翻页循环加页间 sleep、退避加 jitter（委托或内联 HttpClient）；**对外签名不变**，现有 5 源与 144 测试不改仍绿
- [X] T016 [US1] 缓存注入接线 in `src/quantchive/api/deps.py` / `cli/collect.py`：生产注入 `requests_cache.CachedSession`（历史/日终长TTL、实时短TTL），测试可注入内存后端
- [X] T017 [US1] 采集/回填接入降级识别 in `src/quantchive/service/ingest_service.py`：识别到 degraded → 不落残缺 + ingestion_run.degraded=1 + 审计原因（零静默降级 SC-001）

**Checkpoint**: 高频采集节流+抖动+缓存生效，零静默降级可测。**独立可交付止血。**

---

## Phase 4: US2 - 历史一次回填、增量补缺、不受东财限流 (Priority: P1)

**Goal**: baostock 历史行情源 + coverage_range 增量补缺。

**Independent Test**: 注入 fake baostock 返回 N 日 → 写 daily_final + coverage_range；二次回填零请求；单主体失败隔离。

### Tests for US2（先写，先失败）

- [X] T018 [P] [US2] 契约测试 baostock 源 in `tests/contract/test_baostock_src.py`：fetch_price_history 归一为 RawObservation、复权标注、无资金流能力自描述（注入 fake baostock 模块，先失败）
- [X] T019 [P] [US2] 集成测试增量回填 in `tests/integration/test_baostock_backfill.py`：首次写 N 日+记区间、二次仅补缺零请求、扩窗只补新缺口、单主体失败隔离（先失败）

### Implementation for US2

- [X] T020 [US2] 实现 `src/quantchive/datasource/baostock_src.py`：`BaostockSource(HistorySource)`——bs.login/logout 生命周期(可注入 fake 模块)、fetch_price_history(日/周/月+复权)、归一 RawObservation、无资金流 per [contracts/source-protocol.md](./contracts/source-protocol.md)（使 T018 通过）
- [X] T021 [US2] 扩展 `src/quantchive/service/ingest_service.py`：`backfill_price_history`——查 coverage_dao 缺口→只请求缺口→写 daily_final→更新区间；幂等、单主体隔离、无前视（使 T019 通过）
- [X] T022 [US2] 扩展 `src/quantchive/cli/collect.py`：`backfill --source baostock --metric price_hist --scope stock/sector --days N`，输出「补缺/跳过已存/失败」per [contracts/cli.md](./contracts/cli.md)

**Checkpoint**: 历史时序有真实深度、二次回填仅补缺。**独立可交付。**

---

## Phase 5: US3 - 多源可插拔、主备切换、单源故障不瘫痪 (Priority: P2)

**Goal**: 选源工厂 + 主备路由 + 归一层 + 同花顺资金流备源。

**Independent Test**: 主源 fake 抛限流→切备源；全新 fake 源换源不改上层；缺失源工厂兜底不崩；归一层跨源标度一致。

### Tests for US3（先写，先失败）

- [X] T023 [P] [US3] 契约测试选源工厂 in `tests/contract/test_source_registry.py`：get_source 已注册返实例、缺失返 no-op 兜底不崩、换全新 fake 源上层零改动跑通(SC-005)（先失败）
- [X] T024 [P] [US3] 集成测试主备切换 in `tests/integration/test_source_failover.py`：主源抛 rate_limited→切备源产出、ingestion_run.used_source_code=备源、全源失败抛错记审计（先失败）
- [X] T025 [P] [US3] 单元测试归一层 in `tests/unit/test_normalize.py`：东财/baostock/同花顺 同主体同指标→金额整数分标度一致、禁 float(SC-007)（先失败）

### Implementation for US3

- [X] T026 [US3] 实现 `src/quantchive/datasource/registry.py`：`get_source`/`get_history_source` 工厂 + no-op 兜底源（仿 vnpy 鸭子兜底非 ABC）per [contracts/source-protocol.md](./contracts/source-protocol.md)（使 T023 通过）
- [X] T027 [US3] 实现 `src/quantchive/datasource/routing.py`：`METRIC_SOURCE_ROUTES` + `resolve_sources` + `fetch_with_failover`(主→备切换、返实际用源)（使 T024 通过）
- [X] T028 [US3] 实现 `src/quantchive/datasource/normalize.py`：各源字段/单位/复权→内部约定(金额分/价×1e6/量统一)、输出 RawObservation（使 T025 通过）
- [X] T029 [US3] **[手动/联网 gate]** 同花顺 hexin-v 头小流量验证（真实联网，Phase C 首步）：验证可稳定生成→ths_flow is_active=1；验不过→降级预留 is_active=0 记审计，**US3 其余 mock 任务照常推进不阻塞**（D6 风险前置）
- [X] T030 [US3] 实现 `src/quantchive/datasource/ths_flow_src.py`：`ThsFlowSource`（走 akshare stock_fund_flow 那组，归一为 RawObservation）；接入 routing 作资金流备源
- [X] T031 [US3] 采集接入 failover in `src/quantchive/service/ingest_service.py`：资金流采集走 fetch_with_failover(东财主/同花顺备)、审计 used_source_code

**Checkpoint**: 主源限流自动切备源、换源不改上层。**独立可交付。**

---

## Phase 6: US4 - 采集纪律统一固化 (Priority: P3)

**Goal**: 采集基类固化限流纪律。

**Independent Test**: mock 部分标的首轮失败/过短→分轮重取、超限计失败、max_workers=1。

### Tests for US4（先写，先失败）

- [X] T032 [P] [US4] 单元测试采集基类 in `tests/unit/test_collector_base.py`：失败标的分轮重取、过短判未取全下轮重取、达上限如实计失败、单写者（先失败）

### Implementation for US4

- [X] T033 [US4] 实现 `src/quantchive/datasource/_collector_base.py`：`BaseCollector`——max_workers=1、请求间 delay、retry_map 分轮有限重试、check_min_length 完整性校验（仿 qlib，使 T032 通过）
- [X] T034 [US4] 现有批量采集器（个股/ETF/members/backfill）子类化 `BaseCollector` in `src/quantchive/service/ingest_service.py`：复用统一纪律，行为不回退

**Checkpoint**: 统一采集纪律。**独立可交付。**

---

## Phase 7: Polish & Cross-Cutting

- [X] T035 [P] 更新 [quickstart.md](./quickstart.md) 4 场景端到端验证（mock 部分）跑通
- [X] T036 [P] 更新 [README.md](../../README.md)：多源数据层、baostock 历史回填、节流/缓存、源路由说明
- [X] T037 真实联网验证（限流冷却后手动，非 CI）：同花顺 hexin-v 验证(T029) + baostock 回填 60 天 + 时序图有深度 + 二次回填仅补缺
- [X] T038 全量 `uv run pytest` 全绿（宪章 IV 合并关卡）；确认现有 144 测试零回退

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup(1)** → **Foundational(2)** 阻塞所有故事
- **US1 止血(3)** 先做——MVP、最低风险、改动集中 HTTP 层
- **US2 冷热分离(4)** 依赖 Foundational（coverage_range/HistorySource）；可在 US1 后独立做
- **US3 多源(5)** 依赖 US2（baostock 源）+ Foundational；同花顺 T029 验证前置于 T030/T031
- **US4 纪律(6)** 依赖前述源就绪，最后固化
- **Polish(7)** 最后

### 迁移安全性（贯穿全程）

- Phase 2–6 全程新旧并存，每 Phase 结束 `uv run pytest` 全绿
- `_em_client`/现有源薄壳委托——现有 144 测试不改仍绿
- 观测模型/查询/API/可视化零改动（本期只重构数据获取层）

### Parallel Opportunities

- Setup T002/T003/T004 [P]
- Foundational T008/T009/T010 [P]（不同文件）
- 各故事测试 [P] 先写（T011/T012、T018/T019、T023/T024/T025、T032）
- 同文件串行：`ingest_service.py`(T017/T021/T031/T034)、`_em_client.py`(T015)、`base.py`(T005)

---

## Implementation Strategy

### 止血优先，增量交付

1. Setup + Foundational（依赖 + 通用地基，旧测不破）
2. **US1 止血**（节流+抖动+缓存+零静默降级）← MVP，先解当前最痛的限流
3. US2 冷热分离（baostock 回填 + 增量补缺）← 兑现用户要的历史深度
4. US3 多源（选源工厂 + 主备 + 归一 + 同花顺，先验 hexin-v）
5. US4 纪律固化
6. **STOP & VALIDATE**：真实联网验证（限流冷却后）
7. Polish + README

### Notes

- [P] = 不同文件无依赖；同文件（ingest_service/base/_em_client）串行
- 全程旧测全绿；不改上层观测/查询/可视化
- 新依赖走 uv add；金额整数最小单位禁 float
- 合并关卡（宪章）：无前视 / 数值有测试 / 依赖经 uv add / 结构化审计（降级/用源）/ pytest 全绿
- 同花顺 hexin-v 验证(T029)是 US3 的风险闸门——验不过降级预留，不阻塞其余

### /speckit-analyze 修订（B 路径已应用）

- **I1 术语统一**：metric_kind 全仓用 `money_flow`/`price_hist`/`realtime`（权威定义见 data-model §4）；coverage_dao/routing/normalize/CLI 实现时一律用此命名。
- **U1 差异化节流**：T013 page_sleep 支持按 cache_policy 覆盖（默认统一），闭合 FR-004「历史/实时差异化节流」。
- **U2 拼接**：历史/实时口径一致由 T028 归一 + 现有 series（前 feature 已做 daily/intraday 视图）承担，无需新任务。
- **A1 SC-003 可测化**：验收断言为「二次回填对已存区间的网络请求数=0，仅补缺口」，由 T019 覆盖。
- **C1**：T029 标 [手动/联网 gate]，验不过降级不阻塞 US3 其余。

