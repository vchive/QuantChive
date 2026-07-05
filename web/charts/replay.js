/* 盘中逐分钟净额回放（spec005 US3，ECharts timeline）。
   四档净额柱逐分钟"长出" + 价格竖线随帧移动 + play/scrub。盘中只有净额(无流入流出)。
   随 CSS 变量跟随主题。金额只画不算。 */

function renderReplay(dom, data) {
  const chart = echarts.init(dom, null, { renderer: "canvas" });
  const ts = data.points.map((p) => p.ts);
  const keys = ["super_large", "large", "medium", "small"];
  const names = ["超大", "大", "中", "小"];
  const IN = { super_large: "#D93A3A", large: "#E8613C", medium: "#F08A4B", small: "#F5B26B" };
  const OUT = { super_large: "#12A150", large: "#2BBF6B", medium: "#52CC85", small: "#8AD9AB" };
  const POS = cssVar("--pos", "#E5484D");
  const AXIS_C = cssVar("--chart-axis", "#E3E7ED"), LABEL = cssVar("--chart-label", "#8A94A6");
  const GRID = cssVar("--chart-grid", "rgba(0,0,0,0.06)");
  const TIP_BG = cssVar("--chart-tip-bg", "rgba(255,255,255,0.98)");
  const TIP_BD = cssVar("--chart-tip-bd", "#E3E7ED"), TIP_TX = cssVar("--chart-tip-tx", "#1A1F2B");

  const priceAll = data.points.map((p) => (p.price == null ? null : parseFloat(p.price)));

  // 每帧：截止到第 i 分钟的四档柱 + 价格竖线在第 i 帧
  const frames = data.points.map((_, i) => {
    const series = keys.map((k, ki) => ({
      name: names[ki], type: "bar", stack: "t", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 12,
      data: data.points.map((p, xi) => {
        if (xi > i) return null;
        const v = p[k] ? parseFloat(p[k].net) : null;
        if (v == null) return null;
        return { value: v, itemStyle: { color: v >= 0 ? IN[k] : OUT[k] } };
      }),
    }));
    series.unshift({
      name: "价格", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
      lineStyle: { color: POS, width: 1.5 },
      data: priceAll.map((v, xi) => (xi <= i ? v : null)),
      markLine: { silent: true, symbol: "none", lineStyle: { color: POS, type: "dashed", opacity: .5 },
        data: [{ xAxis: i }] },
    });
    return { series };
  });

  const axisCommon = { axisLine: { lineStyle: { color: AXIS_C } }, axisTick: { show: false } };
  chart.setOption({
    baseOption: {
      backgroundColor: "transparent",
      timeline: {
        axisType: "category", data: ts, autoPlay: false, playInterval: 400, loop: false,
        left: 40, right: 40, bottom: 4, symbolSize: 8,
        label: { color: LABEL, fontSize: 10, formatter: (s) => (s || "").slice(-5) },
        lineStyle: { color: AXIS_C }, itemStyle: { color: cssVar("--accent", "#2E6BE6") },
        checkpointStyle: { color: POS, borderColor: "rgba(0,0,0,0)" },
        controlStyle: { color: LABEL, borderColor: LABEL },
      },
      tooltip: { trigger: "axis", backgroundColor: TIP_BG, borderColor: TIP_BD, borderWidth: 1,
        textStyle: { color: TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;" },
      legend: { top: 0, itemGap: 14, textStyle: { color: LABEL, fontSize: 11 },
        data: ["价格", "超大", "大", "中", "小"] },
      grid: [{ left: 44, right: 20, top: 28, height: "38%" }, { left: 44, right: 20, top: "56%", height: "34%" }],
      xAxis: [
        { type: "category", data: ts, gridIndex: 0, axisLabel: { show: false }, splitLine: { show: false }, ...axisCommon },
        { type: "category", data: ts, gridIndex: 1, axisLabel: { color: LABEL, fontSize: 9, formatter: (s) => (s || "").slice(-5) }, splitLine: { show: false }, ...axisCommon, boundaryGap: true },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, scale: true, name: "价", nameTextStyle: { color: LABEL, fontSize: 10 },
          splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
        { type: "value", gridIndex: 1, name: "净额亿", nameTextStyle: { color: LABEL, fontSize: 10 },
          splitLine: { lineStyle: { color: GRID } }, axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
      ],
    },
    options: frames,
  });
  window.addEventListener("resize", () => chart.resize());
  return chart;
}
