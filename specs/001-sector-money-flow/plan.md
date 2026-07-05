# Implementation Plan: A股板块资金流入流出可视化

**Branch**: `001-sector-money-flow` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-sector-money-flow/spec.md`

## Summary

采集 A 股行业/概念板块资金流（东财五档 + 同花顺净额双口径），按 1 分钟级盘中快照 + 日终确定值持久化到 SQLite，保留 7 天可回看，通过 FastAPI 只读 API 与静态可视化页面呈现资金强弱排行与时间动态。架构以「人与 agent 共用的单一业务服务层」为核心：DataSource(可插拔) → DAO → QueryService/IngestService → 出口(FastAPI + 预留 Tool)，采集入口 `collect_once()` 与 Web 解耦以预留独立进程/cron。

技术方案由 9 智能体并行设计 + 对抗审查收敛而成，详见 [research.md](./research.md)（决策与依据）、[data-model.md](./data-model.md)（数据模型）、[contracts/](./contracts/)（接口契约）。

## Technical Context

**Language/Version**: Python 3.12（requires-python >= 3.12）

**Primary Dependencies**: FastAPI + Pydantic v2（已装）、akshare（需 `uv add akshare`，带入 pandas）、uvicorn（需 `uv add`）、pydantic-settings（`.env`）；dev: pytest、httpx

**Storage**: SQLite 本地文件（WAL 模式）；金额一律 INTEGER「分」，禁 REAL

**Testing**: pytest，分层 tests/{contract,unit,integration}

**Target Platform**: 本地/单机 Linux/macOS，uvicorn 单 worker 运行

**Project Type**: 单项目 web 服务（src-layout）+ 独立无构建静态前端（web/）

**Performance Goals**: 盘中采集目标间隔 1-3 分钟、硬上限 10 分钟（东财主口径）；排行页面 5 秒内可读（SC-001）；单表 165 万行/415MB（7天）SQLite 无压力

**Constraints**: 金额精度 0 损失、排序可复现（宪章 III/SC-006）；无前视（宪章 II）；采集失败留断点不造假（宪章 I）；数据源限流严格需自适应降频

**Scale/Scope**: 两口径约 980 个板块 × 240 分钟/日 × 7 天；本期只读查询 6 个端点；个股下钻建表留空

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 满足方式 | 状态 |
|---|---|---|
| **I 可复现性** | 金额整数分精确排序；累计值原值存储可重放；`trade_date/minute_slot` 由 Asia/Shanghai 确定性派生（禁机器时区）；交易日历落库；`adapter_version`+`code_version` 固化到每次 ingestion_run；`is_stale` 纯函数派生；批次时间戳统一 | ✅ PASS |
| **II 禁止前视（不可协商）** | `sector_constituent` 生效区间 SCD-2 + as-of 查询结构性杜绝前视；成分锚定稳定 `sector_id`；日终 hist 按 `日期==trade_date` 精确匹配，拒 `iloc[-1]` | ✅ PASS |
| **III 数值精度** | 全金额 INTEGER 分，禁 REAL；按源单位标度换算（东财×100 / 同花顺×1e10）；`Decimal(str(x))` 禁 `Decimal(float)`；整数列排序 + tiebreaker；Decimal 序列化为字符串 | ✅ PASS |
| **IV 测试优先** | tests/{contract,unit,integration} 分层；单位换算/排序稳定性/断点/生效区间/换源(SC-007) 已知答案 fixture；ak 客户端可注入 mock | ✅ PASS |
| **V 可观测/审计** | 每次采集/补全/清理落 `ingestion_run`（三态 status + achieved_interval + 结构化 error_type + failures 明细 + 版本）；结构化 JSON 日志；health 端点暴露 last_run；禁无声吞异常 | ✅ PASS |

**Gate 结论**：全部通过。3 处复杂度需论证（见 Complexity Tracking），均为已确定近期演进或必要基础设施，非过度设计。

## Project Structure

### Documentation (this feature)

```text
specs/001-sector-money-flow/
├── plan.md              # This file (/speckit-plan)
├── research.md          # 架构决策集 D1-D9 + 依据 + 待验证项 (/speckit-plan)
├── data-model.md        # 7 张表最终 DDL (/speckit-plan)
├── quickstart.md        # 端到端验证场景 (/speckit-plan)
├── contracts/           # DataSource/Service/API/采集契约 (/speckit-plan)
│   ├── datasource.md
│   ├── service.md
│   ├── api.md
│   └── ingestion.md
└── tasks.md             # (/speckit-tasks - 尚未生成)
```

### Source Code (repository root)

```text
QuantChive/
├── pyproject.toml            # uv add akshare, uvicorn, pydantic-settings; dev: pytest, httpx
├── web/                      # 静态可视化(非 python 包)
│   ├── index.html
│   ├── app.js                # fetch /api, 图表(Chart.js CDN), 断点不连线, 不做数值运算
│   └── style.css
├── src/quantchive/
│   ├── app.py                # FastAPI 实例, 路由/静态挂载, 异常处理器, lifespan 启调度
│   ├── core/                 # 横切: settings(.env) / db(WAL,连接工厂) / trading_calendar / logging(结构化JSON)
│   ├── models/               # 领域实体(纯数据,零依赖)
│   │   └── enums.py          # Caliber / SectorType / SourceType (最内层, 各层向内引用)
│   ├── datasource/           # 可插拔外部网关(依赖方向向内→models)
│   │   ├── base.py           # SectorFlowSource Protocol + CaliberCapability
│   │   ├── dto.py            # RawSectorFlow (已 Decimal / 单位归一 / 五档 None)
│   │   ├── akshare_src.py    # 东财+同花顺适配, 单位换算, 重试退避, #7303 容错, 可注入 ak 客户端
│   │   └── registry.py       # get_source(source_id)
│   ├── dao/                  # SQLite; SQL 全在此; Decimal↔整数分在此
│   │   ├── schema.sql
│   │   ├── flow_dao.py / sector_dao.py / run_dao.py / constituent_dao.py
│   ├── service/              # 唯一业务真源(人/agent 共用)
│   │   ├── dto.py            # 对外 Pydantic 模型
│   │   ├── errors.py         # QueryError 层级(结构化)
│   │   ├── query_service.py  # 只读查询(本期全部能力)
│   │   └── ingest_service.py # collect_once() 内核 + 日终补全 + 断点策略
│   │   # action_service.py   —— 未来动作型能力占位, 本期不创建
│   ├── scheduler/            # 应用内后台调度(双 provider 双 Task)
│   │   ├── loop.py / interval.py
│   ├── api/                  # 薄 HTTP 出口
│   │   ├── deps.py / errors.py (code→HTTP) / routers/{sectors,calibers,health}.py
│   ├── tools/                # (预留)LangChain Tool 适配, 本期空
│   └── cli/collect.py        # 不依赖 Web 的采集入口(B 方案, [project.scripts])
└── tests/
    ├── contract/             # datasource 形状 / DTO schema / 错误码 / Decimal 序列化为字符串
    ├── unit/                 # 单位换算(0/负/NaN/亿元2位) / 排序稳定性 / 断点 / 生效区间 as-of
    └── integration/          # api 端到端 / collect_once 落库幂等 / 休市回退 is_stale
```

**Structure Decision**：单项目 src-layout + 独立无构建静态 `web/`（不采 backend/frontend 双工程，避免前端构建链）。**依赖方向严格单向**：`api | web | tools → service → dao | datasource → models`。枚举下沉 `models/enums.py` 供所有层向内引用。`service/` 禁 `import fastapi/akshare`；`api/` 禁直连 dao/datasource。人端路由与未来 agent Tool 消费**同一 QueryService 实例**（FR-012）。

## Complexity Tracking

> 宪章要求复杂度必须被论证。以下 3 项超出「本期最小必要」，均有明确理由。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 新增 akshare 依赖（带入 pandas） | 首个数据源实现所必需；DataSource 抽象已隔离，换源不改上层 | 无 akshare 则无数据来源；手写爬虫更脆弱且违背「可插拔」初衷。`uv add akshare` 并锁定版本 + CI 冒烟 |
| instrument / sector_constituent 预留表（建表留空） | FR-018 个股下钻是已确定近期演进；宪章 II 生效区间需预置；建表零运行成本 | 未来再建表会破坏已有历史序列的 schema 迁移；生效区间事后补建无法回溯历史成分（违反宪章 II） |
| trade_calendar 表 | 宪章 I 交易日确定性判定的必要基础设施（保留窗口 cutoff、EOD 触发、交易时段判断全依赖它） | 用「数据源返回空」猜测交易日不确定、不可复现（违反宪章 I）；成本极低 |
