// QuantChive 市场观测 · 栈式下钻 SPA（contracts/service-api.md）。
// 铁律：金额是后端已定序/已定标的 Decimal 字符串；前端仅格式化展示，绝不做数值排序/加减。
// 能力驱动：读 has_five_tier/mode/supported_metrics 决定渲染，不支持的指标灰字告知。

const $ = (id) => document.getElementById(id);
const el = (t, cls, html) => { const e = document.createElement(t); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
let CURRENT_REDRAW = null;   // 主题切换时重绘当前图表的回调

// ---- 交互日志（确认点击行为）----
const CLICK_LOG = [];
function logClick(action, detail) {
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  CLICK_LOG.unshift(`${ts} · ${action}${detail ? " · " + detail : ""}`);
  if (CLICK_LOG.length > 50) CLICK_LOG.pop();
  const panel = $("clicklog");
  if (panel) panel.innerHTML = CLICK_LOG.map((l) => `<div class="cl-row">${l}</div>`).join("");
  if (window.console) console.log("[click]", action, detail || "");
}


let ASSET = "a_share";          // 当前品种 Tab
const stack = [];               // 下钻栈：[{title, render}]

// ---- 展示格式化（display-only Number；不参与排序，排序由后端定）----
function fmtYi(s) {
  if (s == null) return "—";
  const n = Number(s); if (!isFinite(n)) return "—";
  const yi = n / 1e8; return `${yi >= 0 ? "+" : ""}${yi.toFixed(2)} 亿`;
}
function fmtNum(s, suffix = "") {
  if (s == null) return "—";
  const n = Number(s); if (!isFinite(n)) return "—";
  return n.toLocaleString("zh-CN") + suffix;
}
function fmtPct(s) { if (s == null) return "—"; const n = Number(s); return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`; }
function signCls(s) { const n = Number(s); return n >= 0 ? "pos" : "neg"; }

async function api(path) {
  const resp = await fetch(path);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw { code: data.error?.code || resp.status, message: data.error?.message || "请求失败" };
  return data;
}

function setProvenance(p, extra = "") {
  if (!p) { $("prov").textContent = extra; return; }
  $("prov").innerHTML =
    `交易日 <b>${p.trade_date}</b> · 时点 ${p.captured_at} · 来源 ${p.source_id}`
    + (p.is_stale ? ' · <span class="stale">非实时(最近交易日)</span>' : "") + (extra ? " · " + extra : "");
}

function renderCrumbs() {
  $("crumbs").innerHTML = "";
  stack.forEach((frame, i) => {
    const b = el("button", "crumb", frame.title);
    b.onclick = () => { stack.length = i + 1; renderCrumbs(); frame.render(); };
    $("crumbs").appendChild(b);
    if (i < stack.length - 1) $("crumbs").appendChild(el("span", "sep", " › "));
  });
}

function push(title, render) { stack.push({ title, render }); renderCrumbs(); render(); }

// ---- 观测项表格（能力驱动列）----
function itemsTable(items, { hasFiveTier, onRow }) {
  const t = el("table", "grid");
  const head = hasFiveTier
    ? ["主体", "主力净额", "超大单", "大单", "中单", "小单"]
    : ["主体", "现价", "涨跌幅", "成交量"];
  t.appendChild(el("thead", null, "<tr>" + head.map((h) => `<th>${h}</th>`).join("") + "</tr>"));
  const tb = el("tbody");
  items.forEach((it) => {
    const tr = el("tr");
    const cells = hasFiveTier
      ? [[it.display_name, "name"], [fmtYi(it.main_net), signCls(it.main_net)],
         [fmtYi(it.super_large_net), signCls(it.super_large_net)], [fmtYi(it.large_net), signCls(it.large_net)],
         [fmtYi(it.medium_net), signCls(it.medium_net)], [fmtYi(it.small_net), signCls(it.small_net)]]
      : [[it.display_name, "name"], [fmtNum(it.price), ""], [fmtPct(it.change_pct), signCls(it.change_pct)],
         [fmtNum(it.volume, " 手"), ""]];
    cells.forEach(([v, c], i) => {
      const td = el("td", c, v);
      if (i === 0 && onRow) { td.classList.add("link"); td.onclick = () => onRow(it); }
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

function dualBoard(data, onRow) {
  const wrap = el("div", "boards");
  const mk = (title, items) => {
    const s = el("section");
    s.appendChild(el("h2", null, title));
    s.appendChild(itemsTable(items, { hasFiveTier: data.has_five_tier, onRow }));
    return s;
  };
  if (data.mode === "bipolar") {
    wrap.appendChild(mk("资金净流入", data.top_inflow));
    wrap.appendChild(mk("资金净流出", data.top_outflow));
  } else {
    wrap.appendChild(mk("排行", data.ranked_items));
  }
  return wrap;
}

// ---- 断点不连线的 sparkline（value=null 处断开 path）----
function sparkline(points) {
  const w = 640, h = 120, pad = 8;
  const vals = points.map((p) => (p.value == null ? null : Number(p.value)));
  const nums = vals.filter((v) => v != null);
  if (!nums.length) return el("div", "empty", "无有效数据点");
  const min = Math.min(...nums), max = Math.max(...nums), span = max - min || 1;
  const x = (i) => pad + (i / Math.max(1, points.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
  let d = "", pen = false;
  vals.forEach((v, i) => {
    if (v == null) { pen = false; return; }             // 断点：抬笔，不连线
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
    pen = true;
  });
  const svg = `<svg viewBox="0 0 ${w} ${h}" class="spark"><path d="${d}" fill="none"/></svg>`;
  const box = el("div", "sparkbox", svg);
  const gaps = vals.filter((v) => v == null).length;
  box.appendChild(el("div", "sparkmeta", `${nums.length} 点 · ${gaps} 断点（不连线）`));
  return box;
}

// ---- 视图：大盘 overview 卡片（仅 A股）----
async function viewMarket() {
  const v = $("view"); v.innerHTML = "";
  try {
    const m = await api(`/api/market/overview?asset_class=a_share`);
    const card = el("div", "card");
    card.appendChild(el("div", "card-title", "A股大盘 · 主力净额（个股求和）"));
    card.appendChild(el("div", "big " + signCls(m.main_net), fmtYi(m.main_net)));
    card.appendChild(el("div", "sub",
      `成分 ${m.constituent_count}/${m.expected_count} · 覆盖率 ${m.coverage_pct}%`));
    v.appendChild(card);
    setProvenance(m.provenance);
  } catch (e) {
    v.appendChild(el("div", "hint", e.message || `大盘暂无数据（${e.code}）`));
  }
  // 板块入口
  ["industry", "concept"].forEach((st) => {
    const b = el("button", "enter", st === "industry" ? "行业板块 →" : "概念板块 →");
    b.onclick = () => push(st === "industry" ? "行业板块" : "概念板块", () => viewSectors(st));
    $("view").appendChild(b);
  });
}

// ---- 视图：板块双榜 ----
async function viewSectors(sectorType) {
  const v = $("view"); v.innerHTML = "";
  try {
    const data = await api(`/api/sectors/ranking?caliber=eastmoney&sector_type=${sectorType}&top_n=10`);
    v.appendChild(dualBoard(data, (it) =>
      push(it.display_name, () => viewSectorStocks(it.subject_id, it.display_name))));
    setProvenance(data.provenance, `共 ${data.total_subjects} 个板块`);
  } catch (e) {
    v.appendChild(el("div", "hint", `${e.code}: ${e.message}`));
  }
}

// ---- 视图：板块内个股 ----
async function viewSectorStocks(sectorId, name) {
  const v = $("view"); v.innerHTML = "";
  try {
    const data = await api(`/api/sectors/${sectorId}/stocks?sort_by=main_net&top_n=20`);
    v.appendChild(dualBoard(data, (it) =>
      push(it.display_name, () => viewSeries(it.subject_id, it.display_name))));
    setProvenance(data.provenance, `${name} 成分股`);
  } catch (e) {
    v.appendChild(el("div", "hint",
      e.code === "OUT_OF_WINDOW" ? "该历史日无成分记录（无前视，不回退当前成分）" : `${e.code}: ${e.message}`));
  }
}

// ---- 视图：ETF 单榜 ----
async function viewEtf() {
  const v = $("view"); v.innerHTML = "";
  try {
    const data = await api(`/api/etf/ranking?sort_by=change_pct&top_n=20`);
    v.appendChild(el("div", "hint", "ETF 无资金流五档，按涨跌幅排行（能力自描述）"));
    v.appendChild(dualBoard(data, (it) =>
      push(it.display_name, () => viewSeries(it.subject_id, it.display_name, "change_pct", "intraday"))));
    setProvenance(data.provenance, `共 ${data.total_subjects} 只 ETF`);
  } catch (e) {
    v.appendChild(el("div", "hint", `${e.code}: ${e.message}`));
  }
}

// ---- 视图：单主体时序（日线历史 / 当日分钟可切换）----
async function viewSeries(subjectId, name, metric = "main_net", gran = "daily", chartType = "main") {
  const v = $("view"); v.innerHTML = "";
  // 骨架屏（fetch 前）
  const skel = el("div", "skeleton"); skel.style.height = "520px"; skel.style.margin = "12px 0";
  v.appendChild(skel);
  // 图型切换（主图 / 热力 / 回放 / 对抗）
  const chartToggle = el("div", "gran-toggle");
  [["main", "博弈主图"], ["heatmap", "四档热力"], ["replay", "逐分钟回放"], ["battle", "对抗象限"]].forEach(([ct, label]) => {
    const b = el("button", "gran" + (ct === chartType ? " active" : ""), label);
    b.onclick = () => viewSeries(subjectId, name, metric, ct === "replay" ? "intraday" : gran, ct);
    chartToggle.appendChild(b);
  });
  // 粒度切换
  const toggle = el("div", "gran-toggle");
  [["daily", "日线历史"], ["intraday", "当日分钟"]].forEach(([g, label]) => {
    const b = el("button", "gran" + (g === gran ? " active" : ""), label);
    b.onclick = () => viewSeries(subjectId, name, metric, g, chartType);
    toggle.appendChild(b);
  });
  try {
    const data = await api(`/api/subjects/${subjectId}/tiers_series?granularity=${gran}`);
    v.innerHTML = "";
    // —— 价格头部（长桥/富途风格）——
    const last = data.points[data.points.length - 1] || {};
    const header = el("div", "subject-header");
    const top = el("div", "sh-top");
    top.appendChild(el("span", "sh-name", name));
    if (last.price != null) top.appendChild(el("span", "sh-price num", last.price));
    if (last.change_pct != null) {
      const cls = signCls(last.change_pct);
      top.appendChild(el("span", "sh-pill " + cls, fmtPct(last.change_pct)));
    }
    header.appendChild(top);
    // 键值统计条
    const stats = el("div", "sh-stats");
    const stat = (k, val, cls) => {
      const s = el("div", "sh-stat");
      s.appendChild(el("div", "k", k));
      s.appendChild(el("div", "v" + (cls ? " " + cls : ""), val));
      stats.appendChild(s);
    };
    if (last.main_net != null) stat("主力净额", fmtYi(last.main_net), signCls(last.main_net));
    if (last.super_large) stat("超大单净", fmtYi(last.super_large.net), signCls(last.super_large.net));
    if (last.super_large && last.super_large.inflow != null) {
      stat("超大流入", fmtYi(last.super_large.inflow), "pos");
      stat("超大流出", fmtYi(last.super_large.outflow), "neg");
    }
    stat("截至", last.ts || "—", "");
    header.appendChild(stats);
    v.appendChild(header);
    // 图型 + 粒度切换（一行两组）
    const toggleRow = el("div", "toggle-row");
    toggleRow.appendChild(chartToggle);
    toggleRow.appendChild(toggle);
    v.appendChild(toggleRow);
    // —— 图表 ——
    const chartBox = el("div", "flow-chart");
    chartBox.style.height = "520px";
    v.appendChild(chartBox);
    const draw = () => {
      chartBox.innerHTML = "";
      if (chartType === "heatmap") renderHeatmapRibbon(chartBox, data);
      else if (chartType === "replay") renderReplay(chartBox, data);
      else if (chartType === "battle") renderBattleQuadrant(chartBox, data);
      else renderMainChart(chartBox, data);
    };
    draw();
    CURRENT_REDRAW = draw;   // 主题切换重绘当前图型
    const divTxt = (data.divergence || []).map((d) => d.kind === "accumulation" ? "吸筹" : "派发").join("、");
    v.appendChild(el("div", "sub",
      `${data.points.length} 点`
      + (data.has_gross ? " · 含流入流出" : " · 仅净额（盘中无流入流出）")
      + (divTxt ? ` · 背离：${divTxt}` : "")));
    setProvenance(data.provenance);
  } catch (e) {
    v.innerHTML = "";
    v.appendChild(el("h2", null, `${name} · 资金档位博弈`));
    v.appendChild(toggle);
    const msg = e.code === "OUT_OF_WINDOW" ? "超出保留窗（数据已清理）"
      : e.code === "NO_DATA_FOR_DATE" ? (e.message || "无数据") : `${e.code}: ${e.message}`;
    v.appendChild(el("div", "hint", msg));
  }
}

// ---- 天内时点回放框架（播放选中日当日的资金变化：近期分钟级、久远小时级）----
// 现盘中分钟数据未攒够（下周一盘中调度器跑满一天后到位）。届时 slots 就是当日时点，
// seek 按 slot 取当日某时刻拓扑；框架/UI/播放逻辑不变，只接数据。
const TOPO_PLAY = { timer: null, playing: false };
function stopTopoPlay() {
  if (TOPO_PLAY.timer) { clearInterval(TOPO_PLAY.timer); TOPO_PLAY.timer = null; }
  TOPO_PLAY.playing = false;
  if (typeof stopDayCross === "function") stopDayCross();   // 同时停跨天回放
}
async function fetchIntradayPoints(tradeDate) {
  if (!tradeDate) return { slots: [], granularity: "eod" };
  try { return await api(`/api/flow/topology/intraday_points?trade_date=${tradeDate}`); }
  catch (_) { return { slots: [], granularity: "eod" }; }
}
function buildDayPlaybackBar(dayPoints, tier, mode, tradeDate) {
  const slots = dayPoints.slots || [];
  const bar = el("div", "playbar");
  if (slots.length < 2) {
    // 当日无逐时点数据——占位提示（说清物理原因，不误导为"没做"）
    bar.classList.add("playbar-disabled");
    bar.appendChild(el("span", "play-hint",
      `⏱ 天内时点回放：${tradeDate || "该日"} 无分钟/小时级数据。历史日线源仅提供每日收盘值（EOD），`
      + `天内分钟数据只能由本平台盘中实时逐分钟采集积累——历史日无法补采（免费源不提供历史分钟资金流）。`
      + `盘中调度器运行满一天后，当天即可播分钟级资金流动。`));
    return bar;
  }
  const btn = el("button", "play-btn", TOPO_PLAY.playing ? "⏸" : "▶");
  const slider = el("input", "play-slider");
  slider.type = "range"; slider.min = 0; slider.max = slots.length - 1; slider.value = 0;
  const label = el("span", "play-label", slots[0]);
  const seek = (i) => { label.textContent = slots[i]; logClick("天内时点", slots[i]);
    viewTopologyAtSlot(tier, mode, tradeDate, slots[i]); };
  slider.oninput = () => { label.textContent = slots[+slider.value]; };
  slider.onchange = () => { stopTopoPlay(); seek(+slider.value); };
  btn.onclick = () => {
    if (TOPO_PLAY.playing) { stopTopoPlay(); btn.textContent = "▶"; return; }
    TOPO_PLAY.playing = true; btn.textContent = "⏸"; logClick("播放当日回放", "");
    let i = +slider.value; if (i >= slots.length - 1) i = -1;
    TOPO_PLAY.timer = setInterval(() => {
      i += 1; if (i >= slots.length) { stopTopoPlay(); return; }
      slider.value = i; seek(i);
    }, 3000);
  };
  bar.appendChild(btn); bar.appendChild(slider); bar.appendChild(label);
  return bar;
}
// 播放某日某时点的拓扑：按 minute_slot 取盘中该时刻拓扑，只重绘图表区（保留播放条不打断）
async function viewTopologyAtSlot(tier, mode, tradeDate, slot) {
  const box = document.querySelector(".flow-chart");
  if (!box) return;
  // 天内只支持桑基/排行/矩形（旭日用 tree 端点，暂按桑基降级）；盘中无 gross
  try {
    const data = await api(`/api/flow/topology?tier=${tier}&top_sectors=20&trade_date=${tradeDate}&minute_slot=${slot}`);
    box.innerHTML = "";
    const onSector = (sid, sn) => { stopTopoPlay(); logClick("下钻行业", sn); push(sn + " 成分", () => viewSectorTreemap(sid, sn, tier, tradeDate)); };
    if (mode === "ranking") renderSectorRanking(box, data, onSector);
    else renderFlowSankey(box, data, onSector);   // 其余模式盘中降级为桑基
    const sub = document.querySelector(".sub");
    if (sub) sub.textContent = `${tradeDate} ${slot} · 盘中快照(净额,无流入流出) · 覆盖 ${data.coverage_pct}%`;
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "hint", `${e.code || ""}: ${e.message || "该时点无数据"}`));
  }
}

// ---- 跨天回放（一帧一天，看资金流向多日演变；数据现成，可用）----
const TOPO_DAYPLAY = { timer: null, playing: false };
function stopDayCross() {
  if (TOPO_DAYPLAY.timer) { clearInterval(TOPO_DAYPLAY.timer); TOPO_DAYPLAY.timer = null; }
  TOPO_DAYPLAY.playing = false;
}
// dates 升序；cur 当前日；tier/mode 供重绘
function buildCrossDayBar(dates, cur, tier, mode) {
  const bar = el("div", "playbar");
  bar.appendChild(el("span", "play-cap", "跨天"));
  const btn = el("button", "play-btn", TOPO_DAYPLAY.playing ? "⏸" : "▶");
  const slider = el("input", "play-slider");
  slider.type = "range"; slider.min = 0; slider.max = dates.length - 1;
  slider.value = Math.max(0, dates.indexOf(cur));
  const label = el("span", "play-label", cur);
  const seek = (i) => { label.textContent = dates[i]; logClick("跨天回放", dates[i]); viewTopology(tier, mode, dates[i]); };
  slider.oninput = () => { label.textContent = dates[+slider.value]; };
  slider.onchange = () => { stopDayCross(); seek(+slider.value); };
  btn.onclick = () => {
    if (TOPO_DAYPLAY.playing) { stopDayCross(); btn.textContent = "▶"; return; }
    TOPO_DAYPLAY.playing = true; btn.textContent = "⏸"; logClick("播放跨天", "");
    let i = +slider.value; if (i >= dates.length - 1) i = -1;
    TOPO_DAYPLAY.timer = setInterval(() => {
      i += 1; if (i >= dates.length) { stopDayCross(); return; }
      seek(i);   // viewTopology 重建含本条，playing 保留继续
    }, 3000);
  };
  bar.appendChild(btn); bar.appendChild(slider); bar.appendChild(label);
  return bar;
}

// ---- 日历选择器（原生，只高亮有数据的交易日）----
// availableSet：可选日期集合(YYYY-MM-DD)；cur：当前选中；onPick(date)
function buildCalendar(availableSet, cur, onPick) {
  const wrap = el("div", "cal-wrap");
  const trigger = el("button", "cal-trigger", `📅 ${cur || "选择日期"}`);
  const pop = el("div", "cal-pop");
  // 以当前选中日所在月为起点
  const curD = cur ? new Date(cur + "T00:00:00") : new Date(2026, 6, 3);
  let year = curD.getFullYear(), month = curD.getMonth();   // month 0-11
  const pad = (n) => String(n).padStart(2, "0");
  function render() {
    pop.innerHTML = "";
    const head = el("div", "cal-head");
    const prev = el("button", "cal-nav", "‹"), next = el("button", "cal-nav", "›");
    prev.onclick = (e) => { e.stopPropagation(); month--; if (month < 0) { month = 11; year--; } render(); };
    next.onclick = (e) => { e.stopPropagation(); month++; if (month > 11) { month = 0; year++; } render(); };
    head.appendChild(prev);
    head.appendChild(el("span", "cal-title", `${year} 年 ${month + 1} 月`));
    head.appendChild(next);
    pop.appendChild(head);
    const grid = el("div", "cal-grid");
    ["一", "二", "三", "四", "五", "六", "日"].forEach((w) => grid.appendChild(el("div", "cal-wk", w)));
    const first = new Date(year, month, 1);
    let startDow = (first.getDay() + 6) % 7;   // 周一=0
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    for (let i = 0; i < startDow; i++) grid.appendChild(el("div", "cal-day empty"));
    for (let d = 1; d <= daysInMonth; d++) {
      const iso = `${year}-${pad(month + 1)}-${pad(d)}`;
      const has = availableSet.has(iso);
      const cell = el("div", "cal-day" + (has ? " avail" : " disabled") + (iso === cur ? " sel" : ""), String(d));
      if (has) cell.onclick = (e) => { e.stopPropagation(); pop.classList.remove("open"); onPick(iso); };
      grid.appendChild(cell);
    }
    pop.appendChild(grid);
  }
  trigger.onclick = (e) => { e.stopPropagation(); render(); pop.classList.toggle("open"); };
  document.addEventListener("click", () => pop.classList.remove("open"));
  wrap.appendChild(trigger); wrap.appendChild(pop);
  return wrap;
}

// ---- 资金流向拓扑（spec006）：4 种视图 + 历史选日 + 时间轴回放 + 下钻 ----
let TOPO_DATES = null;   // 缓存可选日期
async function viewTopology(tier = "main", mode = "sankey", tradeDate = "") {
  const v = $("view"); v.innerHTML = "";
  const title = "资金流向 · 大盘 → 行业 → 个股";
  v.appendChild(el("h2", null, title));
  // 视图方式切换
  const modeToggle = el("div", "gran-toggle");
  [["sankey", "桑基水流"], ["sunburst", "旭日钻取"], ["ranking", "强弱排行"], ["treemap", "全景矩形"], ["trends", "多天趋势"]].forEach(([m, label]) => {
    const b = el("button", "gran" + (m === mode ? " active" : ""), label);
    b.onclick = () => { logClick("切视图", label); viewTopology(tier, m, tradeDate); };
    modeToggle.appendChild(b);
  });
  // 档位切换
  const tierToggle = el("div", "gran-toggle");
  [["main", "主力"], ["super_large", "超大单"], ["large", "大单"], ["medium", "中单"], ["small", "小单"]].forEach(([t, label]) => {
    const b = el("button", "gran" + (t === tier ? " active" : ""), label);
    b.onclick = () => { logClick("切档位", label); viewTopology(t, mode, tradeDate); };
    tierToggle.appendChild(b);
  });
  // 历史日期选择器（日历，只可选有数据的交易日）
  if (!TOPO_DATES) {
    try { TOPO_DATES = (await api("/api/flow/topology/dates")).dates; } catch (_) { TOPO_DATES = []; }
  }
  const availSet = new Set(TOPO_DATES || []);
  const curDate2 = tradeDate || (TOPO_DATES && TOPO_DATES[0]) || "";
  const cal = buildCalendar(availSet, curDate2,
    (d) => { logClick("选日期", d); viewTopology(tier, mode, d); });
  const row = el("div", "toggle-row"); row.appendChild(modeToggle); row.appendChild(tierToggle);
  // 多天趋势不需要日期选择/回放/下钻（它本身就是跨多天）；其余模式才挂日历
  if (mode !== "trends") row.appendChild(cal);
  v.appendChild(row);
  const skel = el("div", "skeleton"); skel.style.height = "580px"; v.appendChild(skel);

  // —— 多天趋势折线（独立分支：跨多天、不选单日、不下钻）——
  if (mode === "trends") {
    try {
      const data = await api(`/api/flow/topology/trends?tier=${tier}&days=20&top_sectors=10`);
      v.innerHTML = ""; v.appendChild(el("h2", null, title)); v.appendChild(row);
      const box = el("div", "flow-chart"); box.style.height = "580px"; v.appendChild(box);
      const draw = () => { box.innerHTML = ""; renderSectorTrends(box, data); };
      draw(); CURRENT_REDRAW = draw;
      v.appendChild(el("div", "sub",
        `近 ${data.dates.length} 交易日 · Top10 行业主力净额趋势 · 点图例只看单行业`));
      setProvenance(data.provenance);
    } catch (e) {
      v.innerHTML = ""; v.appendChild(el("h2", null, title)); v.appendChild(row);
      v.appendChild(el("div", "hint", `${e.code}: ${e.message}`));
    }
    return;
  }

  const dq = tradeDate ? `&trade_date=${tradeDate}` : "";
  // 桑基/排行用 topology(扁平)，旭日/矩形树用 tree(层级)
  const useTree = (mode === "sunburst" || mode === "treemap");
  const url = useTree
    ? `/api/flow/topology/tree?tier=${tier}&top_sectors=30${dq}`
    : `/api/flow/topology?tier=${tier}&top_sectors=20${dq}`;
  const onSector = (sid, sn) => { stopTopoPlay(); logClick("下钻行业", sn); push(sn + " 成分", () => viewSectorTreemap(sid, sn, tier, tradeDate)); };
  const onStock = (sid, sn) => { stopTopoPlay(); logClick("下钻个股", sn); push(sn, () => viewSeries(sid, sn, "main_net", "daily", "main")); };
  try {
    const data = await api(url);
    v.innerHTML = ""; v.appendChild(el("h2", null, title)); v.appendChild(row);
    const box = el("div", "flow-chart"); box.style.height = "580px"; v.appendChild(box);
    const drawFns = {
      sankey: () => renderFlowSankey(box, data, onSector),
      sunburst: () => renderFlowSunburst(box, data, onStock),
      ranking: () => renderSectorRanking(box, data, onSector),
      treemap: () => renderMarketTreemap(box, data, onSector),
    };
    const draw = () => { box.innerHTML = ""; drawFns[mode](); };
    draw();
    CURRENT_REDRAW = draw;
    // 跨天回放（一帧一天，看资金流向多日演变——数据现成，能用）
    const daysAsc = (TOPO_DATES || []).slice().reverse();
    if (daysAsc.length > 1) {
      v.appendChild(buildCrossDayBar(daysAsc, tradeDate || data.trade_date, tier, mode));
    }
    // 天内时点回放条：播放选中日"当日"从早到晚的资金变化（近期分钟级、久远小时级）。
    // 现在盘中分钟数据尚未攒够（下周一盘中调度器跑满一天后到位），此处按当日可用时点构建；
    // 只有 EOD 一个点时显示占位提示，不空跑。
    const dayPoints = await fetchIntradayPoints(tradeDate || data.trade_date);
    v.appendChild(buildDayPlaybackBar(dayPoints, tier, mode, tradeDate || data.trade_date));
    const hint = {
      sankey: "点击行业看成分股 · 线宽=资金量 · 红净流入/绿净流出",
      sunburst: "内圈行业外圈个股 · 点个股看博弈 · 红净流入/绿净流出",
      ranking: "各行业主力净额排座次 · 点行业下钻",
      treemap: "全行业一屏 · 面积=成交额 · 点行业下钻 · 红净流入/绿净流出",
    }[mode];
    v.appendChild(el("div", "sub", `${data.trade_date} · 覆盖 ${data.coverage_pct}% · ${hint}`));
    setProvenance(data.provenance);
  } catch (e) {
    v.innerHTML = ""; v.appendChild(el("h2", null, title));
    v.appendChild(el("div", "hint", e.code === "NO_DATA_FOR_DATE" ? (e.message || "无数据") : `${e.code}: ${e.message}`));
  }
}

async function viewSectorTreemap(sectorId, sectorName, tier, tradeDate = "") {
  // 注意：本函数只渲染，不自调 push（push 会回调本函数 → 无限递归冻结）。
  // 入栈由调用方 onSector 负责（见 viewTopology）。
  const v = $("view"); v.innerHTML = "";
  v.appendChild(el("h2", null, `${sectorName} · 成分股资金分布`));
  v.appendChild(el("div", "sub", "面积=成交额 · 颜色：红净流入/绿净流出 · 点股看博弈"));
  const box = el("div", "flow-chart"); box.style.height = "520px"; v.appendChild(box);
  const onStock = (sid, sn) => { logClick("下钻个股", sn); push(sn, () => viewSeries(sid, sn, "main_net", "daily", "main")); };
  const dq = tradeDate ? `&trade_date=${tradeDate}` : "";
  try {
    const data = await api(`/api/flow/topology/sector/${sectorId}?tier=${tier}&top_stocks=30${dq}`);
    renderFlowTreemap(box, data, onStock);
    CURRENT_REDRAW = () => { box.innerHTML = ""; renderFlowTreemap(box, data, onStock); };
  } catch (e) {
    box.appendChild(el("div", "hint", `${e.code}: ${e.message}`));
  }
}

// ---- 品种 Tab 切换：重置栈到根视图 ----
function selectAsset(asset) {
  ASSET = asset;
  stopTopoPlay();   // 切 tab 停回放
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.asset === asset));
  stack.length = 0;
  if (asset === "a_share") push("大盘", viewMarket);
  else if (asset === "topology") push("资金流向", () => viewTopology("main"));
  else push("ETF", viewEtf);
}

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => selectAsset(t.dataset.asset)));

// ---- 主题切换 ----
// [id, 名称, 预览色块(背景, 主色/强调)]
const THEMES = [
  ["light", "浅色", ["#FFFFFF", "#2E6BE6"]],
  ["blue-dark", "蓝灰深色", ["#1B2030", "#4B8BFF"]],
  ["black", "纯黑", ["#141416", "#3A7BFF"]],
  ["warm", "暖白", ["#FDFBF7", "#C9781E"]],
  ["slate", "石板灰", ["#282D34", "#5CA0F0"]],
];
function themeName(id) { const t = THEMES.find((x) => x[0] === id); return t ? t[1] : "浅色"; }
function applyTheme(name) {
  if (name === "light") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", name);
  localStorage.setItem("qc-theme", name);
  const cur = $("theme-current");
  if (cur) cur.textContent = themeName(name);
  document.querySelectorAll(".theme-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.theme === name));
  if (typeof CURRENT_REDRAW === "function") CURRENT_REDRAW();
}
function mountThemeSwitcher() {
  const host = $("theme-switch");
  if (!host) return;
  const saved = localStorage.getItem("qc-theme") || "light";
  // 触发按钮：色点 + 当前主题名 + ▾
  const trigger = el("button", "theme-trigger");
  trigger.innerHTML =
    `<span class="theme-dot" id="theme-dot"></span>`
    + `<span id="theme-current">${themeName(saved)}</span>`
    + `<span class="theme-caret">▾</span>`;
  // 下拉菜单
  const menu = el("div", "theme-menu");
  THEMES.forEach(([id, label, colors]) => {
    const item = el("button", "theme-item");
    item.dataset.theme = id;
    item.innerHTML =
      `<span class="swatch" style="background:${colors[0]};border-color:${colors[1]}"></span>`
      + `<span>${label}</span>`;
    item.onclick = () => { applyTheme(id); menu.classList.remove("open"); };
    menu.appendChild(item);
  });
  trigger.onclick = (e) => { e.stopPropagation(); menu.classList.toggle("open"); };
  document.addEventListener("click", () => menu.classList.remove("open"));
  host.appendChild(trigger);
  host.appendChild(menu);
  applyTheme(saved);
}
mountThemeSwitcher();

selectAsset("a_share");
