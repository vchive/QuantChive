// QuantChive 市场观测 · 栈式下钻 SPA（contracts/service-api.md）。
// 铁律：金额是后端已定序/已定标的 Decimal 字符串；前端仅格式化展示，绝不做数值排序/加减。
// 能力驱动：读 has_five_tier/mode/supported_metrics 决定渲染，不支持的指标灰字告知。

const $ = (id) => document.getElementById(id);
const el = (t, cls, html) => { const e = document.createElement(t); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
let CURRENT_REDRAW = null;   // 主题切换时重绘当前图表的回调

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
async function viewSeries(subjectId, name, metric = "main_net", gran = "daily") {
  const v = $("view"); v.innerHTML = "";
  // 骨架屏（fetch 前）
  const skel = el("div", "skeleton"); skel.style.height = "520px"; skel.style.margin = "12px 0";
  v.appendChild(skel);
  // 粒度切换
  const toggle = el("div", "gran-toggle");
  [["daily", "日线历史"], ["intraday", "当日分钟"]].forEach(([g, label]) => {
    const b = el("button", "gran" + (g === gran ? " active" : ""), label);
    b.onclick = () => viewSeries(subjectId, name, metric, g);
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
    v.appendChild(toggle);
    // —— 主图 ——
    const chartBox = el("div", "flow-chart");
    chartBox.style.height = "520px";
    v.appendChild(chartBox);
    renderMainChart(chartBox, data);
    // 主题切换时重绘本图
    CURRENT_REDRAW = () => { chartBox.innerHTML = ""; renderMainChart(chartBox, data); };
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

// ---- 品种 Tab 切换：重置栈到根视图 ----
function selectAsset(asset) {
  ASSET = asset;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.asset === asset));
  stack.length = 0;
  if (asset === "a_share") push("大盘", viewMarket);
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
