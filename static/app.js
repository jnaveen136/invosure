/* InvoSure front end. Plain JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SYM = { USD: "$", EUR: "€", GBP: "£", INR: "₹" };
const money = (v, cur = "USD") => {
  if (v === null || v === undefined) return "-";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (v < 0 ? "-" : "") + (SYM[cur] || cur + " ") + s;
};
const compact = (v) => "$" + (Math.abs(v) >= 1000 ? (v / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "k" : v.toFixed(0));
const fmtDate = (iso) => {
  if (!iso) return "-";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};
const fmtTime = (iso) => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
};
const toast = (msg) => {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
};
const statusClass = (s) => ({ Approved: "ok", "Needs review": "warn", Rejected: "bad" }[s] || "idle");
const pill = (s) => `<span class="pill ${statusClass(s)}">${esc(s || "Running")}</span>`;
const OUTCOME = {
  pass: ["Passed", "ok"], review: ["Needs review", "warn"], reject: ["Rejected", "bad"], na: ["Not applicable", "idle"],
  waiting: ["Waiting", "idle"], running: ["Checking", "idle"],
};
const outcomePill = (o) => `<span class="pill ${OUTCOME[o][1]}">${OUTCOME[o][0]}</span>`;

const ICON = {
  pass: '<svg viewBox="0 0 20 20" fill="none"><path d="M5 10.5l3.2 3L15 6.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  warn: '<svg viewBox="0 0 20 20" fill="none"><path d="M10 5.5v5.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><circle cx="10" cy="14.5" r="1.3" fill="currentColor"/></svg>',
  fail: '<svg viewBox="0 0 20 20" fill="none"><path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  skip: '<svg viewBox="0 0 20 20" fill="none"><path d="M6 10h8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  pending: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="2.2" fill="currentColor"/></svg>',
  running: '<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="2.6" fill="currentColor"/></svg>',
};

const KEY_CHECKS = [
  ["split", "Split invoices", "Several invoices against one PO must never add up to more than was ordered, and bills must not be split to dodge the approval limit."],
  ["duplicates", "Duplicate invoices", "Same file, same invoice number (even written differently), or the same amount from the same vendor a few days apart."],
  ["delivery", "Partial delivery", "Goods are only paid for once they have been received. Billing exactly what arrived is fine."],
  ["info", "Missing or conflicting information", "Required fields, amounts that contradict each other, a missing or wrong PO, and how reliably the document was read."],
];

/* ------------------------------------------------------------------ router */
let current = { view: null, cleanup: null };
const VIEWS = { overview: viewOverview, process: viewProcess, invoices: viewInvoices, pos: viewPOs, rules: viewRules, batch: viewBatch };

function route() {
  const raw = location.hash.replace("#", "") || "overview";
  const [view, arg] = raw.split("-run-");
  const fn = VIEWS[view] || viewOverview;
  if (current.cleanup) current.cleanup();
  current = { view, cleanup: null };
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === (VIEWS[view] ? view : "overview")));
  app.innerHTML = "";
  fn(arg);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ light / dark mode */
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const isDark = () => {
  const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : darkQuery.matches;
};
function syncThemeButton() {
  const b = $("#theme-btn"), dark = isDark();
  b.classList.toggle("is-dark", dark);
  b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  b.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
}
$("#theme-btn").addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("invosure-theme", next); } catch (e) {}
  syncThemeButton();
});
darkQuery.addEventListener("change", syncThemeButton); // follows the system until a choice is made
syncThemeButton();

/* ------------------------------------------------------------------ overview (dashboard) */
const ICONS = {
  doc: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
  star: '<path d="M12 4l2.5 5.1 5.5.8-4 3.9.9 5.5L12 16.7 7.1 19.3l.9-5.5-4-3.9 5.5-.8z"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
};
const svgIcon = (name, size = 20) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
const ktile = (icon, label, value, sub, tone = "") => `<div class="panel ktile"><span class="kicon ${tone}">${svgIcon(icon)}</span>
  <div style="min-width:0"><div class="label">${label}</div><div class="kvalue">${value}</div><div class="sub">${sub}</div></div></div>`;

async function viewOverview() {
  app.innerHTML = `<div id="ov" class="stack"><div class="panel empty">Loading…</div></div>`;
  const s = await api("/api/stats");
  const range = s.first_day ? (s.first_day === s.last_day ? fmtDate(s.first_day) : `${fmtDate(s.first_day)} - ${fmtDate(s.last_day)}`) : "No invoices yet";
  const pct = (n) => (s.processed ? Math.round((n / s.processed) * 100) : 0);
  $("#ov").innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Welcome back</div><h1 class="page-title">Invoice decisions overview</h1>
      <p class="lede">How invoices are being read, checked and decided.</p></div>
      <div class="row"><span class="date-chip">${svgIcon("cal", 16)} ${esc(range)}</span><a class="btn" href="#process">+ Process invoice</a></div></div>
    <div class="tiles">
      ${ktile("doc", "Invoices processed", s.processed, `${s.approved} approved · ${s.on_hold} need review · ${s.rejected} rejected`)}
      ${ktile("shield", "Straight-through rate", s.auto_rate + "%", "Approved with no person involved", "ok")}
      ${ktile("star", "High-confidence reads", s.high_conf, `${pct(s.high_conf)}% read at 95% confidence or more`)}
      ${ktile("alert", "Exceptions", s.exceptions, `${money(s.value_at_risk)} money at risk`, "bad")}
    </div>
    <div class="chart-row">
      <section class="panel chart-card"><div class="chart-head"><h2 class="chart-title">Straight-through rate over time</h2><span class="muted">by invoice, in order processed</span></div>
        <div class="chart-box" id="line"></div></section>
      <section class="panel chart-card"><div class="chart-head"><h2 class="chart-title">Decisions</h2></div><div id="donut"></div></section>
      <section class="panel chart-card"><div class="chart-head"><h2 class="chart-title">Extraction confidence</h2><span class="muted">invoices by average field confidence</span></div><div id="dist"></div></section>
    </div>
    <section class="panel">
      <div class="panel-head"><h2 class="section-title">Recent documents</h2><a href="#invoices">View all documents →</a></div>
      ${recentTable(s.recent)}
    </section>
    <section class="panel banner">${svgIcon("shield", 22)}<div style="min-width:0"><b>Every decision explained</b>
      <div class="muted">InvoSure shows the evidence, what is still uncertain and who acts next, for every invoice.</div></div>
      <a class="btn secondary" href="#rules">Configure rules →</a></section>`;
  drawLine($("#line"), s.series);
  drawDonut($("#donut"), s.decisions, s.processed);
  drawDist($("#dist"), s.confidence_dist, s.processed);
  bindRunRows();
}

function confBar(v) {
  if (v === null || v === undefined) return '<span class="muted">-</span>';
  const tone = v >= 95 ? "ok" : v >= 80 ? "mid" : v >= 50 ? "warn" : "bad";
  return `<span class="confbar"><span class="num">${v}%</span><span class="cb-track"><span class="cb-fill ${tone}" style="width:${v}%"></span></span></span>`;
}
function recentTable(rows) {
  if (!rows.length) return `<div class="empty"><strong>No documents yet</strong>Process a test invoice to fill the dashboard.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Invoice number</th><th>Supplier</th><th>Invoice date</th><th class="num">Total amount</th><th>Status</th><th>Confidence</th><th></th></tr></thead><tbody>
  ${rows.map((r) => `<tr class="clickable" data-open="${r.id}"><td><span class="row" style="gap:8px">${svgIcon("doc", 16)}${esc(r.invoice_number || "Missing")}</span></td>
    <td>${esc(r.vendor_name || "-")}</td><td>${fmtDate(r.invoice_date)}</td><td class="num">${money(r.total, r.currency || "USD")}</td>
    <td>${pill(r.final_status)}</td><td>${confBar(r.confidence)}</td><td><button class="icon-btn" aria-label="View" data-open="${r.id}">${svgIcon("eye", 18)}</button></td></tr>`).join("")}
  </tbody></table></div>`;
}

function drawLine(el, series) {
  if (!series.length) { el.innerHTML = `<div class="empty">No data yet.</div>`; return; }
  const W = 560, H = 220, L = 40, R = 14, T = 12, B = 30;
  const n = series.length;
  const x = (i) => L + (n === 1 ? (W - L - R) / 2 : (i / (n - 1)) * (W - L - R));
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  const pts = series.map((p, i) => [x(i), y(p.rate)]);
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `${line} L ${pts[n - 1][0].toFixed(1)} ${y(0)} L ${pts[0][0].toFixed(1)} ${y(0)} Z`;
  const grid = [0, 25, 50, 75, 100].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 8}" y="${y(v) + 4}" class="axis" text-anchor="end">${v}%</text>`).join("");
  const ticks = [...new Set([0, Math.floor((n - 1) / 2), n - 1])].map((i) => `<text x="${x(i)}" y="${H - 8}" class="axis" text-anchor="middle">#${i + 1}</text>`).join("");
  const last = pts[n - 1];
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Straight-through rate after each invoice; latest ${series[n - 1].rate}%">
    ${grid}${ticks}<path d="${area}" class="area"/><path d="${line}" class="line"/>
    <circle cx="${last[0]}" cy="${last[1]}" r="5" class="endpoint"/>
    <text x="${Math.min(last[0], W - R - 4)}" y="${last[1] - 12}" class="endlabel" text-anchor="end">${series[n - 1].rate}%</text>
    <line class="xhair" x1="0" x2="0" y1="${T}" y2="${y(0)}" visibility="hidden"/><circle class="hoverdot" r="5" visibility="hidden"/>
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="hit"/></svg><div class="tip" hidden></div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), xh = svg.querySelector(".xhair"), dot = svg.querySelector(".hoverdot");
  svg.querySelector(".hit").addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), sx = ((e.clientX - r.left) / r.width) * W;
    let i = n === 1 ? 0 : Math.round(((sx - L) / (W - L - R)) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const p = series[i];
    xh.setAttribute("x1", pts[i][0]); xh.setAttribute("x2", pts[i][0]); xh.setAttribute("visibility", "visible");
    dot.setAttribute("cx", pts[i][0]); dot.setAttribute("cy", pts[i][1]); dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `<b>Invoice #${p.n}: ${p.rate}%</b><br><span class="muted">${esc(p.file)}</span><br>${pill(p.status)}`;
    const left = (pts[i][0] / W) * r.width;
    tip.style.left = Math.min(Math.max(left - 90, 0), r.width - 190) + "px";
  });
  svg.querySelector(".hit").addEventListener("mouseleave", () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
}

function drawDonut(el, d, total) {
  const parts = [["Approved", "ok"], ["Needs review", "warn"], ["Rejected", "bad"]].map(([k, c]) => ({ k, c, v: d[k] || 0 }));
  const R = 62, C = 2 * Math.PI * R, gap = total > 1 ? 3 : 0;
  let off = 0;
  const arcs = parts.filter((p) => p.v).map((p) => {
    const len = (p.v / Math.max(total, 1)) * C;
    const a = `<circle r="${R}" cx="80" cy="80" class="seg ${p.c}" stroke-dasharray="${Math.max(len - gap, 0.1)} ${C}" stroke-dashoffset="${-off}"><title>${p.k}: ${p.v}</title></circle>`;
    off += len; return a;
  }).join("");
  el.innerHTML = `<div class="donut-wrap"><svg viewBox="0 0 160 160" class="donut" role="img" aria-label="Decisions: ${parts.map((p) => `${p.k} ${p.v}`).join(", ")}">
    <circle r="${R}" cx="80" cy="80" class="track"/><g transform="rotate(-90 80 80)">${arcs}</g>
    <text x="80" y="80" text-anchor="middle" class="dn-num">${total}</text><text x="80" y="100" text-anchor="middle" class="dn-sub">Total</text></svg>
    <ul class="legend">${parts.map((p) => `<li><i class="${p.c}"></i><span>${p.k}<br><b class="num">${p.v}</b> <span class="muted">(${total ? Math.round((p.v / total) * 100) : 0}%)</span></span></li>`).join("")}</ul></div>`;
}

function drawDist(el, dist, total) {
  const max = Math.max(1, ...dist.map((x) => x.count));
  el.innerHTML = `<div class="dist">${dist.map((b) => `<div class="dist-row"><span class="muted num">${b.label}</span>
    <span class="dist-track"><span class="dist-fill" style="width:${(b.count / max) * 100}%"></span></span>
    <span class="num">${b.count} <span class="muted">(${total ? Math.round((b.count / total) * 100) : 0}%)</span></span></div>`).join("")}</div>`;
}

/* ------------------------------------------------------------------ same-time processing */
async function viewBatch(arg) {
  const ids = (arg || "").split(".").filter(Boolean);
  app.innerHTML = `<div class="page-head"><div><div class="eyebrow">Same-time processing</div><h1 class="page-title">Two invoices, one PO balance</h1>
    <p class="lede">Both invoices were sent at the same moment. PO-1026 has $20,000 left and each invoice asks for $15,000. Without protection, both checks would see $20,000 available and both would be approved. InvoSure lets only one invoice at a time read the billing history and record its decision.</p></div>
    <a class="btn secondary" href="#process">Process another</a></div><div class="batch-grid" id="batch"></div>`;
  let timer = null;
  const tick = async () => {
    const runs = await Promise.all(ids.map((id) => api(`/api/runs/${id}`).catch(() => null)));
    $("#batch").innerHTML = runs.map((r) => {
      if (!r) return `<section class="panel panel-pad">Not found</section>`;
      const steps = r.stages || [];
      const doneN = steps.filter((s) => ["pass", "warn", "fail", "skip"].includes(s.status)).length;
      const waiting = steps.some((s) => s.status === "running" && /Waiting for another invoice/.test(s.summary || ""));
      const waited = ((r.meta || {}).timeline || []).some((e) => /Waited for another invoice/.test(e.text));
      const dec = (steps.find((s) => s.id === "decide") || {}).data;
      return `<section class="panel panel-pad batch-card ${r.status === "done" ? statusClass(r.final_status) : ""}">
        <div class="attn-top"><div><div class="attn-title">${esc(r.invoice_number || r.filename)}</div><div class="cell-sub">${esc(r.filename)}</div></div>${r.status === "done" ? pill(r.final_status) : pill(null)}</div>
        <div class="bar" style="margin:14px 0 8px"><span class="now" style="width:${(doneN / steps.length) * 100}%"></span></div>
        <div class="muted" style="font-size:13.5px">${doneN} of ${steps.length} steps</div>
        ${waiting ? `<div class="wait-note">Waiting for the other invoice to finish its checks…</div>` : ""}
        ${r.status === "done" && waited ? `<div class="wait-note">Waited for the other invoice, then re-read the PO balance.</div>` : ""}
        ${dec && dec.why ? `<p style="margin:12px 0 0;font-weight:600">${esc(dec.why)}</p>` : ""}
        <a href="#process-run-${r.id}" style="display:inline-block;margin-top:12px">Open the full run →</a></section>`;
    }).join("");
    if (runs.every((r) => r && r.status === "done")) clearInterval(timer);
  };
  await tick();
  timer = setInterval(tick, 600);
  current.cleanup = () => clearInterval(timer);
}

/* ------------------------------------------------------------------ audit report, extracted data, timeline */
function scoreRing(score, label) {
  const R = 52, C = 2 * Math.PI * R, tone = label === "Payment blocked" ? "bad" : label === "Needs attention" ? "warn" : "ok";
  return `<svg viewBox="0 0 130 130" class="ring" role="img" aria-label="Audit score ${score} out of 100">
    <circle cx="65" cy="65" r="${R}" class="track"/><circle cx="65" cy="65" r="${R}" class="ring-fill ${tone}" stroke-dasharray="${(score / 100) * C} ${C}" transform="rotate(-90 65 65)"/>
    <text x="65" y="68" text-anchor="middle" class="ring-num">${score}</text><text x="65" y="88" text-anchor="middle" class="ring-sub">/ 100</text></svg>`;
}
function auditPanel(d) {
  const a = d.audit;
  if (!a) return "";
  const tone = a.label === "Looks good" ? "ok" : a.label === "Needs attention" ? "warn" : "bad";
  const LV = { error: ["bad", "Error"], warning: ["warn", "Warning"], note: ["note", "Note"] };
  return `<section class="panel audit-panel">
    <div class="panel-head"><div class="row"><span class="kicon">${svgIcon("shield")}</span><div><h2 class="section-title">Audit report</h2><div class="muted" style="font-size:13px">Every check run on this invoice</div></div></div>
      <span class="pill ${tone}">${esc(a.label)}</span></div>
    <div class="audit-top">${scoreRing(a.score, a.label)}
      <div class="audit-counts">
        <div class="cnt ok"><b class="num">${a.passed.length}</b><span>Passed</span></div>
        <div class="cnt warn"><b class="num">${a.warnings.length}</b><span>Warnings</span></div>
        <div class="cnt bad"><b class="num">${a.errors.length}</b><span>Errors</span></div>
        <div class="cnt note"><b class="num">${a.notes.length}</b><span>Notes</span></div>
      </div>
      <div class="audit-conf"><div class="label">Extraction confidence</div><div class="kvalue">${d.extraction_confidence}%</div><div class="sub">average across fields read</div></div>
    </div>
    ${a.findings.length ? `<div class="findings">${a.findings.map((f) => `<div class="finding ${LV[f.level][0]}">
      <div style="min-width:0"><div class="f-title">${LV[f.level][1]} · ${esc(f.title)}</div><div class="f-msg">${esc(f.message)}</div>${f.action ? `<div class="f-act">→ ${esc(f.action)}</div>` : ""}</div>
      ${f.fix_field ? `<button class="btn sm secondary" data-fix="${f.fix_field}">Fix manually</button>` : ""}</div>`).join("")}</div>` : ""}
    <div class="passed-head eyebrow">Passed checks (${a.passed.length})</div>
    <div class="passed-grid">${a.passed.map((p) => `<div class="passed"><span class="ci">${ICON.pass}</span><div style="min-width:0"><b>${esc(p.label)}</b><div class="muted">${esc(p.detail || p.area)}</div></div></div>`).join("")}</div>
  </section>`;
}

function extractedPanel(run, d) {
  const ext = (run.stages || []).find((s) => s.id === "extract");
  const lines = ext && ext.data && ext.data.fields ? ext.data.fields.line_items || [] : [];
  const cur = run.currency || "USD";
  const fieldRows = (d.fields || []).map((f) => {
    const type = (d.editable.find((e) => e.key === f.key) || {}).type;
    const val = f.value === null || f.value === undefined ? "" : f.value;
    return `<tr><td><label for="fx-${f.key}">${esc(f.label)}</label></td>
      <td><input class="field fx" id="fx-${f.key}" data-key="${f.key}" data-orig="${esc(val)}" type="${type === "date" ? "date" : "text"}" value="${esc(val)}" placeholder="Not found"></td>
      <td>${confBar(f.confidence)}</td><td class="cell-sub">${esc(f.basis)}</td></tr>`;
  }).join("");
  return `<section class="panel" id="extracted-panel">
    <div class="panel-head"><div><h2 class="section-title">Extracted data</h2><div class="muted" style="font-size:13px">Edit any field, then re-run every check on the corrected values.</div></div>
      <div class="seg-tabs" role="tablist"><button class="on" data-tab="review">Review</button><button data-tab="table">Table</button><button data-tab="json">JSON</button></div></div>
    <div data-pane="review"><div class="table-wrap"><table class="fields-table"><thead><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Why</th></tr></thead><tbody>${fieldRows}</tbody></table></div>
      <div class="panel-pad row" style="justify-content:space-between"><span class="muted" id="fx-hint">No changes yet.</span><button class="btn" id="fx-run" disabled>Re-run checks with my changes</button></div></div>
    <div data-pane="table" hidden><div class="table-wrap"><table><thead><tr><th>Line</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead><tbody>
      ${lines.map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${l.qty}</td><td class="num">${money(l.unit_price, cur)}</td><td class="num">${money(l.amount, cur)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No itemised lines</td></tr>'}</tbody></table></div></div>
    <div data-pane="json" hidden><pre class="text-dump" style="margin:16px 24px;max-height:420px">${esc(JSON.stringify(ext && ext.data ? ext.data.fields : {}, null, 2))}</pre></div>
  </section>`;
}
function bindExtracted(run) {
  const panel = $("#extracted-panel");
  if (!panel) return;
  panel.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    panel.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
    panel.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.tab));
  }));
  const inputs = [...panel.querySelectorAll(".fx")];
  const changed = () => inputs.filter((i) => i.value.trim() !== i.dataset.orig);
  inputs.forEach((i) => i.addEventListener("input", () => {
    const c = changed();
    i.classList.toggle("edited", i.value.trim() !== i.dataset.orig);
    $("#fx-run").disabled = !c.length;
    $("#fx-hint").textContent = c.length ? `${c.length} field${c.length > 1 ? "s" : ""} changed` : "No changes yet.";
  }));
  $("#fx-run").addEventListener("click", async () => {
    const fields = {};
    changed().forEach((i) => (fields[i.dataset.key] = i.value.trim()));
    try {
      await api(`/api/runs/${run.id}/correct`, { method: "POST", body: JSON.stringify({ fields }) });
      toast("Re-running every check with your corrections");
      showRun(run.id);
    } catch (e) { toast(e.message); }
  });
  document.querySelectorAll("[data-fix]").forEach((b) => b.addEventListener("click", () => {
    const inp = $(`#fx-${b.dataset.fix}`);
    if (!inp) return;
    panel.querySelector('[data-tab="review"]').click();
    inp.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => inp.focus(), 350);
  }));
}

function timelinePanel(run) {
  const tl = (run.meta || {}).timeline || [];
  if (!tl.length) return "";
  const t = (iso) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
  return `<section class="panel"><div class="panel-head"><h2 class="section-title">Invoice timeline</h2><span class="muted">Everything that happened, in order</span></div>
    <ol class="timeline">${tl.map((e) => `<li class="${e.kind}"><span class="tl-time num">${t(e.at)}</span><span class="tl-dot"></span><span class="tl-text">${esc(e.text)}</span></li>`).join("")}</ol></section>`;
}

function attnCard(r) {
  return `<div class="attn-card ${statusClass(r.final_status)}">
    <div class="attn-top"><div><div class="attn-title">${esc(r.vendor_name || r.filename)}</div>
      <div class="cell-sub">${esc(r.invoice_number || "No number")} · ${money(r.total, r.currency || "USD")}</div></div>${pill(r.final_status)}</div>
    <div class="attn-foot"><span class="row">${(r.route || "").split(", ").map((x) => `<span class="chip">${esc(x)}</span>`).join(" ")}</span>
      <button class="btn sm secondary" data-open="${r.id}">Open</button></div></div>`;
}

function runsTable(rows) {
  if (!rows.length) return `<div class="empty"><strong>No invoices yet</strong>Process one of the test invoices to see it here.</div>`;
  return `<div class="table-wrap"><table><thead><tr>
    <th>Vendor</th><th>Invoice</th><th>PO</th><th class="num">Amount</th><th>Decision</th><th>Routed to</th><th>Processed</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable" data-open="${r.id}">
      <td><div class="cell-main">${esc(r.vendor_name || "-")}</div><div class="cell-sub">${esc(r.filename)}</div></td>
      <td><div>${esc(r.invoice_number || "Missing")}</div><div class="cell-sub">${fmtDate(r.invoice_date)}</div></td>
      <td>${esc(r.po_number || "-")}</td>
      <td class="num">${money(r.total, r.currency || "USD")}</td>
      <td>${r.status !== "done" ? pill(null) : pill(r.final_status)}${r.review ? `<div class="cell-sub">Reviewed, was ${esc(r.review.from)}</div>` : ""}</td>
      <td>${r.route ? `<span class="chip">${esc(r.route.split(", ")[0])}</span>` : ""}</td>
      <td class="cell-sub">${fmtTime(r.created_at)}</td></tr>`).join("")}
    </tbody></table></div>`;
}
function bindRunRows(root = app) {
  root.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); openDrawer(el.dataset.open); }));
}

/* ------------------------------------------------------------------ process */
async function viewProcess(runId) {
  if (runId) return showRun(runId);
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Process an invoice</h1>
    <p class="lede">Upload a vendor invoice PDF or pick a test invoice. Every invoice runs through all of the checks below, and each step shows live.</p></div></div>
    <div class="intake">
      <div class="stack">
        <label class="drop" id="drop" for="file-in">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5M12 18v-6M9 15l3-3 3 3"/></svg>
          <strong>Drop an invoice PDF here</strong><span class="muted">or click to choose a file. Digital and scanned PDFs both work.</span>
          <input type="file" id="file-in" accept="application/pdf,.pdf" hidden>
        </label>
        <section class="panel panel-pad">
          <div class="eyebrow">The four key checks, on every invoice</div>
          <ol class="checklist one" style="margin-top:14px">
            ${KEY_CHECKS.map(([, t, d], i) => `<li><span class="n">${i + 1}</span><span><b>${t}</b><span class="muted">${d}</span></span></li>`).join("")}
          </ol>
          <div class="eyebrow" style="margin-top:20px">Plus standard checks</div>
          <p class="muted" style="margin:6px 0 0;font-size:14px">Approved vendor, active vendor, bank account, PO open, currency, lines on PO, prices, tax, invoice date, approval limit. Simple pass or fail; only shown in detail when one fails.</p>
          <div class="eyebrow" style="margin-top:20px">Three outcomes</div>
          <div class="row" style="margin-top:8px">${pill("Approved")}<span class="muted" style="font-size:13.5px">every check passed</span></div>
          <div class="row" style="margin-top:6px">${pill("Needs review")}<span class="muted" style="font-size:13.5px">missing information, ambiguity or an open discrepancy</span></div>
          <div class="row" style="margin-top:6px">${pill("Rejected")}<span class="muted" style="font-size:13.5px">a confirmed violation of a business rule</span></div>
        </section>
      </div>
      <section class="panel">
        <div class="panel-head"><h2 class="section-title">Test invoices</h2><span class="muted">Click one to run it</span></div>
        <div id="samples"><div class="empty">Loading…</div></div>
      </section>
    </div>`;
  const list = await api("/api/samples");
  const groups = [...new Set(list.map((s) => s.group))];
  let i = 0;
  $("#samples").innerHTML = groups.map((g) => `<div class="sample-group">${esc(g)}${g === "Same-time processing" ? ' <button class="btn sm" id="run-both">Run both at the same time</button>' : ""}</div><ul class="samples">${list.filter((s) => s.group === g).map((s) => `
    <li data-file="${esc(s.file)}"><span class="idx">${String(++i).padStart(2, "0")}</span>
      <span style="min-width:0"><div class="s-label">${esc(s.label)}</div><div class="s-file">${esc(s.file)}</div></span>${pill(s.expect)}</li>`).join("")}</ul>`).join("");
  $("#samples").querySelectorAll("li[data-file]").forEach((li) => li.addEventListener("click", async () => {
    const { id } = await api("/api/runs/sample", { method: "POST", body: JSON.stringify({ file: li.dataset.file }) });
    location.hash = "process-run-" + id;
  }));
  const both = $("#run-both");
  if (both) both.addEventListener("click", async (e) => {
    e.stopPropagation();
    const files = list.filter((x) => x.group === "Same-time processing").map((x) => x.file);
    const { ids } = await api("/api/runs/batch", { method: "POST", body: JSON.stringify({ files }) });
    location.hash = "batch-run-" + ids.join(".");
  });
  const drop = $("#drop"), input = $("#file-in");
  const send = async (file) => {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") return toast("Choose a PDF file");
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/runs", { method: "POST", body: fd });
    if (!r.ok) return toast("Upload failed");
    location.hash = "process-run-" + (await r.json()).id;
  };
  input.addEventListener("change", () => send(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => send(e.dataTransfer.files[0]));
}

function showRun(id) {
  app.innerHTML = `<div class="page-head"><div><div class="eyebrow">Live run</div><h1 class="page-title" id="run-title">Processing…</h1>
    <p class="lede" id="run-sub"></p></div><div class="row"><a class="btn secondary" href="#process">Process another</a></div></div>
    <div class="stack">
      <section class="panel"><div class="panel-head"><h2 class="section-title">Key checks</h2><span class="muted">Run on every invoice</span></div>
        <div class="key-grid" id="keys"></div>
        <div id="std-line"></div></section>
      <div id="decision"></div>
      <div id="after-decision" class="stack"></div>
      <div class="runview">
        <section class="panel"><div class="panel-head"><h2 class="section-title">Step by step</h2><span class="muted">Click a step to open it</span></div><ol class="steps" id="steps"></ol></section>
        <section class="panel doc-frame"><div class="panel-head"><span class="eyebrow">Source document</span><a href="/api/runs/${id}/file" target="_blank" rel="noopener">Open PDF</a></div>
          <div class="doc-pages" id="doc-pages"></div></section>
      </div>
    </div>`;
  api(`/api/runs/${id}/pages`).then(({ pages }) => {
    $("#doc-pages").innerHTML = pages ? Array.from({ length: pages }, (_, i) => `<img src="/api/runs/${id}/page/${i + 1}.png" alt="Invoice page ${i + 1}">`).join("")
      : `<div class="empty">Preview not available for this file.</div>`;
  });
  const open = new Set();
  let lastRun = null;
  const es = new EventSource(`/api/runs/${id}/stream`);
  es.onmessage = (e) => { lastRun = JSON.parse(e.data); renderRun(lastRun, open); };
  let poll = null;
  es.addEventListener("done", () => es.close());
  es.onerror = () => {
    // Live connection dropped (slow server, restart). Keep checking until the run has a decision.
    es.close();
    if (poll) return;
    const check = () => api(`/api/runs/${id}`).then((r) => {
      lastRun = r; renderRun(r, open);
      if (r.status === "done") { clearInterval(poll); poll = null; }
    }).catch(() => {});
    check();
    poll = setInterval(check, 2500);
  };
  current.cleanup = () => { es.close(); if (poll) clearInterval(poll); };
}

function keyCard(st, i, title, live = true) {
  let o = "waiting";
  if (st) o = st.status === "running" ? "running" : st.status === "pending" ? "waiting" : st.outcome || "na";
  const verdict = st && st.verdict ? st.verdict : o === "running" ? "Checking now…" : "Not run yet.";
  return `<div class="key-card ${OUTCOME[o][1]} ${o === "running" ? "is-running" : ""}">
    <div class="kc-top"><span class="kc-title"><span class="n">${i + 1}</span>${esc(title)}</span>${outcomePill(o)}</div>
    <div class="kc-verdict">${esc(verdict)}</div></div>`;
}
function stdLine(st) {
  if (!st || !st.data || st.status === "pending" || st.status === "running") return `<div class="std-line idle"><span class="muted">Standard checks: ${st && st.status === "running" ? "checking…" : "waiting"}</span></div>`;
  const d = st.data;
  if (!d.failed || !d.failed.length) return `<div class="std-line ok"><span class="ci">${ICON.pass}</span><span><b>All ${d.ran} standard checks passed.</b> <span class="muted">Vendor, bank account, PO, currency, prices, tax, dates and approval limit.</span></span></div>`;
  return `<div class="std-line bad"><span class="ci">${ICON.fail}</span><span><b>${d.failed.length} of ${d.ran} standard checks failed:</b> ${d.failed.map((f) => `${esc(f.label)} <span class="muted">(${esc(f.detail)})</span>`).join("; ")}</span></div>`;
}

function renderRun(run, open) {
  const decided = run.status === "done";
  $("#run-title").textContent = run.vendor_name ? `${run.vendor_name}${run.invoice_number ? " · " + run.invoice_number : ""}` : run.filename;
  $("#run-sub").innerHTML = `${esc(run.filename)}${run.extractor ? " · read by " + esc(run.extractor) : ""}`;
  const steps = run.stages || [];
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  $("#keys").innerHTML = KEY_CHECKS.map(([id, t], i) => keyCard(byId[id], i, t)).join("");
  $("#std-line").innerHTML = stdLine(byId.standard);
  $("#steps").innerHTML = steps.filter((s) => s.id !== "decide").map((s) => {
    const auto = s.status === "fail" || s.status === "warn";
    const isOpen = open.has(s.id) || (auto && !open.has("x-" + s.id));
    const kIdx = KEY_CHECKS.findIndex(([k]) => k === s.id);
    return `<li class="step ${s.status}" data-step="${s.id}">
      <span class="dot">${ICON[s.status] || ICON.pending}</span>
      <div style="min-width:0"><div class="step-head" data-toggle="${s.id}"><span class="step-name">${kIdx >= 0 ? `<span class="key-tag">Key check ${kIdx + 1}</span>` : ""}${esc(s.name)}</span>
        <span class="step-summary">${s.status === "pending" ? "Waiting" : s.status === "running" ? "Working…" : esc(s.summary || "")}${s.ms !== undefined ? `<span class="step-ms">${(s.ms / 1000).toFixed(1)}s</span>` : ""}</span></div>
        ${isOpen && s.checks ? `<div class="step-body">${stageBody(s, run)}</div>` : ""}</div></li>`;
  }).join("");
  $("#steps").querySelectorAll("[data-toggle]").forEach((h) => h.addEventListener("click", () => {
    const sid = h.dataset.toggle, el = h.closest(".step");
    if (el.querySelector(".step-body")) { open.delete(sid); open.add("x-" + sid); } else { open.add(sid); open.delete("x-" + sid); }
    renderRun(run, open);
  }));
  const dec = byId.decide;
  $("#decision").innerHTML = decided && dec && dec.data && dec.data.status ? decisionCard(dec.data, run) :
    `<div class="decision pending"><div class="decision-top"><span class="decision-status" style="color:var(--ink-3)">Working…</span>${pill(null)}</div>
     <div class="muted">${steps.filter((s) => ["pass", "warn", "fail", "skip"].includes(s.status)).length} of ${steps.length} steps complete</div></div>`;
  if (decided) bindDecision(run);
  // Audit report, editable fields and timeline: drawn once per finished run so typing is never wiped.
  const after = $("#after-decision");
  const sig = decided && dec && dec.data ? run.id + "|" + (dec.data.why || "") + "|" + ((run.meta || {}).timeline || []).length + "|" + !!run.review : "pending";
  if (after.dataset.sig !== sig) {
    after.dataset.sig = sig;
    after.innerHTML = decided && dec && dec.data && dec.data.audit ? setupCallout(run) + auditPanel(dec.data) + extractedPanel(run, dec.data) + timelinePanel(run) : "";
    if (decided) bindExtracted(run);
    const sb = $("#setup-btn");
    if (sb) sb.addEventListener("click", () => openSetupWizard(run));
  }
}

function checksList(checks) {
  if (!checks || !checks.length) return "";
  return `<ul class="checks">${checks.map((c) => `<li class="${c.status}"><span class="ci">${ICON[c.status] || ""}</span><span>${esc(c.label)}</span><span class="c-detail">${esc(c.detail)}</span></li>`).join("")}</ul>`;
}
const bullets = (title, items, cls = "") => (items && items.length ? `<div><div class="eyebrow" style="margin-bottom:6px">${title}</div><ul class="ul ${cls}">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "");

function stageBody(s, run) {
  const d = s.data || {};
  if (s.id === "read" && d.text) return checksList(s.checks) + `<details><summary>Show the text that was read</summary><div class="text-dump">${esc(d.text)}</div></details>`;
  if (s.id === "extract" && d.fields) {
    const f = d.fields, cur = f.currency || "USD";
    const kv = [["Vendor", f.vendor_name], ["Invoice no.", f.invoice_number], ["Invoice date", f.invoice_date], ["Due date", f.due_date],
      ["PO", f.po_number], ["Currency", cur], ["Subtotal", money(f.subtotal, cur)],
      ["Tax", f.tax_amount === null ? "-" : money(f.tax_amount, cur) + (f.tax_rate ? ` (${f.tax_rate}%)` : "")], ["Total", money(f.total, cur)],
      ["Bank account", f.bank_account ? "ending " + f.bank_account.slice(-4) : "-"]];
    return `<div class="kv">${kv.map(([k, v]) => `<div><span>${k}</span><span>${esc(v ?? "-")}</span></div>`).join("")}</div>` +
      (f.line_items && f.line_items.length ? `<div class="table-wrap"><table class="mini"><thead><tr><th>Line</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Amount</th></tr></thead><tbody>
        ${f.line_items.map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${l.qty}</td><td class="num">${money(l.unit_price, cur)}</td><td class="num">${money(l.amount, cur)}</td></tr>`).join("")}</tbody></table></div>` : "") +
      checksList(s.checks.filter((c) => c.status !== "pass"));
  }
  if (s.id === "standard") {
    const failed = s.checks.filter((c) => c.status === "fail" || c.status === "warn");
    return (failed.length ? checksList(failed) : "") +
      `<details ${failed.length ? "" : "open"}><summary>${failed.length ? "Show all standard checks" : "All standard checks"}</summary>${checksList(s.checks)}</details>`;
  }
  if (s.key) {
    let meter = "";
    if (s.id === "split" && d.usage) {
      const u = d.usage, cur = run.currency || "USD";
      const before = Math.min(100, (u.billed_before / u.po_total) * 100), after = Math.min(100, u.pct_after);
      const cls = u.pct_after > 100.01 ? "bad" : u.pct_after > 90 ? "warn" : "ok";
      meter = `<div class="meter ${cls}"><div class="meter-top"><span>${esc(run.po_number || "PO")} billed after this invoice</span><span class="pct">${u.pct_after}%</span></div>
        <div class="bar"><span class="now" style="width:${after}%"></span><span class="before" style="width:${before}%"></span></div>
        <div class="meter-scale"><span>Earlier ${money(u.billed_before, cur)} · this invoice ${money(u.this_invoice, cur)}</span><span>PO ${money(u.po_total, cur)}</span></div></div>`;
    }
    return `<div class="verdict-line">${outcomePill(s.outcome || "na")} <span>${esc(s.verdict || "")}</span></div>` + meter +
      bullets("Evidence", s.evidence) + bullets("Uncertain", s.uncertain, "unc") + checksList(s.checks);
  }
  return checksList(s.checks);
}

function decisionCard(d, run) {
  const cls = statusClass(run.final_status);
  const review = run.review;
  return `<div class="decision ${cls}">
    <div class="decision-top"><span class="decision-status">${esc(run.final_status)}</span>
      <span class="row"><span class="conf conf-${(d.confidence || "").toLowerCase()}">Confidence: ${esc(d.confidence)}</span>
      <span class="muted">Routed to</span> ${d.route.split(", ").map((r) => `<span class="chip">${esc(r)}</span>`).join(" ")}</span></div>
    <div><div class="eyebrow" style="margin-bottom:6px">Why</div><div class="decision-headline">${esc(d.why || d.headline)}</div></div>
    ${d.reasons && d.reasons.length > 1 ? `<ul class="reasons">${d.reasons.map((r) => `<li><span class="chip ${r.severity === "reject" ? "chip-bad" : "chip-warn"}">${esc(r.source)}</span><span>${esc(r.message)}</span></li>`).join("")}</ul>` : ""}
    <div class="dgrid">
      ${bullets("Evidence", d.evidence)}
      ${d.uncertain && d.uncertain.length ? bullets("What remains uncertain", d.uncertain, "unc") : `<div><div class="eyebrow" style="margin-bottom:6px">What remains uncertain</div><div class="muted" style="font-size:14px">Nothing. Every value came from a digital PDF and matched the records exactly.</div></div>`}
    </div>
    ${bullets("What happens next", d.next_steps)}
    ${d.vendor_message ? emailBlock(d.vendor_message) : ""}
    ${review ? auditBlock(review) : run.decision === "Needs review" ? reviewBox() : ""}
  </div>`;
}
const emailBlock = (m) => `<div class="email"><div class="email-head"><span><span class="muted">Draft to vendor:</span> <b>${esc(m.to)}</b><br><span class="muted">Subject:</span> ${esc(m.subject)}</span>
  <button class="btn sm secondary" data-copy>Copy email</button></div><pre data-mail>${esc(m.body)}</pre></div>`;
const auditBlock = (r) => `<div class="audit"><b>${r.action === "approve" ? "Approved" : "Rejected"} by ${esc(r.by)}</b> on ${fmtTime(r.at)} (system decision was ${esc(r.from)}).<br>Note: ${esc(r.note)}</div>`;
const reviewBox = () => `<div class="review-box"><label class="eyebrow" for="rv-note">Reviewer decision</label>
  <textarea class="field" id="rv-note" placeholder="What did you confirm? This note goes into the audit trail."></textarea>
  <div class="row"><button class="btn" data-review="approve">Approve for payment</button><button class="btn danger" data-review="reject">Reject</button></div></div>`;

function bindDecision(run, root = document, after) {
  root.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const box = b.closest(".email"), pre = box.querySelector("[data-mail]");
    const head = box.querySelector(".email-head span").innerText;
    try { await navigator.clipboard.writeText(head + "\n\n" + pre.innerText); toast("Email copied"); }
    catch { const r = document.createRange(); r.selectNodeContents(pre); getSelection().removeAllRanges(); getSelection().addRange(r); toast("Text selected, press Ctrl+C"); }
  }));
  root.querySelectorAll("[data-review]").forEach((b) => b.addEventListener("click", async () => {
    const note = root.querySelector("#rv-note").value.trim();
    if (!note) { root.querySelector("#rv-note").focus(); return toast("Add a note for the audit trail first"); }
    try {
      await api(`/api/runs/${run.id}/review`, { method: "POST", body: JSON.stringify({ action: b.dataset.review, note }) });
      toast(b.dataset.review === "approve" ? "Approved for payment" : "Rejected");
      const fresh = await api(`/api/runs/${run.id}`);
      if (after) after(fresh); else renderRun(fresh, new Set());
    } catch (e) { toast(e.message); }
  }));
}

/* ------------------------------------------------------------------ invoices */
async function viewInvoices() {
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Invoices</h1><p class="lede">Every processed invoice with its decision and who it was routed to. Click a row for the full explanation.</p></div></div>
    <div class="filters">
      <input class="field grow" id="q" placeholder="Search vendor, invoice number or PO…" aria-label="Search">
      <select class="field" id="fs" aria-label="Decision"><option value="">All decisions</option><option>Approved</option><option>Needs review</option><option>Rejected</option></select>
      <select class="field" id="fv" aria-label="Vendor"><option value="">All vendors</option></select>
    </div><section class="panel" id="inv-table"></section>`;
  const runs = (await api("/api/runs")).filter((r) => r.status === "done");
  const vendors = [...new Set(runs.map((r) => r.vendor_name).filter(Boolean))].sort();
  $("#fv").innerHTML += vendors.map((v) => `<option>${esc(v)}</option>`).join("");
  const draw = () => {
    const q = $("#q").value.toLowerCase(), st = $("#fs").value, v = $("#fv").value;
    const rows = runs.filter((r) => (!st || r.final_status === st) && (!v || r.vendor_name === v) &&
      (!q || [r.vendor_name, r.invoice_number, r.po_number, r.filename].join(" ").toLowerCase().includes(q)));
    $("#inv-table").innerHTML = runsTable(rows);
    bindRunRows($("#inv-table"));
  };
  ["input", "change"].forEach((ev) => ["#q", "#fs", "#fv"].forEach((s) => $(s).addEventListener(ev, draw)));
  draw();
}

/* ------------------------------------------------------------------ drawer */
async function openDrawer(id) {
  const drawer = $("#drawer"), body = $("#drawer-body");
  drawer.hidden = false;
  body.innerHTML = `<div class="muted">Loading…</div>`;
  const run = await api(`/api/runs/${id}`);
  const draw = (r) => {
    const byId = Object.fromEntries((r.stages || []).map((s) => [s.id, s]));
    const dec = byId.decide && byId.decide.data;
    const cur = r.currency || "USD";
    body.innerHTML = `<h2 class="drawer-title">Invoice detail</h2>
      <div class="detail-rows">
        <div class="band"><span>${esc(r.vendor_name || r.filename)}</span><span>${esc(r.invoice_number || "No number")}</span></div>
        <div><span>Decision</span><span>${pill(r.final_status)}</span></div>
        <div><span>Amount</span><span class="num">${money(r.total, cur)}</span></div>
        <div><span>Invoice date</span><span>${fmtDate(r.invoice_date)}</span></div>
        <div><span>Purchase order</span><span>${esc(r.po_number || "-")}</span></div>
        <div><span>Source file</span><span><a href="/api/runs/${r.id}/file" target="_blank" rel="noopener">${esc(r.filename)}</a></span></div>
      </div>
      <div class="key-grid two-col">${KEY_CHECKS.map(([k, t], i) => keyCard(byId[k], i, t)).join("")}</div>
      <div class="panel" style="box-shadow:none">${stdLine(byId.standard)}</div>
      ${dec ? decisionCard(dec, r) : ""}
      ${dec && dec.audit ? `<div class="detail-rows"><div><span>Audit score</span><span>${dec.audit.score} / 100 · ${esc(dec.audit.label)}</span></div><div><span>Extraction confidence</span><span>${dec.extraction_confidence}%</span></div></div>` : ""}
      ${timelinePanel(r)}
      <a class="btn secondary" href="#process-run-${r.id}">Open the full run to edit fields</a>`;
    bindDecision(r, body, (fresh) => { draw(fresh); if (current.view !== "process") route(); });
    body.querySelector('a[href^="#process-run"]').addEventListener("click", closeDrawer);
  };
  draw(run);
}
function closeDrawer() { $("#drawer").hidden = true; }
document.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

/* ------------------------------------------------------------------ purchase orders */
async function viewPOs() {
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Vendors and purchase orders</h1>
    <p class="lede">The records every invoice is checked against. Add a supplier and its PO here before processing its invoices, or set them up straight from an invoice that was rejected for a missing vendor or PO.</p></div>
    <div class="row"><button class="btn secondary" id="add-vendor">Add vendor</button><button class="btn" id="add-po">Add purchase order</button></div></div>
    <div class="seg-tabs" role="tablist" style="margin-bottom:16px"><button class="on" data-ptab="pos">Purchase orders</button><button data-ptab="vendors">Vendors</button></div>
    <div data-ppane="pos"><div class="po-grid" id="pos"></div></div>
    <div data-ppane="vendors" hidden><section class="panel" id="vendors"></section></div>`;
  const ref = await api("/api/reference");
  const { pos, vendors } = ref;
  document.querySelectorAll("[data-ptab]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-ptab]").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll("[data-ppane]").forEach((p) => (p.hidden = p.dataset.ppane !== b.dataset.ptab));
  }));
  $("#add-vendor").addEventListener("click", () => openVendorForm({}, viewPOs));
  $("#add-po").addEventListener("click", () => openPOForm({}, viewPOs));
  const addedChip = '<span class="chip">Added here</span>';
  $("#pos").innerHTML = pos.slice().sort((a, b) => (b.added === a.added ? 0 : b.added ? 1 : -1)).map((p) => {
    const cls = p.status !== "open" ? "" : p.pct > 100.01 ? "bad" : p.pct >= 90 ? "warn" : "ok";
    return `<section class="panel po-card ${p.status !== "open" ? "closed" : ""}">
      <div class="attn-top"><div><div class="attn-title">${esc(p.po_number)} · ${esc(p.vendor_name)} ${p.added ? addedChip : ""}</div>
        <div class="cell-sub">Raised ${fmtDate(p.po_date)} by ${esc(p.buyer)} · ${esc(p.currency)}</div></div>
        <span class="row">${p.added ? `<button class="btn sm danger" data-del-po="${esc(p.po_number)}">Remove</button>` : ""}<span class="pill ${p.status === "open" ? "ok" : "idle"}">${p.status === "open" ? "Open" : "Closed"}</span></span></div>
      <div class="meter ${cls}"><div class="meter-top"><span>Billed</span><span class="pct">${p.pct}%</span></div>
        <div class="bar"><span class="now" style="width:${Math.min(100, p.pct)}%"></span></div>
        <div class="meter-scale"><span>${money(p.billed_value, p.currency)} billed</span><span>${money(Math.max(p.total - p.billed_value, 0), p.currency)} left of ${money(p.total, p.currency)}</span></div></div>
      <div class="table-wrap"><table class="mini"><thead><tr><th>Line</th><th class="num">Ordered</th><th class="num">Received</th><th class="num">Billed</th><th class="num">Unit price</th></tr></thead>
        <tbody>${p.lines.map((l) => `<tr><td>${esc(l.description)}${l.line_type === "service" ? ' <span class="cell-sub">service</span>' : ""}</td>
          <td class="num">${l.qty}</td><td class="num">${l.line_type === "service" ? "-" : l.received}</td>
          <td class="num" style="${l.billed > l.qty ? "color:var(--bad);font-weight:700" : ""}">${l.billed}</td><td class="num">${money(l.unit_price, p.currency)}</td></tr>`).join("")}</tbody></table></div>
    </section>`;
  }).join("");
  $("#vendors").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Vendor</th><th>Tax ID</th><th>Bank account</th><th>Terms</th><th>Status</th><th></th></tr></thead><tbody>
    ${vendors.slice().sort((a, b) => (b.added === a.added ? a.vendor_id.localeCompare(b.vendor_id) : b.added ? 1 : -1)).map((v) => `<tr>
      <td><div class="cell-main">${esc(v.name)} ${v.added ? addedChip : ""}</div><div class="cell-sub">${esc(v.vendor_id)}${v.contact_email ? " · " + esc(v.contact_email) : ""}</div></td>
      <td class="num">${esc(v.tax_id || "-")}</td><td class="num">${v.bank_account ? "ending " + esc(String(v.bank_account).slice(-4)) : "-"}</td>
      <td>${esc(v.payment_terms_days)} days</td><td><span class="pill ${v.status === "active" ? "ok" : "bad"}">${esc(v.status)}</span></td>
      <td>${v.added ? `<button class="btn sm danger" data-del-vendor="${esc(v.vendor_id)}">Remove</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  document.querySelectorAll("[data-del-po]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/pos/${encodeURIComponent(b.dataset.delPo)}`, { method: "DELETE" }); toast(`${b.dataset.delPo} removed`); viewPOs(); } catch (e) { toast(e.message); }
  }));
  document.querySelectorAll("[data-del-vendor]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/vendors/${encodeURIComponent(b.dataset.delVendor)}`, { method: "DELETE" }); toast("Vendor removed"); viewPOs(); } catch (e) { toast(e.message); }
  }));
}

/* ------------------------------------------------------------------ add vendor / PO forms (in the side panel) */
const fieldRow = (id, label, value = "", attrs = "") => `<label class="form-row" for="${id}"><span>${label}</span><input class="field" id="${id}" value="${esc(value)}" ${attrs}></label>`;

function vendorFields(v = {}) {
  return `<div class="form-grid">
    ${fieldRow("v-name", "Vendor name *", v.name, 'required autocomplete="off"')}
    ${fieldRow("v-tax", "Tax ID", v.tax_id)}
    ${fieldRow("v-bank", "Bank account", v.bank_account)}
    ${fieldRow("v-routing", "Routing / BIC", v.bank_routing)}
    ${fieldRow("v-cur", "Currency", v.currency || "USD")}
    ${fieldRow("v-terms", "Payment terms (days)", v.payment_terms_days || 30, 'type="number" min="0"')}
    ${fieldRow("v-email", "Contact email", v.contact_email)}
    ${fieldRow("v-phone", "Phone", v.phone)}
  </div>`;
}
const readVendor = () => ({
  name: $("#v-name").value, tax_id: $("#v-tax").value, bank_account: $("#v-bank").value, bank_routing: $("#v-routing").value,
  currency: $("#v-cur").value, payment_terms_days: Number($("#v-terms").value || 30), contact_email: $("#v-email").value, phone: $("#v-phone").value,
});

function lineRow(l = {}) {
  const type = l.line_type || "goods";
  return `<tr class="po-line">
    <td><input class="field" data-l="description" value="${esc(l.description || "")}" placeholder="What was ordered" aria-label="Description"></td>
    <td><input class="field num" data-l="qty" type="number" min="0" step="any" value="${l.qty ?? 1}" aria-label="Quantity"></td>
    <td><input class="field num" data-l="unit_price" type="number" min="0" step="any" value="${l.unit_price ?? ""}" aria-label="Unit price"></td>
    <td><select class="field" data-l="line_type" aria-label="Type"><option value="goods" ${type === "goods" ? "selected" : ""}>Goods</option><option value="service" ${type === "service" ? "selected" : ""}>Service</option></select></td>
    <td><input class="field num" data-l="received" type="number" min="0" step="any" value="${l.received ?? l.qty ?? 0}" aria-label="Received" ${type === "service" ? "disabled" : ""}></td>
    <td><button type="button" class="icon-btn" data-rm aria-label="Remove line">✕</button></td></tr>`;
}
function poFields(po = {}, vendors = [], fixedVendorLabel = null) {
  return `<div class="form-grid">
    ${fieldRow("p-num", "PO number", po.po_number)}
    <label class="form-row" for="p-vendor"><span>Vendor *</span>${fixedVendorLabel ? `<input class="field" id="p-vendor" value="${esc(fixedVendorLabel)}" disabled>` :
      `<select class="field" id="p-vendor">${vendors.map((v) => `<option value="${esc(v.vendor_id)}" ${v.vendor_id === po.vendor_id ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>`}</label>
    ${fieldRow("p-date", "PO date", po.po_date || "", 'type="date"')}
    ${fieldRow("p-buyer", "Buyer", po.buyer || "")}
  </div>
  <div class="eyebrow" style="margin:16px 0 8px">Lines</div>
  <div class="table-wrap"><table class="mini po-lines"><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Unit price</th><th>Type</th><th class="num">Received</th><th></th></tr></thead>
    <tbody id="p-lines">${(po.lines && po.lines.length ? po.lines : [{}]).map(lineRow).join("")}</tbody></table></div>
  <button type="button" class="btn sm secondary" id="p-add-line" style="margin-top:10px">+ Add line</button>
  <p class="muted" style="font-size:13px;margin:8px 0 0">"Received" is what the warehouse has taken in. Services need no receipt.</p>`;
}
function bindLines() {
  const tb = $("#p-lines");
  const wire = (tr) => {
    tr.querySelector("[data-rm]").addEventListener("click", () => { if (tb.children.length > 1) tr.remove(); });
    tr.querySelector('[data-l="line_type"]').addEventListener("change", (e) => { tr.querySelector('[data-l="received"]').disabled = e.target.value === "service"; });
  };
  [...tb.children].forEach(wire);
  $("#p-add-line").addEventListener("click", () => { tb.insertAdjacentHTML("beforeend", lineRow({})); wire(tb.lastElementChild); });
}
const readLines = () => [...document.querySelectorAll("#p-lines .po-line")].map((tr) => {
  const g = (k) => tr.querySelector(`[data-l="${k}"]`);
  return { description: g("description").value, qty: Number(g("qty").value || 0), unit_price: Number(g("unit_price").value || 0),
    line_type: g("line_type").value, received: g("line_type").value === "service" ? 0 : Number(g("received").value || 0) };
});
function openPanel(html) {
  $("#drawer").hidden = false;
  $("#drawer-body").innerHTML = html;
}

function openVendorForm(prefill, done) {
  openPanel(`<h2 class="drawer-title">Add vendor</h2><p class="muted" style="margin:0">Invoices are matched to vendors by tax ID first, then by name. The bank account must match the one printed on invoices, or they are stopped as a possible fraud.</p>
    <form id="vendor-form" class="stack" style="gap:14px">${vendorFields(prefill)}<div class="form-err" id="form-err" hidden></div>
    <div class="row"><button class="btn" type="submit">Save vendor</button><button class="btn secondary" type="button" data-close-form>Cancel</button></div></form>`);
  $("[data-close-form]").addEventListener("click", closeDrawer);
  $("#vendor-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const v = await api("/api/vendors", { method: "POST", body: JSON.stringify(readVendor()) }); toast(`${v.name} added as ${v.vendor_id}`); closeDrawer(); done && done(v); }
    catch (err) { $("#form-err").textContent = err.message; $("#form-err").hidden = false; }
  });
}

async function openPOForm(prefill, done) {
  const [{ vendors }, next] = await Promise.all([api("/api/reference"), api("/api/pos/next-number")]);
  const active = vendors.filter((v) => v.status === "active");
  openPanel(`<h2 class="drawer-title">Add purchase order</h2><p class="muted" style="margin:0">Invoices are checked line by line against these quantities and prices.</p>
    <form id="po-form" class="stack" style="gap:6px">${poFields({ po_number: next.po_number, ...prefill }, active)}<div class="form-err" id="form-err" hidden></div>
    <div class="row" style="margin-top:14px"><button class="btn" type="submit">Save purchase order</button><button class="btn secondary" type="button" data-close-form>Cancel</button></div></form>`);
  $("[data-close-form]").addEventListener("click", closeDrawer);
  bindLines();
  $("#po-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/pos", { method: "POST", body: JSON.stringify({ po_number: $("#p-num").value, vendor_id: $("#p-vendor").value,
        po_date: $("#p-date").value, buyer: $("#p-buyer").value, lines: readLines() }) });
      toast(`${r.po_number} added`); closeDrawer(); done && done(r);
    } catch (err) { $("#form-err").textContent = err.message; $("#form-err").hidden = false; }
  });
}

/* Set up vendor and PO straight from an invoice that was stopped because they are not on file. */
const SETUP_CODES = ["unknown_vendor", "no_po", "po_conflict"];
function setupCallout(run) {
  const codes = ((run.result || {}).issues || []).map((i) => i.code);
  if (!codes.some((c) => SETUP_CODES.includes(c)) || run.review) return "";
  const msg = codes.includes("unknown_vendor") ? "This supplier is not in the vendor list, so the invoice could not be matched or paid."
    : "No matching purchase order was found for this invoice.";
  return `<section class="panel setup-callout"><div style="min-width:0"><b>Expected this invoice?</b><div class="muted">${msg} If the purchase is genuine, set up the missing records from what was read on the invoice, then check it again.</div></div>
    <button class="btn" id="setup-btn">Set up from this invoice</button></section>`;
}
async function openSetupWizard(run) {
  const dec = ((run.stages || []).find((s) => s.id === "decide") || {}).data || {};
  const ext = ((run.stages || []).find((s) => s.id === "extract") || {}).data || {};
  const F = Object.fromEntries((dec.fields || []).map((f) => [f.key, f.value]));
  const raw = ext.fields || {};
  const [{ vendors, pos }, next] = await Promise.all([api("/api/reference"), api("/api/pos/next-number")]);
  const norm = (x) => String(x || "").replace(/[^A-Z0-9]/gi, "").toUpperCase();
  const known = vendors.find((v) => (F.vendor_tax_id && norm(v.tax_id) === norm(F.vendor_tax_id)) || v.name.toLowerCase() === String(F.vendor_name || "").toLowerCase());
  const poExists = F.po_number && pos.some((p) => p.po_number === F.po_number);
  const tax = raw.tax_inclusive && F.total && F.tax_amount ? (F.total - F.tax_amount) / F.total : 1;
  const lines = (raw.line_items || []).map((l) => ({ description: l.description, qty: Math.abs(l.qty), unit_price: Math.round(Math.abs(l.unit_price) * tax * 100) / 100, line_type: "goods", received: Math.abs(l.qty) }));
  const poPrefill = { po_number: F.po_number && !poExists ? F.po_number : next.po_number, po_date: F.invoice_date || "", lines };
  openPanel(`<h2 class="drawer-title">Set up from this invoice</h2>
    <p class="muted" style="margin:0">Filled in from what was read on ${esc(run.filename)}. Check every value against your own records before saving. Anything created here is marked "Added here" and can be removed.</p>
    <form id="setup-form" class="stack" style="gap:6px">
      ${known ? `<div class="detail-rows"><div><span>Vendor</span><span>${esc(known.name)} (${esc(known.vendor_id)}) is already on file</span></div></div>`
        : `<div class="eyebrow" style="margin-top:12px">1. New vendor</div>${vendorFields({ name: F.vendor_name, tax_id: F.vendor_tax_id, bank_account: F.bank_account, bank_routing: raw.bank_routing, currency: F.currency || "USD" })}`}
      ${poExists ? `<div class="detail-rows"><div><span>Purchase order</span><span>${esc(F.po_number)} is already on file</span></div></div>`
        : `<div class="eyebrow" style="margin-top:16px">${known ? "1" : "2"}. New purchase order</div>${poFields(poPrefill, vendors, known ? known.name : "The new vendor above")}`}
      <div class="form-err" id="form-err" hidden></div>
      <div class="row" style="margin-top:14px"><button class="btn" type="submit">Save and check again</button><button class="btn secondary" type="button" data-close-form>Cancel</button></div>
    </form>`);
  $("[data-close-form]").addEventListener("click", closeDrawer);
  if (!poExists) bindLines();
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#form-err");
    try {
      let vendorId = known ? known.vendor_id : null;
      if (!known) vendorId = (await api("/api/vendors", { method: "POST", body: JSON.stringify(readVendor()) })).vendor_id;
      if (!poExists) await api("/api/pos", { method: "POST", body: JSON.stringify({ po_number: $("#p-num").value, vendor_id: vendorId,
        po_date: $("#p-date").value, buyer: $("#p-buyer").value, lines: readLines() }) });
      await api(`/api/runs/${run.id}/recheck`, { method: "POST" });
      closeDrawer(); toast("Saved. Checking the invoice again"); showRun(run.id);
    } catch (ex) { err.textContent = ex.message + (known ? "" : " (if the vendor was already saved, it is now in the list; reopen this form)"); err.hidden = false; }
  });
}

/* ------------------------------------------------------------------ rules */
async function viewRules() {
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Rules and thresholds</h1>
    <p class="lede">The extractor only reads the invoice. These rules make every decision, so a change here changes the outcome of the next run.</p></div></div>
    <div class="stack">
      <section class="panel"><div class="panel-head"><h2 class="section-title">Thresholds</h2>
        <div class="row"><button class="btn secondary" id="simulate">Simulate before saving</button><button class="btn" id="save">Save changes</button></div></div>
        <div class="rules-grid" id="rules"></div><div id="sim-out"></div></section>
      <section class="panel panel-pad"><h2 class="section-title" style="margin-bottom:16px">How a decision is made</h2>
        <ol class="checklist">
          <li><span class="n">A</span><span><b>Approved</b><span class="muted">All four key checks and every standard check passed. Paid on the due date to the bank account on file.</span></span></li>
          <li><span class="n">R</span><span><b>Needs review</b><span class="muted">Information is missing, something is ambiguous, or a discrepancy is still open. The run names who must act and what to ask the vendor.</span></span></li>
          <li><span class="n">X</span><span><b>Rejected</b><span class="muted">A confirmed violation: a duplicate, billing beyond the PO, or any failed standard check such as an unknown vendor or changed bank account.</span></span></li>
          <li><span class="n">O</span><span><b>Override</b><span class="muted">A reviewer can approve or reject an invoice that needs review. A note is required and kept in the audit trail.</span></span></li>
        </ol></section>
      <section class="panel panel-pad"><h2 class="section-title">Demo data</h2>
        <p class="lede" style="margin-bottom:14px">Clears processed invoices, uploaded files, threshold changes and any vendors or POs added in the app. The built-in demo vendors, POs, goods receipts and invoice ledger stay.</p>
        <div id="reset-area"><button class="btn danger" id="reset">Reset demo data</button></div></section>
    </div>`;
  const rules = await api("/api/rules");
  const draw = (rs) => ($("#rules").innerHTML = rs.map((r) => `<div class="rule"><div><label for="r-${r.key}"><b>${esc(r.label)}</b></label>
    <div class="muted" style="font-size:13.5px">${esc(r.help)}</div></div><input class="field" type="number" step="any" id="r-${r.key}" data-key="${r.key}" value="${r.value}"></div>`).join(""));
  draw(rules);
  const values = () => { const b = {}; document.querySelectorAll("[data-key]").forEach((i) => (b[i.dataset.key] = Number(i.value))); return b; };
  $("#simulate").addEventListener("click", async () => {
    $("#sim-out").innerHTML = `<div class="panel-pad muted">Re-deciding every processed invoice with these values…</div>`;
    const r = await api("/api/simulate", { method: "POST", body: JSON.stringify(values()) });
    const sm = r.summary, diff = sm.Approved.after - sm.Approved.before;
    const delta = (k) => { const d = sm[k].after - sm[k].before; return d ? `<span class="${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>` : '<span class="muted">no change</span>'; };
    const headline = !r.total ? "Process some invoices first; the simulation replays them."
      : !r.changed.length ? `None of the ${r.total} processed invoices would get a different decision.`
      : diff > 0 ? `With these rules, ${diff} more invoice${diff > 1 ? "s" : ""} would have been approved automatically.`
      : diff < 0 ? `With these rules, ${-diff} fewer invoice${diff < -1 ? "s" : ""} would have been approved automatically.`
      : `${r.changed.length} invoice${r.changed.length > 1 ? "s" : ""} would get a different decision.`;
    $("#sim-out").innerHTML = `<div class="sim">
      <div class="sim-head"><div class="eyebrow">Simulation · nothing saved yet</div><div class="sim-title">${esc(headline)}</div></div>
      <div class="sim-sum">${["Approved", "Needs review", "Rejected"].map((k) => `<div class="cnt ${statusClass(k)}"><span>${k}</span><b class="num">${sm[k].before} → ${sm[k].after}</b>${delta(k)}</div>`).join("")}</div>
      ${r.changed.length ? `<div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Today</th><th>With these rules</th><th>Why</th></tr></thead><tbody>
        ${r.changed.map((c) => `<tr><td><div class="cell-main">${esc(c.vendor_name || "-")}</div><div class="cell-sub">${esc(c.invoice_number || c.filename)}</div></td>
          <td>${pill(c.before)}</td><td>${pill(c.after)}</td><td class="cell-sub" style="max-width:420px">${esc(c.why)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="muted" style="font-size:13px">Every processed invoice was re-decided in the order it arrived, using the values already read. Click "Save changes" to use these rules for new invoices.</div></div>`;
  });
  $("#save").addEventListener("click", async () => {
    const body = {};
    document.querySelectorAll("[data-key]").forEach((i) => (body[i.dataset.key] = Number(i.value)));
    draw(await api("/api/rules", { method: "PUT", body: JSON.stringify(body) }));
    toast("Rules saved");
  });
  $("#reset").addEventListener("click", () => {
    $("#reset-area").innerHTML = `<div class="confirm-inline"><span>This removes every processed invoice.</span>
      <button class="btn danger sm" id="reset-yes">Yes, reset</button><button class="btn secondary sm" id="reset-no">Cancel</button></div>`;
    $("#reset-no").addEventListener("click", viewRules);
    $("#reset-yes").addEventListener("click", async () => { await api("/api/reset", { method: "POST" }); toast("Demo data reset"); viewRules(); });
  });
}

route();
