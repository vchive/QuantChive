# Contract: 前端可视化（ECharts 四视图）

**Feature**: 005 | 决策 D8 见 [research.md](../research.md)

## 通用约定
- **单库 ECharts**（CDN/vendored 全量，`index.html` 引 `<script>`）。
- **A股配色语义**：红=流入/涨、绿=流出/跌（**不用**绿入红出）。四档同色系明度递进（超大最深→小最浅）；主力(超大+大)暖色组、散户(中+小)冷色组。
- **金额只画不算**：消费 `tiers_series` 端点已算好的整数分/派生值，前端只格式化(分→亿/万)、不做加减（宪章III）。
- **断点**：value=null 不连线；`has_gross=false` 不画流入流出（只画净额）。

## 视图 A — 复合主图（P0，替换 sparkline）
- 三 grid 竖排共享时间轴：① 价格线/K线（红涨绿跌）② 四档双向堆叠柱（净流入向上/流出向下堆，四档明度递进）③ 累计主力线 vs 累计散户线（背离段高亮：吸筹绿框/派发红框）。
- `dataZoom(inside+slider, xAxisIndex 跨grid)` 联动；`axisPointer:{link}` 十字光标同步；`legend` 可隐藏某档。
- 消费 `tiers_series?granularity=daily`。

## 视图 B — 热力条带（P1）
- 4 行（超大/大/中/小）× 时间列，`heatmap` 色=净额（红入绿出、明度=幅度）+ 上方对齐价格 sparkline。一屏看全四档演化。

## 视图 D — 逐分钟回放（P1/P2 盘中）
- `timeline` 外壳 + 四档净额柱逐帧 `appendData`「长出」+ 价格 `markLine` 竖线随帧移动 + play/pause/step/scrub。
- 消费 `tiers_series?granularity=intraday`（`has_gross=false`，只画净额博弈）。历史逐日、盘中逐分钟。

## 视图 C — 对抗图（P2/P3）
- 镜像 area（主力零轴上/散户零轴下）+ 象限 scatter（x=涨跌幅 y=主力净额，自动高亮"价跌主力买=逆势吸筹"象限）。

## 来源角标（D6）
- 读 `provenance.validation`：`ok`→"来源:新浪 ✓"；`divergence`→"来源:新浪 ⚠️与百度口径分歧"（点开看详情）。不弹窗。

## 教学不进产品
- 术语讲解在 `docs/flow-terms-guide.md`（交付用户），产品保持专业（对标东财/同花顺），无新手气泡。
