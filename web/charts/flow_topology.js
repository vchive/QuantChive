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
  const chart = initChart(dom);
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
      nodeGap: 10, nodeWidth: 22, layoutIterations: 32,
      emphasis: { focus: "adjacency" },
      lineStyle: { curveness: 0.5 },
      data: nodes, links: links,
    }],
  });
  // 点击行业节点 → 下钻。ECharts sankey 节点图元 dataIndex 有时为 null，
  // chart.on('click') 拿不到 data → 双保险：① 常规 click ② zrender 级手动命中矩形。
  const byId = {};
  data.nodes.forEach((n) => { byId[n.id] = n; });
  const fire = (sid, name) => { if (sid != null && onSectorClick) onSectorClick(sid, name); };
  chart.on("click", (pm) => {
    const d = pm.data || {};
    if (d._sid != null) return fire(d._sid, d._label);
    const orig = byId[d.name || pm.name];
    if (orig) fire(orig.subject_id, orig.name);
  });
  // zrender 兜底：ECharts sankey 节点不触发 chart.on('click')（图元 dataIndex 为 null）。
  // 用图元自带 el.contain(x,y) 精确命中（曲线真实形状，非矩形近似）。
  // 命中节点矩形 → 该行业；命中流向色带 → 其目标行业。都可下钻，点击区域大。
  const sectorLinks = (data.links || []).filter((l) => l.target && l.target.indexOf("sector:") === 0);
  chart.getZr().on("click", (e) => {
    const nodes = chart.getOption().series[0].data;
    const list = chart.getZr().storage.getDisplayList();
    const rects = list.filter((el) => el.type === "rect");
    const paths = list.filter((el) => el.type === "path");
    const x = e.offsetX, y = e.offsetY;
    // ① 命中节点矩形（rect[i] ↔ nodes[i]）
    for (let i = 0; i < rects.length; i++) {
      if (rects[i].contain(x, y)) {
        const n = nodes[i];
        if (n && n._sid != null) fire(n._sid, n._label);
        return;
      }
    }
    // ② 命中流向色带 path（path[j] ↔ sectorLinks[j]）→ 目标行业
    for (let j = 0; j < paths.length && j < sectorLinks.length; j++) {
      if (paths[j].contain(x, y)) {
        return fireByTarget(sectorLinks[j].target);
      }
    }
    // ③ 兜底：色带是细曲线，点附近常错过。取纵向中心最接近点击 Y 的行业节点就近下钻。
    let best = null, bestDist = Infinity;
    for (let i = 0; i < rects.length; i++) {
      const n = nodes[i];
      if (!n || n._sid == null) continue;   // 跳过大盘
      const br = rects[i].getBoundingRect().clone();
      br.applyTransform(rects[i].transform);
      const cy = br.y + br.height / 2;
      const d = Math.abs(y - cy);
      if (d < bestDist) { bestDist = d; best = n; }
    }
    if (best) fire(best._sid, best._label);
  });
  function fireByTarget(target) {
    const sid = parseInt(target.split(":")[1], 10);
    const node = chart.getOption().series[0].data.find((n) => n._sid === sid);
    if (node) fire(node._sid, node._label);
  }
  return chart;
}

/* Treemap：行业内个股。面积=gross、色=净额方向。点个股 → onStockClick 跳博弈图 */
function renderFlowTreemap(dom, data, onStockClick) {
  const chart = initChart(dom);
  const c = _topoColors();
  const stocks = data.nodes.filter((n) => n.depth === 2);
  const children = stocks.map((n) => {
    const net = parseFloat(n.net_yuan);
    const gross = n.gross_yuan != null ? Math.abs(parseFloat(n.gross_yuan)) : Math.abs(net);
    return { name: n.name, value: gross || 1, _net: n.net_yuan, _dir: n.direction, _sid: n.subject_id,
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
  chart.on("click", (pm) => {
    if (pm.data && pm.data._sid != null && onStockClick) onStockClick(pm.data._sid, pm.name);
  });
  return chart;
}


/* 旭日图（Sunburst）：大盘→行业→个股 圈层，点击逐层钻取。data=tree 响应 */
function renderFlowSunburst(dom, tree, onStockClick) {
  const chart = initChart(dom);
  const c = _topoColors();
  const conv = (n) => ({
    name: n.name, value: Math.abs(parseFloat(n.gross_yuan || n.net_yuan)) || 1,
    _net: n.net_yuan, _dir: n.direction, _sid: n.subject_id, _depth: n.depth,
    itemStyle: { color: _dirColor(n.direction, c) },
    children: (n.children || []).map(conv),
  });
  const root = conv(tree.root);
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      formatter: (pm) => {
        const s = parseFloat(pm.data._net || 0);
        return `${pm.name}<br/>净额 <b>${s >= 0 ? "+" : ""}${_yi(pm.data._net || "0")} 亿</b>`;
      },
    },
    series: [{
      type: "sunburst", data: root.children, radius: [0, "95%"],
      center: ["50%", "50%"], sort: undefined,
      emphasis: { focus: "ancestor" },
      levels: [{}, { r0: "15%", r: "48%", label: { rotate: "tangential", fontSize: 11 } },
        { r0: "48%", r: "80%", label: { align: "right", fontSize: 10 } }],
      itemStyle: { borderColor: cssVar("--panel", "#fff"), borderWidth: 1 },
      label: { color: "#fff" },
    }],
  });
  chart.on("click", (pm) => {
    if (pm.data && pm.data._depth === 2 && pm.data._sid != null && onStockClick) {
      onStockClick(pm.data._sid, pm.name);
    }
  });
  return chart;
}

/* 行业强弱排行（双向条）：各行业净额横向，红涨绿跌一屏排座次。data=topology 响应 */
function renderSectorRanking(dom, data, onSectorClick) {
  const chart = initChart(dom);
  const c = _topoColors();
  const secs = data.nodes.filter((n) => n.depth === 1 && n.subject_id != null)
    .map((n) => ({ name: n.name, sid: n.subject_id, net: parseFloat(n.net_yuan) / 1e8, dir: n.direction }))
    .sort((a, b) => a.net - b.net);   // 升序，绿(流出)在下、红(流入)在上
  chart.setOption({
    backgroundColor: "transparent",
    grid: { left: 90, right: 60, top: 12, bottom: 20 },
    tooltip: {
      backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      formatter: (pm) => `${pm.name}<br/>主力净额 <b>${pm.value >= 0 ? "+" : ""}${pm.value.toFixed(2)} 亿</b>`,
    },
    xAxis: { type: "value", axisLabel: { color: c.LABEL, fontSize: 10, formatter: "{value}亿" },
      splitLine: { lineStyle: { color: cssVar("--chart-grid", "rgba(0,0,0,0.06)") } },
      axisLine: { show: false } },
    yAxis: { type: "category", data: secs.map((s) => s.name),
      axisLabel: { color: c.TIP_TX, fontSize: 11 }, axisLine: { lineStyle: { color: cssVar("--chart-axis", "#E3E7ED") } },
      axisTick: { show: false } },
    series: [{
      type: "bar", data: secs.map((s) => ({ value: s.net, _sid: s.sid,
        itemStyle: { color: _dirColor(s.dir, c), borderRadius: 3 },
        label: { show: true, position: s.net >= 0 ? "right" : "left",
          formatter: (p) => `${p.value >= 0 ? "+" : ""}${p.value.toFixed(1)}`,
          color: c.LABEL, fontSize: 10 } })),
      barMaxWidth: 16,
    }],
  });
  chart.on("click", (pm) => {
    if (pm.data && pm.data._sid != null && onSectorClick) onSectorClick(pm.data._sid, pm.name);
  });
  return chart;
}

/* 全市场矩形树（Treemap 全景）：所有行业一张图，面积=成交额、色=净额方向。data=tree 响应 */
function renderMarketTreemap(dom, tree, onSectorClick) {
  const chart = initChart(dom);
  const c = _topoColors();
  const data = (tree.root.children || []).map((n) => ({
    name: n.name, value: Math.abs(parseFloat(n.gross_yuan || n.net_yuan)) || 1,
    _net: n.net_yuan, _sid: n.subject_id, itemStyle: { color: _dirColor(n.direction, c) },
  }));
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      formatter: (pm) => {
        const s = parseFloat(pm.data._net || 0);
        return `${pm.name}<br/>净额 <b>${s >= 0 ? "+" : ""}${_yi(pm.data._net || "0")} 亿</b>`;
      },
    },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false },
      top: 8, left: 8, right: 8, bottom: 8,
      label: { show: true, formatter: "{b}", color: "#fff", fontSize: 11 },
      itemStyle: { borderColor: cssVar("--panel", "#fff"), borderWidth: 2, gapWidth: 2 },
      data: data,
    }],
  });
  chart.on("click", (pm) => {
    if (pm.data && pm.data._sid != null && onSectorClick) onSectorClick(pm.data._sid, pm.name);
  });
  return chart;
}


/* 多天趋势折线（spec006）：各行业近N天净额趋势对比。data=trends 响应。
   点图例=只看该行业；红涨绿跌语义靠正负值本身。随主题跟随。 */
function renderSectorTrends(dom, data) {
  const chart = initChart(dom);
  const c = _topoColors();
  const LABEL = c.LABEL, GRID = cssVar("--chart-grid", "rgba(0,0,0,0.06)");
  // 每行业一条折线（值=亿）；多色区分（主题强调色系轮转）
  const palette = ["#E5484D", "#2E6BE6", "#12A150", "#C9781E", "#8B5CF6",
                   "#EC4899", "#0EA5E9", "#F59E0B", "#14B8A6", "#EF4444"];
  const series = data.series.map((s, i) => ({
    name: s.sector, type: "line", smooth: true, showSymbol: false,
    lineStyle: { width: 2, color: palette[i % palette.length] },
    emphasis: { focus: "series" },
    data: s.values.map((v) => parseFloat(v) / 1e8),
  }));
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis", backgroundColor: c.TIP_BG, borderColor: c.TIP_BD, borderWidth: 1,
      textStyle: { color: c.TIP_TX, fontSize: 12 }, extraCssText: "border-radius:8px;",
      valueFormatter: (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "亿",
    },
    legend: { type: "scroll", top: 0, textStyle: { color: LABEL, fontSize: 11 },
      inactiveColor: cssVar("--text-disabled", "#C2C8D2"), data: data.series.map((s) => s.sector) },
    grid: { left: 56, right: 24, top: 40, bottom: 40 },
    xAxis: { type: "category", data: data.dates.map((d) => d.slice(5)), boundaryGap: false,
      axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { lineStyle: { color: cssVar("--chart-axis", "#E3E7ED") } },
      axisTick: { show: false } },
    yAxis: { type: "value", name: "净额(亿)", nameTextStyle: { color: LABEL, fontSize: 10 },
      axisLabel: { color: LABEL, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: GRID } } },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 4, height: 14,
      fillerColor: cssVar("--accent-weak", "rgba(46,107,230,0.1)"), handleStyle: { color: cssVar("--accent", "#2E6BE6") } }],
    series: series,
  });
  return chart;
}
