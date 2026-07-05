/* 资金流向拓扑（spec006，ECharts Sankey + Treemap）。
   桑基：大盘→行业→个股，线宽=|净额|(恒正)、红=净流入/绿=净流出、hover高亮全链路。
   Treemap：行业内个股全景，面积=成交额gross、色=净额方向。
   随 CSS 变量跟随主题。金额只画不算(后端出字符串，parseFloat 仅绘图)。 */

function _topoColors() {
  return {
    POS: cssVar("--pos", "#E5484D"), NEG: cssVar("--neg", "#12A150"),
    FLAT: cssVar("--flat", "#8A94A6"), LABEL: cssVar("--chart-label", "#8A94A6"),
    TIP_BG: cssVar("--chart-tip-bg", "rgba(255,255,255,0.98)"),
    TIP_BD: cssVar("--chart-tip-bd", "#E3E7ED"), TIP_TX: cssVar("--chart-tip-tx", "#1A1F2B"),
  };
}
function _dirColor(dir, c) { return dir === "in" ? c.POS : dir === "out" ? c.NEG : c.FLAT; }
function _yi(yuan) { const n = parseFloat(yuan); return (n / 1e8).toFixed(2); }  // 元→亿显示

/* 桑基主图。data=topology 响应；onSectorClick(sectorId) 点行业下钻 */
function renderFlowSankey(dom, data, onSectorClick) {
  const chart = echarts.init(dom, null, { renderer: "canvas" });
  const c = _topoColors();
  const nodes = data.nodes.map((n) => ({
    name: n.id, _label: n.name, depth: n.depth, _sid: n.subject_id,
    _net: n.net_yuan, _dir: n.direction,
    itemStyle: { color: _dirColor(n.direction, c), borderColor: "transparent" },
    label: { formatter: () => n.name, color: c.TIP_TX, fontSize: 11 },
  }));
  const links = data.links.map((l) => ({
    source: l.source, target: l.target, value: Math.abs(parseFloat(l.abs_value)) || 1,
    _signed: l.signed_value, _dir: l.direction,
    lineStyle: { color: _dirColor(l.direction, c), opacity: 0.45 },
  }));

  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item", backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.18);",
      formatter: (pm) => {
        if (pm.dataType === "edge") {
          const s = parseFloat(pm.data._signed);
          return `${pm.data._dir === "in" ? "净流入" : pm.data._dir === "out" ? "净流出" : "持平"}<br/>` +
            `<b>${s >= 0 ? "+" : ""}${_yi(pm.data._signed)} 亿</b>`;
        }
        const s = parseFloat(pm.data._net);
        return `${pm.data._label}<br/>净额 <b>${s >= 0 ? "+" : ""}${_yi(pm.data._net)} 亿</b>`;
      },
    },
    series: [{
      type: "sankey", orient: "horizontal", nodeAlign: "left",
      left: 12, right: 120, top: 20, bottom: 20,
      nodeGap: 10, nodeWidth: 14, layoutIterations: 32,
      emphasis: { focus: "adjacency" },
      lineStyle: { curveness: 0.5 },
      data: nodes, links: links,
    }],
  });
  // 点击行业节点 → 下钻
  chart.on("click", (pm) => {
    if (pm.dataType === "node" && pm.data.depth === 1 && pm.data._sid != null && onSectorClick) {
      onSectorClick(pm.data._sid, pm.data._label);
    }
  });
  window.addEventListener("resize", () => chart.resize());
  return chart;
}

/* Treemap：行业内个股。面积=gross、色=净额方向 */
function renderFlowTreemap(dom, data) {
  const chart = echarts.init(dom, null, { renderer: "canvas" });
  const c = _topoColors();
  const stocks = data.nodes.filter((n) => n.depth === 2);
  const children = stocks.map((n) => {
    const net = parseFloat(n.net_yuan);
    const gross = n.gross_yuan != null ? Math.abs(parseFloat(n.gross_yuan)) : Math.abs(net);
    return { name: n.name, value: gross || 1, _net: n.net_yuan, _dir: n.direction,
      itemStyle: { color: _dirColor(n.direction, c) } };
  });
  const sec = data.nodes.find((n) => n.depth === 1);
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      formatter: (pm) => {
        const s = parseFloat(pm.data._net || 0);
        return `${pm.name}<br/>净额 <b>${s >= 0 ? "+" : ""}${_yi(pm.data._net || "0")} 亿</b>` +
          `<br/>成交额 ${_yi(String(pm.value))} 亿`;
      },
    },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false },
      top: 8, left: 8, right: 8, bottom: 8, width: undefined,
      label: { show: true, formatter: "{b}", color: "#fff", fontSize: 11 },
      itemStyle: { borderColor: cssVar("--panel", "#fff"), borderWidth: 1, gapWidth: 1 },
      data: children,
      name: sec ? sec.name : "行业",
    }],
  });
  window.addEventListener("resize", () => chart.resize());
  return chart;
}
