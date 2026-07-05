/* 资金档位博弈复合主图（spec005 US1，ECharts 长桥/富途风格）。
   三格共享时间轴：① 价格 ② 四档双向堆叠柱 ③ 累计主力vs散户线(蓄水面积)。
   配色模型：红/绿=净流入/流出(net正负)，明度深浅=档位大小(超大→小)。
   A股红涨绿跌；金额只画不算(后端已算好字符串，parseFloat 仅绘图)。 */

// 从 CSS 变量读主题色（随主题切换自动跟随）
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// 流入侧(net>0)：红→橙渐浅；流出侧(net<0)：深绿→浅绿。明度=档位
const TIER_IN  = { super_large: "#D93A3A", large: "#E8613C", medium: "#F08A4B", small: "#F5B26B" };
const TIER_OUT = { super_large: "#12A150", large: "#2BBF6B", medium: "#52CC85", small: "#8AD9AB" };

function num(s) { return s == null ? null : parseFloat(s); }

function renderMainChart(dom, data) {
  const chart = echarts.init(dom, null, { renderer: "canvas" });
  const ts = data.points.map((p) => p.ts);
  // 主题色（随 CSS 变量跟随主题切换）
  const POS = cssVar("--pos", "#E5484D"), NEG = cssVar("--neg", "#12A150");
  const AXIS_LABEL = { color: cssVar("--chart-label", "#8A94A6"), fontSize: 10, fontFamily: "'DIN Alternate','SF Pro Display',sans-serif" };
  const GRID_LINE = { lineStyle: { color: cssVar("--chart-grid", "rgba(16,24,40,0.06)") } };
  const AXIS_C = cssVar("--chart-axis", "#E3E7ED");
  const TIP_BG = cssVar("--chart-tip-bg", "rgba(255,255,255,0.98)");
  const TIP_BD = cssVar("--chart-tip-bd", "#E3E7ED");
  const TIP_TX = cssVar("--chart-tip-tx", "#1A1F2B");
  const ACCENT = cssVar("--accent", "#2E6BE6");

  // 四档柱：每根按 net 正负选色系、按档位选明度
  const tierBar = (key, name) => ({
    name, type: "bar", stack: "tier", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 14,
    data: data.points.map((p) => {
      const v = p[key] ? num(p[key].net) : null;
      if (v == null) return null;
      const color = v >= 0 ? TIER_IN[key] : TIER_OUT[key];
      return { value: v, itemStyle: { color } };
    }),
  });

  const priceData = data.points.map((p) => num(p.price));
  const cumMain = data.points.map((p) => num(p.cum_main_net));
  const cumRetail = data.points.map((p) => num(p.cum_retail_net));

  // 背离段高亮（吸筹绿/派发红，淡）
  const markAreas = (data.divergence || []).map((d) => [
    { xAxis: d.from_ts, itemStyle: { color: d.kind === "accumulation" ? "rgba(44,201,140,0.10)" : "rgba(245,70,92,0.10)" } },
    { xAxis: d.to_ts },
  ]);

  const areaGrad = (rgb) => ({ type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [
    { offset: 0, color: `rgba(${rgb},0.18)` }, { offset: 1, color: `rgba(${rgb},0)` }] });

  const grids = [
    { left: 56, right: 16, top: 28, height: "34%" },
    { left: 56, right: 16, top: "48%", height: "26%" },
    { left: 56, right: 16, top: "80%", height: "16%" },
  ];
  const mkX = (i, showLabel) => ({
    type: "category", data: ts, gridIndex: i,
    axisLine: { lineStyle: { color: AXIS_C } }, axisTick: { show: false },
    splitLine: { show: false }, axisLabel: showLabel ? AXIS_LABEL : { show: false },
    boundaryGap: i === 1,
  });
  const mkY = (i, name) => ({
    type: "value", gridIndex: i, name, nameTextStyle: { color: cssVar("--chart-label", "#99A1AE"), fontSize: 10 },
    scale: i === 0, splitLine: GRID_LINE, axisLine: { show: false }, axisTick: { show: false },
    axisLabel: AXIS_LABEL,
  });

  chart.setOption({
    backgroundColor: "transparent",
    animationDuration: 300,
    tooltip: {
      trigger: "axis",
      backgroundColor: TIP_BG, borderColor: TIP_BD, borderWidth: 1,
      padding: [8, 12], textStyle: { color: TIP_TX, fontSize: 12 },
      extraCssText: "border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.18);",
      axisPointer: {
        type: "cross", animation: false, link: [{ xAxisIndex: "all" }],
        lineStyle: { color: GRID_LINE.lineStyle.color, width: 1, type: "dashed" },
        crossStyle: { color: GRID_LINE.lineStyle.color, width: 1, type: "dashed" },
        label: { backgroundColor: ACCENT, color: "#fff", fontSize: 11 },
      },
    },
    legend: {
      top: 0, itemGap: 16, selectedMode: true, inactiveColor: cssVar("--text-disabled", "#C2C8D2"),
      textStyle: { color: cssVar("--muted", "#626B7A"), fontSize: 11 },
      data: ["价格", "超大", "大", "中", "小", "累计主力", "累计散户"],
    },
    grid: grids,
    xAxis: [mkX(0), mkX(1), mkX(2, true)],
    yAxis: [mkY(0, "价"), mkY(1, "净额亿"), mkY(2, "累计")],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2] },
      { type: "slider", xAxisIndex: [0, 1, 2], bottom: 2, height: 14,
        fillerColor: cssVar("--accent-weak", "rgba(46,107,230,0.10)"), borderColor: "transparent",
        handleStyle: { color: ACCENT }, textStyle: { color: cssVar("--chart-label", "#99A1AE"), fontSize: 10 } },
    ],
    series: [
      { name: "价格", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
        lineStyle: { color: POS, width: 1.5 }, data: priceData,
        markArea: markAreas.length ? { silent: true, data: markAreas } : undefined },
      tierBar("super_large", "超大"), tierBar("large", "大"),
      tierBar("medium", "中"), tierBar("small", "小"),
      { name: "累计主力", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
        lineStyle: { color: POS, width: 1.5 }, areaStyle: { color: areaGrad("245,70,92") }, data: cumMain },
      { name: "累计散户", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
        lineStyle: { color: NEG, width: 1.5 }, areaStyle: { color: areaGrad("44,201,140") }, data: cumRetail },
    ],
  });
  window.addEventListener("resize", () => chart.resize());
  return chart;
}
