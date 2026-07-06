# QuantChive MCP Server 接入指南

把 QuantChive 的市场观测能力(资金流/排行/拓扑/回测取数)暴露成 **MCP 标准工具**,
任何 MCP 客户端(Claude Code、Claude Desktop、Hermes、自研 agent)零胶水即可调用。

## 运行模型

- **stdio 模式**:客户端按需 spawn `quantchive-mcp` 子进程,用完退出。**不常驻额外服务。**
- **只读**:MCP 工具只查不写(采集/回填不在此暴露)。读同一 SQLite(WAL,不阻塞采集写)。
- 你仍只常驻现有 uvicorn(采集+网页);MCP server 由客户端拉起。

## 启动命令

```bash
uv run quantchive-mcp        # stdio,等 JSON-RPC 输入
```

## 接入 Claude Code

项目根 `.mcp.json`(或用户级 MCP 配置):

```json
{
  "mcpServers": {
    "quantchive": {
      "command": "uv",
      "args": ["run", "quantchive-mcp"],
      "cwd": "/Users/liminghan01/Documents/quant/QuantChive"
    }
  }
}
```

或命令行:`claude mcp add quantchive -- uv run quantchive-mcp`(在项目目录)。

## 接入 Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "quantchive": {
      "command": "uv",
      "args": ["--directory", "/Users/liminghan01/Documents/quant/QuantChive", "run", "quantchive-mcp"]
    }
  }
}
```

## 工具清单(14)

| 工具 | 用途 | 面向 |
|---|---|---|
| `list_subjects` | 列主体(拿 subject_id) | 寻址 |
| `describe_capability` | 主体能力自省(有无五档/支持指标) | 寻址 |
| `market_overview` | 大盘五档净额 | 问答 |
| `rank_sectors` | 板块资金流双榜 | 问答/分析 |
| `rank_stocks_in_sector` | 板块内个股排行 | 分析 |
| `scan_market_stocks` | 全市场个股平铺横截面 | 分析 |
| `rank_etf` | ETF 排行 | 问答 |
| `get_series` | 单主体单指标时序 | 分析 |
| `get_tiers_series` | 单股四档资金博弈+背离 | 分析 |
| `flow_topology` | 大盘→行业资金流向拓扑 | 分析 |
| `sector_trends` | 各行业多天净额趋势 | 分析 |
| `get_series_range` | 日线区间取数(回测,绕保留窗) | 策略 |
| `get_series_batch` | 多主体区间批量取数 | 策略 |
| `fund_nav` | 开放式基金净值 | 问答 |

## 数据语义(工具返回)

- **金额是字符串**(整数分→元字符串),不丢精度;前端/agent 只读不算。
- **红涨绿跌**(A股语义);净额正=流入、负=流出。
- **provenance** 透传来源/时点/is_stale;覆盖率诚实标注;断点 null 不造假。
- **无前视**:区间/横截面的 end_date/as_of 严格截断,不返未来数据。
- **错误结构化**:`{"error":{"code","message","detail"}}`,不静默吞。

## 依赖说明

- `mcp[cli]` pin `<2`(v2 类改名 FastMCP→MCPServer 约 2026-07 落地,pin 保命)。
- stdio 日志走 stderr(stdout 留给 JSON-RPC 协议)。
