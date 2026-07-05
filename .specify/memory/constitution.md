# QuantChive Constitution

QuantChive 是一个量化交易 / 回测系统。本宪章定义所有功能开发（spec → plan → tasks → implement）必须遵守的不可协商约束。任何 spec、plan、代码若与本宪章冲突，以本宪章为准。

## Core Principles

### I. 可复现性优先 (Reproducibility First)
相同输入必须产生相同输出。所有涉及随机性的逻辑（采样、初始化、模拟）必须接受显式 `seed` 参数并默认固定；回测结果、策略信号、指标计算必须是确定性的（deterministic），不得依赖挂钟时间、机器本地时区或未固定的第三方状态。任何一次回测运行都必须能通过「数据版本 + 代码版本 + 参数 + seed」完全重放。

### II. 禁止前视偏差 (No Look-Ahead — NON-NEGOTIABLE)
回测与信号生成在任意时间点 `t` 只能使用 `t` 时刻及之前已知的数据。严禁使用未来数据（future leakage）：不得用当日收盘价决定当日开盘建仓、不得用尚未公布的财报/因子、不得在特征工程中跨越样本边界泄露标签。所有时间序列操作（滚动窗口、resample、shift）必须显式声明对齐方向，PR 审查必须逐一确认无前视。违反此原则的代码一律不得合并。

### III. 数值与货币精度 (Numerical & Monetary Correctness)
金额、持仓、成交量等货币/份额数值使用 `Decimal` 或整数最小单位表示，严禁用 `float` 直接做金额加减与比较。价格与因子计算允许 `float`，但必须显式处理 NaN/Inf 并记录来源。所有涉及资金的计算（手续费、滑点、盈亏、可用保证金）必须有单元测试覆盖边界值（0、负数、精度截断）。

### IV. 测试优先与回测护栏 (Test-First & Backtest Guardrails)
核心逻辑（策略引擎、撮合/成交模拟、指标计算、风控）采用测试先行：先写会失败的测试，确认失败后再实现。每个策略必须至少有一个「已知答案」的最小回测夹具（fixture），用于回归防止逻辑漂移。数据管道的 schema 变更、成交模型变更、指标公式变更必须有对应契约测试（contract test）。测试不通过不得进入 implement 完成状态。

### V. 可观测性与审计追踪 (Observability & Auditability)
所有交易决策、下单、成交、风控拦截必须产生结构化日志（JSON），包含时间戳、策略 ID、标的、数量、价格、触发原因。回测运行必须落盘可查询的运行记录（存入 SQLite），包含参数快照与结果指标，便于事后审计与对比。禁止「无声吞异常」——错误必须被记录或向上抛出。

## Technology Constraints

技术栈为项目级约束，plan 阶段不得擅自替换，如需引入新的重量级依赖须在 plan 中显式论证并更新本节。

- **语言 / 运行时**：Python，`requires-python >= 3.12`。
- **环境与依赖管理**：统一使用 `uv`（`uv sync` / `uv run` / `uv add`）。严禁使用 `pip install` 直接改环境、严禁手工编辑 `uv.lock`；新增依赖走 `uv add`。
- **数据库**：SQLite（本地文件），用于行情/策略/交易/回测运行记录的持久化。Schema 迁移需可重放。
- **Web / API**：FastAPI + Pydantic v2；对外接口的输入输出必须由 Pydantic 模型约束与校验。
- **AI 能力**：LangChain + OpenAI 兼容接口，仅用于分析/研究/辅助，**不得**直接接入实盘下单链路做未经确认的自动交易决策。
- **配置与密钥**：密钥、API Key 通过环境变量 / `.env` 注入，严禁硬编码或提交进仓库。

## Development Workflow

- **规格驱动**：所有非平凡功能走 Spec Kit 流程 `/speckit-specify → /speckit-clarify → /speckit-plan → /speckit-tasks → /speckit-analyze → /speckit-implement`。spec 只描述 what/why，plan 才描述 how。
- **一功能一分支**：每个功能在独立分支与 `specs/NNN-*/` 目录下开发。
- **审查关卡（Quality Gates）**：合并前必须确认——(1) 无前视偏差；(2) 资金计算有测试；(3) 新增依赖经由 `uv add`；(4) 结构化日志到位；(5) `uv run` 下测试全绿。
- **一致性检查**：实现前用 `/speckit-analyze` 交叉核对 spec/plan/tasks 一致性。

## Governance

本宪章优先于其他一切开发实践。任何 spec、plan、代码评审都必须核对是否符合本宪章；不符合的必须在对应文档中显式说明理由并获批，否则不得进入下一阶段。原则 II（禁止前视偏差）为不可协商项，无例外。修订本宪章须在 PR 中记录变更内容、理由与影响范围，并同步更新受影响的模板与 spec。复杂度必须被论证——若某设计违反「可复现 / 无前视 / 精度」原则，须给出不可避免的理由与缓解措施。

**Version**: 1.0.0 | **Ratified**: 2026-07-02 | **Last Amended**: 2026-07-02
