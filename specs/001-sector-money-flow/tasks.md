---
description: "Task list for 001-sector-money-flow implementation"
---

# Tasks: A股板块资金流入流出可视化

**Input**: Design documents from `specs/001-sector-money-flow/`

**Prerequisites**: [plan.md](./plan.md) · [spec.md](./spec.md) · [research.md](./research.md) · [data-model.md](./data-model.md) · [contracts/](./contracts/)

**Tests**: 包含测试任务 —— 宪章原则 IV「测试优先与回测护栏」为不可协商项，核心逻辑（单位换算/排序/断点/生效区间/采集幂等）测试先行。

**Organization**: 按用户故事分组，每个故事可独立实现与测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1/US2/US3）
- 每个任务含精确文件路径

## Path Conventions

单项目 src-layout：`src/quantchive/`、`tests/`、`web/` 在仓库根。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与基础结构

- [X] T001 创建 src-layout 目录骨架：`src/quantchive/{core,models,datasource,dao,service,scheduler,api,tools,cli}/__init__.py`、`web/`、`tests/{contract,unit,integration}/` per [plan.md](./plan.md) Project Structure
- [X] T002 用 uv 添加运行依赖：`uv add akshare uvicorn pydantic-settings`（禁 pip、禁手改 uv.lock）
- [X] T003 [P] 用 uv 添加开发依赖：`uv add --dev pytest httpx`，并在 `pyproject.toml` 配置 `[tool.pytest.ini_options]`（testpaths、pythonpath=src）
- [X] T004 [P] 在 `pyproject.toml` 添加 `[project.scripts]` 注册 `quantchive-collect = "quantchive.cli.collect:main"`（B 方案 CLI 入口）
- [X] T005 [P] **实测锁定 akshare 列名**：装完后跑 `stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")` 打印 `.columns.tolist()`，记录到 `specs/001-sector-money-flow/research.md` 待验证项并据此确定 `COLUMN_ALIASES`（research.md 风险项 1）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事都依赖的核心基础设施

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事不能开始

- [X] T006 [P] 定义领域枚举 `Caliber`/`SectorType`/`SourceType`/`ValueType`/`SortField` in `src/quantchive/models/enums.py`（最内层，各层向内引用，禁反向依赖）
- [X] T007 [P] 实现结构化配置 in `src/quantchive/core/settings.py`（pydantic-settings 读 `.env`：DB 路径、`RETENTION_TRADE_DAYS=7`、各 provider 间隔/重试参数）
- [X] T008 [P] 实现结构化 JSON 日志 in `src/quantchive/core/logging.py`（宪章 V，禁无声吞异常）
- [X] T009 实现 SQLite 连接工厂 in `src/quantchive/core/db.py`（WAL/foreign_keys=ON/busy_timeout；单 writer 连接 + 分离读连接）per [data-model.md](./data-model.md) 全局约定
- [X] T010 编写建表脚本 `src/quantchive/dao/schema.sql`：7 张表全部 DDL（data_source/sector/sector_money_flow/ingestion_run/trade_calendar/instrument/sector_constituent）+ 全部索引 + CHECK 约束 per [data-model.md](./data-model.md)
- [X] T011 [P] 单元测试 schema：应用 schema.sql 后校验 `uq_flow` 幂等唯一索引、五档 all-or-nothing CHECK、FK 生效 in `tests/unit/test_schema.py`（先失败）
- [X] T012 实现 schema 初始化/迁移应用逻辑 in `src/quantchive/dao/db_init.py`（幂等建表），使 T011 通过
- [X] T013 [P] 实现交易日历 `src/quantchive/core/trading_calendar.py`：`trade_calendar` 表同步（akshare `tool_trade_date_hist_sina`）+ 交易日判定 + 交易时段(含午休)判定 + `Clock` 契约(返回 aware datetime, Asia/Shanghai)，per [contracts/ingestion.md](./contracts/ingestion.md)
- [X] T014 [P] 单元测试交易日历：交易日/非交易日/午休时段/时区确定性 in `tests/unit/test_trading_calendar.py`
- [X] T015 [P] 定义金额换算工具 `src/quantchive/core/money.py`：`to_cents(raw_str, unit)` 用 `Decimal(str(x))` + ROUND_HALF_EVEN，禁 `Decimal(float)`（宪章 III，D1）
- [X] T016 [P] 单元测试金额换算：东财元(×100)/同花顺亿(×1e10)/0/负/NaN/亿元2位小数边界 in `tests/unit/test_money.py`（东财 `-1.411231e+08`元→`-14112310000`分；同花顺 `1.23`亿→`12300000000`分）

**Checkpoint**: 基础设施就绪（枚举/配置/日志/DB/schema/交易日历/金额换算）—— 用户故事可开始

---

## Phase 3: User Story 1 - 查看板块资金流排行 (Priority: P1) 🎯 MVP

**Goal**: 采集东方财富行业/概念板块当日资金流入库，通过 API + 页面展示按主力净额排序的 Top N 净流入/净流出聚焦榜 + 查看全部。

**Independent Test**: 有当日东财数据时，`/api/sectors/ranking` 返回按主力净额正确排序、金额准确、区分流入流出的排行；打开首页 5 秒内可读（SC-001）。

### Tests for User Story 1 (测试先行) ⚠️

- [X] T017 [P] [US1] 契约测试 DataSource 形状：fake source 实现 `SectorFlowSource` Protocol，返回 `RawSectorFlow` 形状正确 in `tests/contract/test_datasource_shape.py`（先失败）
- [X] T018 [P] [US1] 契约测试 `/api/sectors/ranking`：200 正常 + 金额序列化为字符串 + 双榜方向 in `tests/contract/test_api_ranking.py`（先失败）
- [X] T019 [P] [US1] 单元测试排序稳定性：相同批数据排序结果可复现，tiebreaker `sector_id ASC`，流出榜=最负 N in `tests/unit/test_ranking_sort.py`（先失败）
- [X] T020 [P] [US1] 集成测试 collect_once 幂等：同分钟重跑不产生重复行（uq_flow），单板块失败不影响其他板块 in `tests/integration/test_collect_once.py`（先失败）

### Implementation for User Story 1

- [X] T021 [P] [US1] 定义 `RawSectorFlow` dataclass in `src/quantchive/datasource/dto.py`（已 Decimal/单位归一到元/五档 None）per [contracts/datasource.md](./contracts/datasource.md)
- [X] T022 [P] [US1] 定义 `SectorFlowSource` Protocol + `CaliberCapability` in `src/quantchive/datasource/base.py`
- [X] T023 [US1] 实现 akshare 东财适配 `fetch_snapshot`(eastmoney, industry/concept) in `src/quantchive/datasource/akshare_src.py`：列名映射(COLUMN_ALIASES)、单位换算(元×100)、重试退避、#7303/空/NaN 容错、可注入 ak 客户端 per [contracts/ingestion.md](./contracts/ingestion.md)（使 T017 通过）
- [X] T024 [P] [US1] 实现 source registry `get_source(source_id)` in `src/quantchive/datasource/registry.py`
- [X] T025 [P] [US1] 实现 sector_dao（upsert 得 sector_id，新板块记 NEW_SECTOR）in `src/quantchive/dao/sector_dao.py`
- [X] T026 [P] [US1] 实现 run_dao（ingestion_run 三态审计写入/查询）in `src/quantchive/dao/run_dao.py`
- [X] T027 [US1] 实现 flow_dao（`INSERT ... ON CONFLICT(uq_flow) DO UPDATE` 幂等写入 + 排行/序列查询 SQL，Decimal↔整数分转换）in `src/quantchive/dao/flow_dao.py`（依赖 T025/T026）
- [X] T028 [US1] 实现 `collect_once()` 内核 in `src/quantchive/service/ingest_service.py`：全依赖注入、批次时间戳统一、断点=缺行、三态 status、achieved_interval 落审计 per [contracts/ingestion.md](./contracts/ingestion.md)（使 T020 通过，依赖 T023/T027）
- [X] T029 [P] [US1] 定义对外 Pydantic 模型 `SectorFlowItem`/`DataProvenance`/`SectorRankingResult` in `src/quantchive/service/dto.py`（金额 Decimal 序列化为字符串）per [contracts/service.md](./contracts/service.md)
- [X] T030 [P] [US1] 定义结构化错误 `QueryError` 层级 in `src/quantchive/service/errors.py`
- [X] T031 [US1] 实现 `QueryService.get_ranking()` in `src/quantchive/service/query_service.py`：可选排序字段(默认主力净额)、Top N 双榜 + full 全量、当日取最新 intraday、tiebreaker、当日无数据结构化错误（使 T019 通过，依赖 T027/T029/T030）
- [X] T032 [US1] 实现 ranking 路由 `/api/sectors/ranking` in `src/quantchive/api/routers/sectors.py`（薄路由，调 QueryService）per [contracts/api.md](./contracts/api.md)
- [X] T033 [US1] 实现 API 异常处理器 `QueryError.code → HTTP` in `src/quantchive/api/errors.py` + 统一响应体 `{"error":{...}}`
- [X] T034 [US1] 组装 FastAPI app in `src/quantchive/app.py`：注册路由、异常处理器、挂载 `web/` 静态（使 T018 通过）
- [X] T035 [P] [US1] 实现最简可视化页面 `web/index.html` + `web/app.js` + `web/style.css`：fetch ranking、Top N 双榜展示、金额字符串直接显示（不做 JS 数值运算）per [contracts/api.md](./contracts/api.md) D9
- [X] T036 [US1] 实现 CLI 采集入口 `src/quantchive/cli/collect.py`（不依赖 Web，调 collect_once，供手动灌数据以测试 US1）

**Checkpoint**: US1 完整可独立测试 —— 能采东财数据入库、API 返回排行、页面可看。**这是 MVP。**

---

## Phase 4: User Story 2 - 切换板块口径（东财/同花顺）(Priority: P2)

**Goal**: 接入同花顺口径（仅净额、无五档），用户可在东财/同花顺间切换；界面标示当前口径并诚实告知同花顺不提供五档、不提供某排序字段。

**Independent Test**: 两口径数据都在时切换，排行随口径变、五档仅东财出现、对同花顺请求五档排序返回 422 而非伪造 0（US2 场景）。

### Tests for User Story 2 (测试先行) ⚠️

- [ ] T037 [P] [US2] 契约测试 `/api/calibers`：ths 的 `has_five_tier=false`、`available_sort_fields` 不含各单档 in `tests/contract/test_api_calibers.py`（先失败）
- [ ] T038 [P] [US2] 契约测试同花顺不支持的排序 → 422 `METRIC_NOT_SUPPORTED` in `tests/contract/test_metric_not_supported.py`（先失败）
- [ ] T039 [P] [US2] 单元测试同花顺五档字段全为 None（不提供，非 0）in `tests/unit/test_ths_no_five_tier.py`（先失败）

### Implementation for User Story 2

- [ ] T040 [US2] 扩展 akshare 适配 `fetch_snapshot`(ths, industry/concept) in `src/quantchive/datasource/akshare_src.py`：同花顺即时快照、单位换算(亿×1e10)、五档置 None、更严限流降频 per [contracts/ingestion.md](./contracts/ingestion.md)（使 T039 通过）
- [ ] T041 [P] [US2] 实现 `capabilities()` per-caliber 能力声明 in `src/quantchive/datasource/akshare_src.py`（东财 has_five_tier/has_daily_final=True；同花顺=False）
- [ ] T042 [P] [US2] 定义 `CaliberMeta` Pydantic 模型 in `src/quantchive/service/dto.py`
- [ ] T043 [US2] 实现 `QueryService.get_caliber_meta()`/`list_calibers()`/`list_sectors()` + `get_ranking` 中 sort_by 能力校验（不支持→MetricNotSupported）in `src/quantchive/service/query_service.py`（使 T038 通过）
- [ ] T044 [US2] 实现 `/api/calibers`、`/api/calibers/{caliber}`、`/api/sectors` 路由 in `src/quantchive/api/routers/calibers.py`（使 T037 通过）
- [ ] T045 [US2] 页面增加口径切换控件 in `web/app.js` + `web/index.html`：切换刷新排行、标示当前口径、同花顺明示「不提供五档」

**Checkpoint**: US1 + US2 均可独立工作 —— 双口径可切换、能力诚实自描述

---

## Phase 5: User Story 3 - 单板块分钟序列与 7 天回看 (Priority: P3)

**Goal**: 展示单板块当日盘中 1 分钟级资金流序列，并可回看保留窗口(7天)内任一交易日的分钟序列；采集失败断点如实呈现、超窗口明确告知；收盘补全日终确定值。

**Independent Test**: 有多日分钟序列的板块，`/api/sectors/{name}/series` 返回当日+回看序列、断点 net=null 不连线、超 7 天返回 422（US3 场景）。

### Tests for User Story 3 (测试先行) ⚠️

- [ ] T046 [P] [US3] 契约测试 `/api/sectors/{name}/series`：正常序列 + 断点 net_amount=null + gap_count in `tests/contract/test_api_series.py`（先失败）
- [ ] T047 [P] [US3] 契约测试超保留窗口 → 422 `OUT_OF_RETENTION_WINDOW` in `tests/contract/test_out_of_window.py`（先失败）
- [ ] T048 [P] [US3] 单元测试 is_stale 由 (trade_date, as_of, calendar) 纯函数派生（休市回退）in `tests/unit/test_is_stale.py`（先失败）
- [ ] T049 [P] [US3] 集成测试 EOD 收盘补全：daily_final 走独立 'EOD' 行、永不 update 盘中行、每交易日至少一条日终值(SC-009) in `tests/integration/test_eod_backfill.py`（先失败）

### Implementation for User Story 3

- [ ] T050 [P] [US3] 定义 `MinutePoint`/`SectorSeriesResult` Pydantic 模型 in `src/quantchive/service/dto.py`
- [ ] T051 [US3] 实现 `QueryService.get_sector_series()` in `src/quantchive/service/query_service.py`：按板块+交易日取分钟序列、断点 net=None + gap_count、超窗口 OutOfRetentionWindow、is_stale 派生（使 T046/T047/T048 通过）
- [ ] T052 [US3] 实现 series 路由 `/api/sectors/{sector_name}/series` in `src/quantchive/api/routers/sectors.py`
- [ ] T053 [US3] 实现 `fetch_daily_final` in `src/quantchive/datasource/akshare_src.py`：东财收盘后单次 rank 全量、同花顺末次快照近似(is_approximate_final)、拒 iloc[-1] per [contracts/ingestion.md](./contracts/ingestion.md)
- [ ] T054 [US3] 实现 EOD 补全 + 保留窗口清理逻辑 in `src/quantchive/service/ingest_service.py`（daily_final 独立行、retention_cleanup 落审计）（使 T049 通过，依赖 T053）
- [ ] T055 [US3] 页面增加单板块分钟序列图 in `web/app.js`：Chart.js 折线、断点不连线、当日/回看日期选择

**Checkpoint**: 三个用户故事均独立可用

---

## Phase 6: 调度器与自动化（跨故事集成）

**Purpose**: 把 collect_once 接入应用内后台调度，实现盘中自动采集 —— US1/US2/US3 的数据自动流入

- [ ] T056 [P] 实现 `AdaptiveInterval`（AIMD：成功收紧/限流乘性放宽≤600s/软失败温和放宽，可注入 RNG）in `src/quantchive/scheduler/interval.py`
- [ ] T057 [P] 单元测试 AdaptiveInterval：限流放宽封顶 10min、成功收紧至 target in `tests/unit/test_interval.py`
- [ ] T058 实现后台调度循环 in `src/quantchive/scheduler/loop.py`：东财/同花顺各独立 asyncio.Task 各持独立 AdaptiveInterval、交易时段判断、POST_CLOSE 分支(EOD 补全→清理)、优雅停止(interrupted) per [contracts/ingestion.md](./contracts/ingestion.md)
- [ ] T059 接入 FastAPI lifespan in `src/quantchive/app.py`：启动拉起调度 Task、关闭优雅停止（依赖 T058）
- [ ] T060 实现 `/api/health` in `src/quantchive/api/routers/health.py`：暴露 latest_trade_date、last_run、per_caliber_last_run（宪章 V，FR-015）

**Checkpoint**: 服务启动即自动盘中采集 + 收盘补全 + 保留清理

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事完善

- [ ] T061 [P] 单元测试 sector_constituent as-of 查询无前视（宪章 II，即使表本期留空也验证查询 SQL 正确）in `tests/unit/test_constituent_asof.py`
- [ ] T062 [P] 契约测试换源不改上层：fake source 替换 akshare 跑通排行（SC-007）in `tests/contract/test_source_swappable.py`
- [ ] T063 [P] 更新 [README.md](../../README.md)：运行方式（`uv run uvicorn ...` 单 worker）、CLI 采集、单 worker 约束说明
- [ ] T064 [P] 添加 `.env.example`（DB 路径、保留天数、provider 参数；密钥占位，禁提交真值）
- [ ] T065 运行 [quickstart.md](./quickstart.md) 全部 6 个验证场景，确认端到端通过
- [ ] T066 全量 `uv run pytest`，确认 contract/unit/integration 全绿（宪章 IV 合并关卡）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即开始
- **Foundational (Phase 2)**: 依赖 Setup —— 阻塞所有用户故事
- **User Stories (Phase 3-5)**: 均依赖 Foundational
  - US1(P1) 是 MVP，含采集→入库→API→页面完整链路
  - US2/US3 依赖 Foundational，实现上复用 US1 建立的 datasource/dao/service/api 骨架
- **调度器 (Phase 6)**: 依赖 US1 的 collect_once（T028）与 US3 的 EOD 补全（T054）
- **Polish (Phase 7)**: 依赖所需用户故事完成

### User Story Dependencies

- **US1 (P1)**: Foundational 后即可开始 —— 无其他故事依赖，独立成 MVP
- **US2 (P2)**: Foundational 后可开始；扩展 US1 的 akshare 适配与 service（同花顺口径），独立可测
- **US3 (P3)**: Foundational 后可开始；新增 series 查询与 EOD 补全，独立可测

### Within Each User Story

- 测试先写且先失败（宪章 IV）→ 模型 → DAO → Service → API → 页面
- 同文件顺序任务不可并行（如 akshare_src.py 的 T023/T040/T053 顺序改同一文件）

### Parallel Opportunities

- Setup 中 T003/T004/T005 [P] 可并行
- Foundational 中 T006/T007/T008、T013/T015 及各自测试 [P] 可并行
- 各故事测试任务 [P] 可并行先写
- US1 中 T021/T022/T024/T025/T026/T029/T030/T035 [P] 可并行（不同文件）
- Foundational 完成后，若人手足够 US1/US2/US3 可并行（注意 akshare_src.py 同文件需串行）

---

## Parallel Example: User Story 1

```bash
# 先并行写 US1 全部测试(应先失败):
Task: "契约测试 DataSource 形状 in tests/contract/test_datasource_shape.py"
Task: "契约测试 /api/sectors/ranking in tests/contract/test_api_ranking.py"
Task: "单元测试排序稳定性 in tests/unit/test_ranking_sort.py"
Task: "集成测试 collect_once 幂等 in tests/integration/test_collect_once.py"

# 再并行创建 US1 无依赖的模型/DTO/DAO(不同文件):
Task: "RawSectorFlow dataclass in src/quantchive/datasource/dto.py"
Task: "SectorFlowSource Protocol in src/quantchive/datasource/base.py"
Task: "sector_dao in src/quantchive/dao/sector_dao.py"
Task: "run_dao in src/quantchive/dao/run_dao.py"
Task: "对外 Pydantic 模型 in src/quantchive/service/dto.py"
Task: "结构化错误 in src/quantchive/service/errors.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. 完成 Phase 1 Setup + Phase 2 Foundational
2. 完成 Phase 3 US1（东财单口径：采集→入库→排行 API→页面）
3. 手动跑 T036 CLI 灌一批当日数据
4. **STOP & VALIDATE**：`/api/sectors/ranking` + 首页排行独立可用
5. 可选：接 Phase 6 调度器让采集自动化

### Incremental Delivery

1. Setup + Foundational → 地基就绪
2. US1 → 独立测试 → MVP（东财排行可看）
3. US2 → 独立测试 → 双口径可切换
4. US3 → 独立测试 → 分钟序列与回看
5. Phase 6 调度器 → 全自动采集
6. Phase 7 → 换源验证/quickstart/全量测试绿

---

## Notes

- [P] = 不同文件、无依赖
- akshare_src.py 由 T023(东财snapshot)→T040(同花顺)→T053(日终) 顺序扩展，**不可并行**
- query_service.py 由 T031(ranking)→T043(caliber/meta)→T051(series) 顺序扩展
- 金额全程整数分，JSON 序列化为字符串（宪章 III/SC-006），前端不做数值运算
- 每个任务或逻辑组后提交；任一 checkpoint 可停下独立验证
- 合并关卡（宪章）：无前视 / 资金计算有测试 / 依赖经 uv add / 结构化日志 / `uv run pytest` 全绿
