---
description: "Task list for 005-flow-tier-battle-viz"
---

# Tasks: 资金档位博弈可视化 + 多源数据层增强

**Input**: [plan.md](./plan.md) · [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

**Tests**: 含测试（宪章 IV）。源适配/gross推导/降采/校验/端点/前端逻辑 mock 可测不联网；百度注入 fake page。

**组织**：按用户故事分组，对齐 plan 的 Phase A–F（US1+US2=P1 MVP / US3+US4=P2 / US5=P3）。每 US 独立可交付、可测。

**⚠️ 铁律**：金额整数分、流入流出=(gross±net)//2、累计/算术全后端前端只画、红涨绿跌、校验只标记不改数、盘中只净额（诚实）。每阶段 `uv run pytest` 全绿（基线 213），不破坏上层观测三表核心结构。

## Format: `[ID] [P?] [Story] Description`
- **[P]** 可并行（不同文件无依赖）；**[Story]** US1–US5；每任务含精确文件路径。

---

## Phase 1: Setup

- [ ] T001 确认基线 `uv run pytest` 全绿（213），记回归基准
- [ ] T002 [P] `index.html` 引入 ECharts（CDN 或 vendored 全量包），确认 `echarts` 全局可用
- [ ] T003 [P] `core/settings.py` 加配置：gross校验开关、校验量级阈值(默认3.0)、百度抽样数/轮换、盘中归档频率

## Phase 2: Foundational（阻塞所有故事）

**⚠️ 完成前用户故事不能开始；全程旧测全绿**

- [ ] T004 `dao/schema.sql`：observation 加 4 档 gross 列（super_large/large/medium/small_gross_cents）+ "四档gross全有或全无"CHECK per [data-model.md](./data-model.md)
- [ ] T005 `dao/db_init.py`：幂等 ALTER 加 4 gross 列（旧库）；seed baidu_flow(is_active=0)/tushare_flow(is_active=0，预留)
- [ ] T006 [P] `core/money.py`：加 `tier_inflow(gross,net)`/`tier_outflow(gross,net)` 纯函数（int→int，整除恒精确）
- [ ] T007 [P] 单测 in `tests/unit/test_tier_gross.py`：gross列/CHECK、流入流出整除恒整、gross全NULL时流入流出为空（先失败）
- [ ] T008 `dao/observation_dao.py`：_OBS_COLS + ObservationRow 加 4 gross 字段；upsert 支持写 gross

**Checkpoint**: gross 列 + 派生纯函数就绪，旧测全绿

---

## Phase 3: US1 - 个股四档博弈 + 价格背离（历史四档）(Priority: P1) 🎯 MVP

**Goal**: 新浪四档 gross 入库 + 多档对齐端点（流入流出/全程累计/背离）。

**Independent Test**: 中国船舶四档时序：各档 net+流入+流出（满足恒等式）、cum_main_net 全程累计、价对齐、断点null、价跌主力累计升→标记吸筹。

### Tests for US1（先写先失败）
- [ ] T009 [P] [US1] 契约测试 in `tests/contract/test_tiers_series_api.py`：端点返四档(net+流入+流出)+主力+价+cum_main_net+divergence，has_gross语义，断点null
- [ ] T010 [P] [US1] 单测背离识别 in `tests/unit/test_divergence.py`：价净跌+主力累计净升→accumulation、价升+累计降→distribution（简单方向背离，确定性）
- [ ] T011 [P] [US1] 单测累计 in `tests/unit/test_cum_netflow.py`：全程绝对累计从最早日、后端整数求和

### Implementation for US1
- [ ] T012 [US1] 改 `datasource/sina_flow_src.py`：解析 r0-r3(gross) → 各档 gross_cents 归一（补现有 net 解析）per [contracts/source-adapter.md](./contracts/source-adapter.md)
- [ ] T013 [US1] 改 `service/ingest_service.py` backfill_flow_history：写四档 gross（+现有net）；入库前恒等式自检（坏数据拒入库记审计）
- [ ] T014 [US1] 新 `service/flow_query_service.py`：多档对齐时序 + 流入流出(tier_inflow/outflow) + 全程累计 + 简单方向背离识别（使 T009-T011 通过）
- [ ] T015 [US1] 新 `api/routers/flow.py`：`GET /api/subjects/{id}/tiers_series` 薄路由 → flow_query_service；注册进 app
- [ ] T016 [US1] `dao/observation_dao.py`：tiers_series 多档对齐查询（按 metric_source 源解析，daily 取四档gross+net）

### 前端 US1（视图 A 主图）
- [ ] T017 [US1] 新 `web/charts/main_chart.js`：复合主图 ECharts option（价格 + 四档双向堆叠柱 + 累计主力/散户线，三grid dataZoom联动 + 光标同步 + 背离段高亮）per [contracts/frontend-viz.md](./contracts/frontend-viz.md)
- [ ] T018 [US1] 改 `web/app.js`：个股详情用主图A替换 sparkline，消费 tiers_series；红涨绿跌、金额只格式化不算

**Checkpoint**: 个股四档博弈 + 价格背离看得见（现成60天日线跑满）。**独立可交付 MVP。**

---

## Phase 4: US2 - 专业可视化补全（热力条带）(Priority: P1)

**Goal**: 四档热力条带（一屏看全四档演化），补齐主图外的扫描视图。

**Independent Test**: 4行(超大/大/中/小)×时间heatmap，色=净额红入绿出、明度=幅度 + 对齐价格sparkline。

- [ ] T019 [P] [US2] 新 `web/charts/heatmap_ribbon.js`：四档热力条带 ECharts option（heatmap + 对齐价格sparkline）
- [ ] T020 [US2] 改 `web/app.js`：加"热力条带"视图切换，复用 tiers_series 数据

**Checkpoint**: 主图 + 热力双视图。**独立可交付。**

---

## Phase 5: US3 - 盘中逐分钟净额回放 (Priority: P2)

**Goal**: 盘中每分钟归档东财净额 + 逐分钟回放（只净额，物理天花板）。

**Independent Test**: mock 分钟序列 → timeline回放四档净额柱逐帧 + 价格竖线随帧移动；非交易日优雅提示。

### Tests for US3
- [ ] T021 [P] [US3] 集成测试 in `tests/integration/test_intraday_archive.py`：盘中东财净额每分钟归档为 granularity='1min' 序列（各档只净额，has_gross=false）（先失败）

### Implementation for US3
- [ ] T022 [US3] 改 `scheduler/jobs.py`：盘中分钟归档 job（东财 clist 净额 → 落库 1min 序列，依赖调度器盘中运行）。**东财 clist 各档净额字段已现成**（em_stock_src 已取 f62主力/f66超大/f72大/f78中/f84小 → super_large/large/medium/small_net），归档直接复用现有 stock 采集的四档净额，无需新取字段（analyze U2 已实测确认）
- [ ] T023 [US3] `service/flow_query_service.py`：tiers_series granularity='intraday' 分支（读分钟净额，has_gross=false，只返净额）
- [ ] T024 [US3] 新 `web/charts/replay.js`：逐分钟回放 ECharts（timeline外壳 + 四档净额柱appendData + 价格markLine随帧 + play/scrub）
- [ ] T025 [US3] 改 `web/app.js`：盘中回放视图接线，intraday 粒度只画净额博弈

**Checkpoint**: 盘中主力vs散户净额逐分钟回放。**独立可交付。**

---

## Phase 6: US4 - 多源主备校验 (Priority: P2)

**Goal**: 三级校验（恒等式 + 百度当日对表 + 只标记不改数）+ 前端来源角标。

**Independent Test**: mock 新浪+百度同股当日 → 恒等式坏拒入库、方向反/量级差>3倍标记divergence、口径差异不报、只标记不改数。

### Tests for US4
- [ ] T026 [P] [US4] 单测 in `tests/unit/test_flow_validate.py`：恒等式自检拒坏数据、跨源方向/量级抓异常、口径差异不报（先失败）
- [ ] T027 [P] [US4] 契约测试百度探针 in `tests/contract/test_baidu_flow_src.py`：注入 fake page（evaluate返固定JSON）→ 解析四档 gross+net 归一，不联网（先失败）

### Implementation for US4
- [ ] T028 [US4] 新 `datasource/baidu_flow_src.py`：无头浏览器校验探针（page_factory可注入，page.evaluate内fetch多股，解析fundFlowSpread）per [contracts/source-adapter.md](./contracts/source-adapter.md)
- [ ] T029 [US4] 新 `datasource/validate.py`：check_identity + cross_source_verdict(默认3倍) + record_validation（只标记记审计不改数）
- [ ] T030 [US4] 改 `service/ingest_service.py`：收盘后百度抽样(几十只+轮换) → 跟新浪当日跨源校验 → 记审计
- [ ] T031 [US4] 改 `service/flow_query_service.py` + `api/routers/flow.py`：provenance.validation 角标(ok/divergence)下发
- [ ] T032 [US4] 改 `web/app.js`：数据来源角标显示"✓校验通过/⚠️口径分歧"，不弹窗

**Checkpoint**: 多源校验 + 来源角标。**独立可交付。**

---

## Phase 7: US5 - 板块档位博弈（成分派生）+ 对抗图 (Priority: P3)

**Goal**: 板块四档=成分股求和派生；对抗图（象限散点选股）。

**Independent Test**: mock 板块成分各有四档gross → 板块四档=成分求和(≥覆盖率门禁)，覆盖不足不落假值。

### Tests for US5
- [ ] T033 [P] [US5] 集成测试 in `tests/integration/test_sector_tier_derive.py`：板块四档=成分求和、覆盖率门禁、宁缺勿假（先失败）

### Implementation for US5
- [ ] T034 [US5] 改 `service/ingest_service.py`：板块四档 gross/net 由成分股求和派生（仿 market_aggregate，**复用 settings.market_coverage_threshold 门禁**，≥门禁才落、覆盖不足不落假值；**盘后派生落 is_derived=1 的 observation**，不查询时重算全成分，analyze U1/C1 已锁定）
- [ ] T035 [US5] `service/flow_query_service.py`：板块 tiers_series 读**盘后派生落库的 is_derived 行**（不查询时重算全成分，与 T034 锁定一致）
- [ ] T036 [P] [US5] 新 `web/charts/battle_quadrant.js`：对抗图（主力vs散户镜像面积 + 象限散点，x=涨跌幅 y=主力净额，高亮逆势吸筹象限）
- [ ] T037 [US5] 改 `web/app.js`：板块四档视图 + 对抗图接线

**Checkpoint**: 板块博弈 + 对抗选股。**独立可交付。**

---

## Phase 8: Polish & Cross-Cutting

- [ ] T038 [P] 降采扩展 `service/ingest_service.py` retention_downsample：分钟→小时处理 4档net+4档gross（取小时末累计快照）；+ 单测 `tests/unit/test_downsample_tiers.py`
- [ ] T039 [P] 新 `datasource/tushare_flow_src.py`：预留适配器（能力声明 is_active=0，fetch 抛 NotImplementedError + "未开通token"日志）
- [ ] T040 [P] 新 `docs/flow-terms-guide.md`：术语表（超大单/主力/散户/净流入/流入流出/背离/吸筹/派发）+ 四视图看图指南（交付用户，不进产品）
- [ ] T041 [P] 更新 `README.md`：资金档位博弈可视化 + 多源(新浪gross/百度探针/Tushare预留) + 三层存储 + 校验
- [ ] T042 更新 [quickstart.md](./quickstart.md) 6 场景跑通（mock 部分）
- [ ] T043 真实联网验证（手动，非CI）：重建库→新浪回填四档gross→百度探针抽样校验→起服务看中国船舶主图博弈+背离；盘中(开盘)验证分钟归档 + 探新浪实时gross
- [ ] T044 全量 `uv run pytest` 全绿（宪章合并关卡）；现有 213 测试零回退

---

## Dependencies & Execution Order

- **Setup(1)** → **Foundational(2)** 阻塞所有故事
- **US1(3)** MVP 先做——数据现成、最快见效、含核心博弈+背离
- **US2(4)** 依赖 US1 端点（同 tiers_series 数据）
- **US3(5)** 盘中，依赖调度器（spec003）；独立于 US1/US2
- **US4(6)** 校验，依赖百度源 + US1 入库；独立
- **US5(7)** 板块派生，依赖 US1 个股 gross 就绪
- **Polish(8)** 最后

### 并行机会
- Setup T002/T003 [P]；Foundational T006/T007 [P]
- 各故事测试先写 [P]（T009-11、T021、T026-27、T033）
- 前端 charts 文件互相独立 [P]（main/heatmap/replay/battle）
- 同文件串行：flow_query_service（T014/T023/T031/T035）、ingest_service（T013/T030/T034/T038）、app.js（T018/T020/T025/T032/T037）

### 迁移安全
- gross 列 ADD COLUMN 幂等；全新项目重建走 spec004 流程
- 观测三表核心结构不变（只加列+端点+前端+派生+审计）
- 每 Phase pytest 全绿；旧 series 端点保留（兼容）

---

## Implementation Strategy（MVP 优先）

1. Setup + Foundational（gross列+派生+ECharts引入）
2. **US1 历史四档+主图A** ← MVP，数据现成，个股博弈+背离看得见（最快见效，兑现"先跑起来"）
3. US2 热力（同数据补扫描视图）
4. **STOP & 真实验证**：重建库+新浪回填gross+起服务看主图
5. US3 盘中回放 + US4 校验（P2，盘中依赖开盘验证）
6. US5 板块派生+对抗图（P3）
7. Polish：降采/Tushare预留/术语文档/README/全绿关卡

### Notes
- 盘中只净额是物理约束（东财实时无gross），前端 intraday 只画净额博弈——诚实非缺陷
- 百度探针无头浏览器慢，只做校验抽样不做主力；page 可注入 mock
- Tushare 仅预留 is_active=0，路由跳过
- 教学出文档不进产品；红涨绿跌守 A股语义；金额算术全后端

### /speckit-analyze 修订（B 路径已应用）
- **U2 东财clist各档字段**：已实测确认 em_stock_src 现有采集就取 f62/f66/f72/f78/f84 五档净额 → super_large/large/medium/small_net，盘中归档(T022)直接复用，无需新字段。风险消除。
- **U1 板块派生门禁**：复用 `settings.market_coverage_threshold`（仿大盘求和），T034 已写死。
- **C1 板块派生落库**：锁定"盘后派生落 is_derived=1，查询读派生行"（T034 落库、T035 读），不查询时重算全成分。
- **A1 SC-005 存储**：引用实测估算 1-4GB（数据层讨论已算），非软措辞。
