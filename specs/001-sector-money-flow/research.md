# Research & Architecture Decisions: 板块资金流

**Feature**: 001-sector-money-flow | **Date**: 2026-07-02

本文档由 9 智能体并行设计（数据模型/采集调度/分层/akshare 适配）+ 对抗式审查收敛而成，消除全部内部冲突，产出唯一决策。对抗审查抓出并修复的高危项：金额跨源单位不一致、`total_net` 废字段、`fetch_daily_final` 签名不符、#7303 毒批、幂等键缺失、双 provider 节奏冲突、枚举反向依赖、时区/交易日历不确定。

## 关键架构决策（Decision / Rationale / Alternatives）

### D1 · 金额精度存储：INTEGER「分」+ 按数据源声明单位标度

- **Decision**：所有金额列一律 `INTEGER`，语义为「有符号整数分（1 元 = 100 分），净流入为正」。**单位换算按数据源分别定义，禁止全局写死 ×100**。东财单位=元（×100），同花顺单位=亿元（×10_000_000_000）。换算强制 `int((Decimal(str(raw)) * SCALE).quantize(Decimal("1"), ROUND_HALF_EVEN))`，先 `str()` 再进 Decimal，绝不 `Decimal(float)`。每条记录额外落 `source_unit`（'yuan'/'yi'）与 `raw_value`（原始字符串）供审计回溯。
- **Rationale**：解决对抗审查发现的硬伤——同花顺若沿用 ×100 会偏小 1e8 倍且排序仍「自洽」不报错。整数分保证 `ORDER BY` 精确可复现（宪章 III、SC-006）。SQLite INTEGER 8 字节，±9.2e16 元，永不溢出。
- **Alternatives 否决**：TEXT Decimal（字典序排序错乱）；REAL（违反宪章 III）；统一元单位（同花顺 2 位小数精度不足以承载元级）。
- **精度诚实**：同花顺净额源精度仅到 0.01 亿（百万元级），契约测试对同花顺只断言到其真实精度，不谎称元级精确。

### D2 · 分钟序列：存累计快照原值，差分在查询层现算

- **Decision**：`sector_money_flow` 存数据源返回的「当日累计净流入」原值。分钟增量（速率）在查询/展示层用相邻整数相减现算，并附带「是否跨断点」标志；跨断点不做差分。
- **Rationale**：累计值是原始观测，信息无损可重放（宪章 I）；差分是派生量，跨断点差分会造假分钟脉冲。全整数相减，无 float。三份审查一致确认这是最强的一处设计。
- **Alternatives 否决**：存差分（丢首值/断点后不可逆）。

### D3 · 调度器技术选型：手写 asyncio 循环，不引入 APScheduler

- **Decision**：随 FastAPI lifespan 启动的自旋 async 循环，不加调度依赖。东财、同花顺**各起一个独立 `asyncio.Task`**，各持独立 `AdaptiveInterval` 与独立 sleep，互不拖累。
- **Rationale**：单机单进程固定间隔轮询是最简周期任务，APScheduler 的 cron/持久化/job store 与自建 IngestionRun 审计职责重叠（YAGNI）。独立 Task 修复审查发现的核心自相矛盾（单个 `self.interval` 无法让两源跑不同间隔）。
- **本期单 worker 约束**：明确以 `uvicorn` 单 worker 运行；多 worker 会双采，本期不支持（文档声明）。

### D4 · Trigger 抽象：`collect_once()` 纯用例 + 全依赖注入

- **Decision**：`collect_once()` 位于 `service/ingest_service.py`，函数体零 `fastapi`/`akshare` 符号，`source/flow_dao/run_dao/sector_dao/clock` 全部注入。A 方案（应用内调度器）与 B 方案（CLI/cron，`cli/collect.py` + `[project.scripts]`）调同一函数。B 方案退避状态从 IngestionRun 读取（无常驻状态也生效）。
- **Rationale**：满足 FR-005「采集一次不依赖 Web，为切独立进程预留」。切 cron = 写 cli.py + 停 lifespan task，业务零改动。

### D5 · DataSource 抽象接口：Protocol + 修正日终接口形状

- **Decision**：`SectorFlowSource` Protocol。关键修正 `fetch_daily_final`：
  - **东财日终 = 收盘后（结算后约 15:40）再调一次 `stock_sector_fund_flow_rank(indicator="今日")`**，单次拿全量板块日终累计值，`value_type='daily_final'`。**不做逐板块 hist 循环**（避免 400+ 次请求的自造限流灾难）。`*_hist` 仅保留给未来历史回补。
  - **同花顺无日频历史源**：日终 = 收盘后末次 `即时` 快照，`value_type='daily_final'` 但 `capabilities().has_daily_final=False`，provenance 诚实标注「末次即时快照近似，非日频确定值」。**不用「3日排行」冒充日终**（滚动聚合，语义错误）。
  - **地域无日终**（且本期不采，见 D8）。
- **换源三保证**：Protocol 依赖倒置 + 形状收口在 `RawSectorFlow`（已 Decimal、单位归一、五档缺失用 None）+ `capabilities()` 能力自描述（上层问能力不问身份，无 `if source==...`）。

### D6 · 盘中失败与收盘补全的数据表达：断点=缺行；value_type 物理隔离

- **Decision**：盘中单次失败 → 有限次重试 + 退避；仍失败**不写任何行**（断点由缺行天然表达，禁写 0/NULL 占位行）。缺口可感知由 `ingestion_run`（`status='partial'/'failed'` + `failures` 明细 + 覆盖的 `minute_slot`）承载。日终补全值 `value_type='daily_final'` 走独立行，**永不 update 盘中 `intraday_snapshot` 行**。
- **审计三态**：`ingestion_run.status ∈ {success, partial, failed, interrupted}`（替换恒 True 的 `ok` 死逻辑）。断点判定基于 `failures` 是否覆盖目标 sector_type，非单一布尔。
- **#7303 持久失败降级**：`uv add akshare` 锁定版本 + CI 冒烟测试固定列名；某口径整调用连续 N 次抛异常 → 升级为高优先级审计告警（非静默 pending），health 端点暴露。

### D7 · 板块主键：系统代理键 `sector_id` + 业务键 (source, sector_type, source_name)

- **Decision**：`sector` 维表用自增 `sector_id` 作稳定真主键；业务唯一键 `UNIQUE(source_code, sector_type, source_symbol)`（`source_symbol`=akshare 板块名称）。事实表外键指向 `sector_id`。可选加固列 `em_board_code`（待实测补，名称变更时靠代码续接同一 `sector_id`）。入库 upsert：命中业务键复用 `sector_id`，未命中新建 + 记审计 `NEW_SECTOR`。
- **Rationale**：东财 rank 接口**只返回名称、不返回代码**（审查核实）。纯名称做键则改名即断裂历史序列（违反宪章 I）。代理键锚定成分归属（宪章 II 生效区间）。
- **跨口径**：本期不自动对齐东财↔同花顺同名板块，按 source 各查各存，不 join。

### D8 · 地域板块：本期不采集

- **Decision**：本期 `capabilities().supported_sector_types` 只含 `industry` + `concept`，**不采集地域（region）**。schema CHECK 保留 'region' 取值供未来。
- **Rationale**：地域无 hist 日终接口 → 永久违反 SC-009；本期永不展示；存无人查又破坏日终一致性的数据违反宪章「复杂度须论证」。等有日终方案再开。

### D9 · 可视化页面：静态 HTML/JS + `/api`，无前端构建链

- **Decision**：`app.mount("/", StaticFiles(directory="web", html=True))`，`web/index.html`+`app.js`+`style.css` 用 `fetch` 调 `/api`。图表用 CDN 版轻量库（Chart.js）；断点（`net_amount=null`）线段中断不连线。金额字符串格式化展示，JS 不做数值加减/排序（后端 Decimal 已定序）。
- **Rationale**：人端与 agent 端消费完全同一 JSON 契约（FR-012 对称性）；避免 SSR 模板分散展示逻辑。

## 存储量估算（对 SC-004 / 保留窗口的可行性判断）

- 两口径约 980 个板块 × 240 分钟/交易日 × 7 天 ≈ **165 万行 / ~415 MB**（含索引）。SQLite 单表无压力，无需分区。
- 扩到 30 天 ≈ 1.8 GB，届时靠 `idx_flow_trade_date` + 周期清理 + `wal_checkpoint(TRUNCATE)` 控制。
- 保留窗口 `RETENTION_TRADE_DAYS` 为配置常量（本期 7，改 30 不动 schema）。

## 时效/日终承诺按口径分别声明（修正 SC-003/SC-009 落差）

- **SC-003（1-3min）**：东财主口径承诺；同花顺副口径「尽力而为、颗粒更粗」。
- **SC-009（每日日终确定值）**：东财口径保证（收盘后 rank 单次）；同花顺为末次快照近似（`has_daily_final=False`，诚实标注）；地域本期不采不承诺。

## 遗留风险与实现期需验证项

### akshare 待实测（`uv add akshare` 后运行验证）

1. `stock_sector_fund_flow_rank(indicator="今日")` 列名前缀完整形态（文档表头与数据示例不一致，需 `.columns.tolist()` 实测锁定 `COLUMN_ALIASES` 双候选兜底）。
   - **2026-07-02 实测结果**：东财接口返回 `ConnectionError / RemoteDisconnected`（服务端拒绝，疑似限流或临时不可用）——**实时列名未能锁定**。适配器已按官方文档已知列名实现 `COLUMN_ALIASES` **多候选兜底**（`名称`/`超大单净流入-净额`/`大单净流入-净额`/`中单净流入-净额`/`小单净流入-净额`/`主力净流入-净额`），并在列名缺失时抛结构化 `schema_drift` 审计。**待网络可用时用真实 `.columns.tolist()` 复核。**
   - **复测（网络恢复后）**：东财 `stock_sector_fund_flow_rank`（行业+概念）**仍 `RemoteDisconnected`**，而同期 `tool_trade_date_hist_sina()`（新浪源，交易日历）**正常返回 8797 行**。→ 确认是**东财该接口特异性拒连**（限流/反爬），非全局网络问题。这正是 research.md 头号风险；适配器容错设计（多候选列名 + 结构化 rate_limited/schema_drift 审计 + 重试退避）覆盖此场景。**MVP 代码结构不受影响；跑真实东财数据需接口恢复或换出口 IP。**
   - **解决（2026-07-02，已落地）**：诊断确认 akshare `stock_sector_fund_flow_rank` 走 `push2.eastmoney.com` **主域名，本机 IP 被限流**。改为**直连东财延迟行情备用域名 `push2delay.eastmoney.com/api/qt/clist/get`**（带浏览器 UA + Referer），实测 HTTP 200、行业 496 + 概念 495 个板块、五档齐全、单位=元。适配器 `EastMoneySource._call_rank` 已从 akshare 改为直连 push2delay（f-code `f14/f62/f66/f72/f78/f84` → 中文列名 → 现有 `_row_to_flow`），`push2` 作为回退 host。`http_get` 可注入（mock 测试）。**真实采集验证通过：`quantchive-collect` 入库 200 行、排行 API 返回真实数据（风力发电主力 +58.46亿等）。**
2. `stock_board_industry_name_em()`/`stock_board_concept_name_em()` 返回列（用于补 `em_board_code`）。
3. `stock_board_industry_cons_em()` 成分股接口（未来下钻，本期不采）。
4. 东财 hist 当日行收盘后可得时点（补 T+1 兜底窗口）。
5. 装完 akshare 版本号复核列名未漂移。

### 已知风险 + 缓解

- **#7303 新板块 `-` 毒批**：异常在 akshare 内部抛出 → `try/except` 包整个 `ak.*()` 调用；持久失败升级高优先级审计告警 + 版本锁定 + CI 冒烟（D6）。
- **同花顺封 IP**：独立 AdaptiveInterval 大幅降频、更少重试、口径级失败隔离；前端诚实标「该口径暂不可用」。
- **单 worker 约束**：多 worker 会双采，本期文档声明单 worker 运行（单实例守卫为后续项）。
- **`total_net` 语义**：东财「总净额」= 主力净额（`net_amount_cents` 存主力值），文档钉死，不合成恒 0 的五档合计。
- **A 方案单点（服务停=采集停）**：Trigger 抽象已备 B 方案 cron；IngestionRun 缺口可查；EOD 补全保证每日至少一条日终值，把「服务停」降级为「分钟序列有洞」。
- **同花顺日终近似**：`has_daily_final=False` + provenance `is_approximate_final`，不冒充日频确定值。

## 已验证的未来数据源（2026-07-03 实测记录）

> 调研开源项目 [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（Apache 2.0，Claude Skill 形态）后的实测结论。**不属于本 spec(001) 范围**，记此以备后续功能引用。

### ✅ 北向资金 · 市场级分钟数据（同花顺 hexin，实测可用）

- **推翻原判断**：spec(001) 阶段认定「北向因监管 2024 改季度披露、免费源已死」而排除北向。经实测，**同花顺 `data.hexin.cn` 接口绕过了东财断供，北向市场级分钟数据是活的**。
- **接口**：`GET https://data.hexin.cn/market/hsgtApi/method/dayChart/`（无 query 参数）
- **必带 header**：`User-Agent: Mozilla/5.0 (Windows NT 10.0...) Chrome/117`、`Host: data.hexin.cn`、`Referer: https://data.hexin.cn/`
- **返回**：JSON `{time:[...], hgt:[...], sgt:[...]}` —— 沪股通(hgt)/深股通(sgt) **当日累计净买入**
- **实测（2026-07-03）**：HTTP 200，**262 个时间点**（09:10–15:00，含集合竞价），分钟级；样例末值 hgt=-9.28亿、sgt=-36.24亿，真实非零。
- **单位**：亿元（对应 `AmountUnit.YI`，`to_cents` ×1e10）
- **边界**：这是**市场级总量**（沪/深股通累计净买入），**不是**「北向归因到板块」。归因到板块仍缺免费数据源。市场级北向分钟曲线本身是大盘外资情绪核心指标，值得独立成新功能 spec(002)。

### ❌ a-stock-data 对板块级资金流无补强

- 该项目的资金流**全是个股级**（东财 push2 单只股票五档分钟流），**无板块级资金净流入金额端点**。
- 其行业板块列表也是东财 `fs=m:90+t:2`，与本项目现用源相同。→ **替代不了也补强不了本项目的板块资金流**；现有东财直连（含翻页）在板块维度更完整。

### 💡 可借鉴的工程理念（非数据源）

- **抗封优先级**：mootdx(通达信 TCP)/腾讯「不封 IP」，东财 push2「有风控会封 IP」——印证本项目遇到的东财限流。但通达信/腾讯**均无板块资金流**，对本功能用不上；未来若做个股行情可引入。
- **个股→板块归属**：东财 `slist` 接口（一次拿 BK码+涨跌+龙头股），可作未来「个股下钻」（FR-018）的参考。
