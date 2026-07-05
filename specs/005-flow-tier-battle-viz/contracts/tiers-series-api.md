# Contract: 多档对齐时序端点

**Feature**: 005 | 决策 D2/D7 见 [research.md](../research.md)

## `GET /api/subjects/{subject_id}/tiers_series`

一次返回四档 + 主力 + 价格 + 累计 + 背离标记的**对齐序列**，避免前端请求 4 次。

**Query 参数**：
- `granularity`: `daily`（默认）| `intraday`（盘中分钟，只有净额）
- `trade_date`: 可选，锚定日；缺省最新
- （复用现有能力门禁：THS源/ETF 无四档时降级）

**响应（Pydantic 模型）**：
```jsonc
{
  "subject_id": 998, "source_symbol": "600150", "display_name": "中国船舶",
  "metric_kind": "money_flow", "granularity": "daily",
  "provenance": { "trade_date": "2026-07-03", "source_id": "sina_flow",
                  "is_stale": false, "validation": "ok|divergence" },  // 来源角标
  "has_gross": true,          // false=只有净额(盘中/东财源)，前端不画流入流出
  "points": [
    { "ts": "2026-05-06",
      "price": "8.38", "change_pct": "-7.91",
      "tiers": {
        "super_large": { "net": "10.71亿分整数", "inflow": ..., "outflow": ... },
        "large":  { "net":..., "inflow":..., "outflow":... },
        "medium": { "net":..., "inflow":..., "outflow":... },
        "small":  { "net":..., "inflow":..., "outflow":... }
      },
      "main_net": ..., "retail_net": ...,
      "cum_main_net": ...,     // 全程绝对累计（后端算）
      "cum_retail_net": ...
    }
    // ... 断点 ts 存在但值为 null，不连线
  ],
  "divergence": [             // 简单方向背离标记（后端算，D7）
    { "from": "2026-05-06", "to": "2026-05-20", "kind": "accumulation" }  // 吸筹/派发
  ],
  "gap_count": 0
}
```

**行为契约（mock 可测）**：
- **流入/流出**：`inflow=(gross+net)//2`、`outflow=(gross-net)//2`，恒整数；`has_gross=false` 时 tiers 只含 net、inflow/outflow 省略（诚实缺失）。
- **累计**：`cum_main_net` = 从主体最早日起 main_net 累加（全程绝对，后端整数求和），随 points 递增下发。
- **背离**：窗口内 `price[末]-price[首]` 与 `cum_main_net[末]-cum_main_net[首]` 方向相反 → 段标记 accumulation(价跌累计升)/distribution(价升累计降)。
- **金额**：整数分下发，前端只格式化显示不算术（宪章III）。
- **能力门禁**：ETF/THS源无四档 → `has_gross=false` 或降级单 main_net 线；板块 → 成分派生（US5）。
- **粒度分级**：daily 读日线四档gross（有流入流出）；intraday 读分钟净额（`has_gross=false`）；断点null；超保留窗 OutOfWindow。

## 旧端点保留
现有 `GET /{id}/series?metric=` 保留（单指标，兼容）；新 `tiers_series` 供四档博弈图。
