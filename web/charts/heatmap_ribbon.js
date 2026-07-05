/* 四档热力条带（spec005 US2，ECharts heatmap）。
   行=四档(超大/大/中/小)，列=时间，色=净额(红入绿出、明度=幅度) + 上方对齐价格。
   一屏看全四档时间演化。随 CSS 变量跟随主题。金额只画不算。 */

function renderHeatmapRibbon(dom, data) {
  const chart = initChart(dom);
  const ts = data.points.map((p) => p.ts);
  const rows = ["超大", "大", "中", "小"];
  const keys = ["super_large", "large", "medium", "small"];
  const POS = cssVar("--pos", "#E5484D"), NEG = cssVar("--neg", "#12A150");
  const AXIS_C = cssVar("--chart-axis", "#E3E7ED");
  const LABEL = cssVar("--chart-label", "#8A94A6");
  const TIP_BG = cssVar("--chart-tip-bg", "rgba(255,255,255,0.98)");
  const TIP_BD = cssVar("--chart-tip-bd", "#E3E7ED");
  const TIP_TX = cssVar("--chart-tip-tx", "#1A1F2B");

  // 热力数据 [列(时间), 行(档位), 净额]
  let maxAbs = 0;
  const cells = [];
  data.points.forEach((p, xi) => {
    keys.forEach((k, yi) => {
      const v = p[k] ? parseFloat(p[k].net) : null;
      if (v != null) { maxAbs = Math.max(maxAbs, Math.abs(v)); cells.push([xi, 3 - yi, v]); }
    });
  });
  const priceData = data.points.map((p) => (p.price == null ? null : parseFloat(p.price)));

  chart.setOption({
    backgroundColor: "transparent",
    animationDuration: 300,
    tooltip: {
      position: "top", backgroundColor: TIP_BG, borderColor: TIP_BD, borderWidth: 1,
      textStyle: { color: TIP_TX, fontSize: 12 },
      extraCssText: "border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.18);",
      formatter: (pm) => {
        if (pm.seriesType === "heatmap") {
          const [xi, yi, v] = pm.data;
          return `${ts[xi]}<br/>${rows[3 - yi]}单净额：${v >= 0 ? "+" : ""}${v.toFixed(2)}亿`;
        }
        return `${ts[pm.dataIndex]}<br/>价 ${pm.value}`;
      },
    },
    grid: [
      { left: 44, right: 24, top: 16, height: "22%" },   // 价格
      { left: 44, right: 24, top: "42%", height: "48%" }, // 热力
    ],
    xAxis: [
      { type: "category", data: ts, gridIndex: 0, axisLabel: { show: false },
        axisLine: { lineStyle: { color: AXIS_C } }, axisTick: { show: false }, splitLine: { show: false } },
      { type: "category", data: ts, gridIndex: 1, splitArea: { show: false },
        axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { lineStyle: { color: AXIS_C } }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, scale: true, splitLine: { lineStyle: { color: cssVar("--chart-grid", "rgba(0,0,0,0.06)") } },
        axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false }, name: "价", nameTextStyle: { color: LABEL, fontSize: 10 } },
      { type: "category", data: rows.slice().reverse(), gridIndex: 1, splitArea: { show: true },
        axisLabel: { color: LABEL, fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
    ],
    visualMap: {
      min: -maxAbs, max: maxAbs, calculable: true, orient: "horizontal",
      left: "center", bottom: 0, itemWidth: 12, itemHeight: 120,
      inRange: { color: [NEG, cssVar("--panel-2", "#F2F4F7"), POS] },  // 绿(流出)→中性→红(流入)
      textStyle: { color: LABEL, fontSize: 10 },
    },
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1] }, { type: "slider", xAxisIndex: [0, 1], bottom: 32, height: 12,
      fillerColor: cssVar("--accent-weak", "rgba(46,107,230,0.1)"), handleStyle: { color: cssVar("--accent", "#2E6BE6") } }],
    series: [
      { name: "价格", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
        lineStyle: { color: POS, width: 1.5 }, data: priceData },
      { name: "四档", type: "heatmap", xAxisIndex: 1, yAxisIndex: 1, data: cells,
        itemStyle: { borderColor: "transparent", borderWidth: 0 }, progressive: 0 },
    ],
  });
  return chart;
}
