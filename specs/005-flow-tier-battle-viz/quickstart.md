# Quickstart: 资金档位博弈可视化 — 验证指南

**Feature**: 005 | 契约见 [contracts/](./contracts/)

端到端验证场景。mock 部分不联网；真实部分标注。

## 前置
```bash
uv sync                # 含 playwright（百度探针）
uv run pytest          # 基线全绿（现 213 + 新增）
```

## 场景 1 · gross 存储 + 流入流出推导（P0，mock）
```bash
uv run pytest tests/unit/test_tier_gross.py -q
```
预期：新浪解析四档 gross+net；`tier_inflow/outflow=(gross±net)//2` 恒整数；"四档gross全有或全无"CHECK；东财行 gross 全 NULL 流入流出为空（诚实缺失）。

## 场景 2 · 多档对齐端点 + 累计 + 背离（P0，mock）
```bash
uv run pytest tests/integration/test_tiers_series.py -q
```
预期：`GET /api/subjects/{id}/tiers_series?granularity=daily` 一次返四档(net+流入+流出)+主力+价+`cum_main_net`(全程绝对累计)+`divergence`段；价跌+主力累计升→标记 accumulation(吸筹)；断点null；has_gross=false 时只返净额。

## 场景 3 · 三级校验（P2，mock）
```bash
uv run pytest tests/unit/test_flow_validate.py -q
```
预期：恒等式失败拒入库记审计；新浪vs百度方向反/量级差>3倍→divergence标记(数据不改)；数值有差但方向量级合理→ok不报。

## 场景 4 · 百度探针（可注入，mock）
```bash
uv run pytest tests/contract/test_baidu_flow_src.py -q
```
预期：注入 fake page（evaluate 返固定 fundFlowSpread JSON）→ 解析四档 turnoverIn/Out/net → gross+net 归一整数分；不联网。

## 场景 5 · 降采取小时末累计（P2，mock）
```bash
uv run pytest tests/unit/test_downsample_tiers.py -q
```
预期：分钟→小时降采,各档 net/gross 取该小时**最后一个分钟点**的累计值(非求和/平均)。

## 场景 6 · 前端四视图（P0-P2，手动看）
```bash
uv run uvicorn quantchive.app:app
# 浏览器 → 个股 → 复合主图A(价格+四档双向堆叠柱+累计主力/散户线)
#   缩放三格联动、十字光标同步、背离段高亮；红涨绿跌
```
预期：中国船舶四档博弈可见、"价跌主力吸筹"背离自动高亮。

## 真实联网验证（手动，非CI）
```bash
# 新浪回填四档 gross（重建库后）
uv run quantchive-collect --target backfill --source sina --days 60
# 百度校验探针（无头浏览器，抽样）
uv run python scripts/probe_baidu.py    # 已验证 4/4
# 盘中（下周开盘）：调度器每分钟归档东财净额 + 探新浪实时是否带gross
```
**通过标准**：个股 tiers_series 四档流入流出非空且满足恒等式；主图博弈+背离可见；校验角标正确；全量 pytest 全绿。
