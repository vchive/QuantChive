/* 主力vs散户对抗图（spec005 US5，ECharts）。
   上：主力vs散户累计净额镜像面积（分歧张口=博弈强度）。
   下：象限散点 x=涨跌幅 y=主力净额，自动高亮"价跌主力买=逆势吸筹"象限。
   随 CSS 变量跟随主题。金额只画不算。 */

function renderBattleQuadrant(dom, data) {
  const chart = initChart(dom);
  const ts = data.points.map((p) => p.ts);
  const POS = cssVar("--pos", "#E5484D"), NEG = cssVar("--neg", "#12A150");
  const AXIS_C = cssVar("--chart-axis", "#E3E7ED"), LABEL = cssVar("--chart-label", "#8A94A6");
  const GRID = cssVar("--chart-grid", "rgba(0,0,0,0.06)");
  const TIP_BG = cssVar("--chart-tip-bg", "rgba(255,255,255,0.98)");
  const TIP_BD = cssVar("--chart-tip-bd", "#E3E7ED"), TIP_TX = cssVar("--chart-tip-tx", "#1A1F2B");

  const num = (s) => (s == null ? null : parseFloat(s));
  const cumMain = data.points.map((p) => num(p.cum_main_net));
  const cumRetail = data.points.map((p) => num(p.cum_retail_net));

  // 象限散点：每天一个点 [涨跌幅, 主力净额]，逆势吸筹象限(价跌+主力买)高亮
  const scatter = data.points.map((p) => {
    const chg = num(p.change_pct), mn = num(p.main_net);
    if (chg == null || mn == null) return null;
    const accum = chg < 0 && mn > 0;          // 价跌主力买=逆势吸筹
    const dist = chg > 0 && mn < 0;           // 价涨主力卖=逆势派发
    return { value: [chg, mn], itemStyle: {
      color: accum ? POS : dist ? NEG : cssVar("--text-3", "#99A1AE"),
      opacity: (accum || dist) ? 0.9 : 0.4,
      borderColor: accum ? POS : "transparent", borderWidth: accum ? 1.5 : 0 } };
  }).filter(Boolean);

  chart.setOption({
    backgroundColor: "transparent", animationDuration: 300,
    tooltip: { backgroundColor: TIP_BG, borderColor: TIP_BD, borderWidth: 1,
      textStyle: { color: TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      formatter: (pm) => {
        if (pm.seriesType === "scatter") {
          const [chg, mn] = pm.value;
          const tag = chg < 0 && mn > 0 ? "（逆势吸筹）" : chg > 0 && mn < 0 ? "（逆势派发）" : "";
          return `涨跌 ${chg.toFixed(2)}%<br/>主力净额 ${mn >= 0 ? "+" : ""}${mn.toFixed(2)}亿 ${tag}`;
        }
        return `${ts[pm.dataIndex]}<br/>${pm.seriesName} ${pm.value}`;
      } },
    legend: { top: 0, itemGap: 14, textStyle: { color: LABEL, fontSize: 11 },
      data: ["累计主力", "累计散户"] },
    grid: [{ left: 52, right: 20, top: 28, height: "38%" }, { left: 52, right: 20, top: "60%", height: "34%" }],
    xAxis: [
      { type: "category", data: ts, gridIndex: 0, axisLabel: { color: LABEL, fontSize: 9 },
        axisLine: { lineStyle: { color: AXIS_C } }, axisTick: { show: false }, splitLine: { show: false } },
      { type: "value", gridIndex: 1, name: "涨跌幅%", nameLocation: "middle", nameGap: 22,
        nameTextStyle: { color: LABEL, fontSize: 10 }, axisLabel: { color: LABEL, fontSize: 10 },
        axisLine: { lineStyle: { color: AXIS_C } }, splitLine: { lineStyle: { color: GRID } },
        axisTick: { show: false } },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, name: "累计净额", nameTextStyle: { color: LABEL, fontSize: 10 },
        axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: GRID } } },
      { type: "value", gridIndex: 1, name: "主力净额亿", nameTextStyle: { color: LABEL, fontSize: 10 },
        axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false },
        splitLine: { lineStyle: { color: GRID } } },
    ],
    series: [
      { name: "累计主力", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
        lineStyle: { color: POS, width: 1.5 }, areaStyle: { color: `rgba(245,70,92,0.10)` }, data: cumMain },
      { name: "累计散户", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false,
        lineStyle: { color: NEG, width: 1.5 }, areaStyle: { color: `rgba(44,201,140,0.10)` }, data: cumRetail },
      { name: "象限", type: "scatter", xAxisIndex: 1, yAxisIndex: 1, symbolSize: 9, data: scatter,
        markLine: { silent: true, symbol: "none", lineStyle: { color: LABEL, type: "dashed", opacity: .4 },
          data: [{ xAxis: 0 }, { yAxis: 0 }] } },
    ],
  });
  return chart;
}
