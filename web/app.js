// QuantChive 市场观测 · 栈式下钻 SPA（contracts/service-api.md）。
// 铁律：金额是后端已定序/已定标的 Decimal 字符串；前端仅格式化展示，绝不做数值排序/加减。
// 能力驱动：读 has_five_tier/mode/supported_metrics 决定渲染，不支持的指标灰字告知。

const $ = (id) => document.getElementById(id);
const el = (t, cls, html) => { const e = document.createElement(t); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

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
  v.appendChild(el("h2", null, `${name} · ${metric} 时序`));
  // 粒度切换：daily 历史（回填）/ intraday 当日分钟
  const toggle = el("div", "gran-toggle");
  [["daily", "日线历史"], ["intraday", "当日分钟"]].forEach(([g, label]) => {
    const b = el("button", "gran" + (g === gran ? " active" : ""), label);
    b.onclick = () => viewSeries(subjectId, name, metric, g);
    toggle.appendChild(b);
  });
  v.appendChild(toggle);
  try {
    const data = await api(`/api/subjects/${subjectId}/series?metric=${metric}&granularity=${gran}`);
    v.appendChild(sparkline(data.points));
    v.appendChild(el("div", "sub",
      `粒度 ${data.granularity} · ${data.points.length} 点 · 截至 ${data.trade_date}`));
    setProvenance(data.provenance);
  } catch (e) {
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

selectAsset("a_share");
