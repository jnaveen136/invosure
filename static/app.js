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
const VIEWS = { overview: viewOverview, process: viewProcess, invoices: viewInvoices, pos: viewPOs, rules: viewRules };

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

/* ------------------------------------------------------------------ overview */
async function viewOverview() {
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Accounts payable overview</h1>
    <p class="lede">Every invoice runs through four key checks and a set of standard checks, then is approved, sent for review or rejected, with the reasons and evidence shown.</p></div>
    <a class="btn" href="#process">Process an invoice</a></div><div id="ov" class="stack"><div class="panel empty">Loading…</div></div>`;
  const [s, runs] = await Promise.all([api("/api/stats"), api("/api/runs")]);
  const done = runs.filter((r) => r.status === "done");
  const attention = done.filter((r) => r.final_status !== "Approved" && !r.review).slice(0, 6);
  $("#ov").innerHTML = `
    <div class="tiles">
      ${tile("Invoices processed", s.processed, `${s.approved} approved, ${s.on_hold} need review, ${s.rejected} rejected`)}
      ${tile("Value processed", compact(s.value_processed), `${money(s.value_approved)} approved for payment`)}
      ${tile("Approved without a person", s.auto_rate + "%", "Straight-through rate")}
      ${tile("Value stopped", compact(s.value_on_hold + s.value_blocked), `${money(s.value_blocked)} rejected`)}
    </div>
    <section class="panel">
      <div class="panel-head"><h2 class="section-title">Key checks across all invoices</h2><span class="muted">Extraction: ${esc(s.extractor)}</span></div>
      <div class="key-stats">${s.key_stats.map(keyStat).join("")}</div>
    </section>
    <div class="two">
      <section class="panel">
        <div class="panel-head"><h2 class="section-title">Needs attention</h2><span class="muted">${attention.length ? "Not yet reviewed" : ""}</span></div>
        <div class="panel-pad">${attention.length ? `<div class="attn">${attention.map(attnCard).join("")}</div>` :
          `<div class="empty"><strong>Nothing waiting</strong>Invoices that need review or were rejected show up here.</div>`}</div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2 class="section-title">Most common reasons</h2></div>
        ${reasonsPanel(s.top_issues)}
      </section>
    </div>
    <section class="panel">
      <div class="panel-head"><h2 class="section-title">Recent invoices</h2><a href="#invoices">View all</a></div>
      ${runsTable(done.slice(0, 8))}
    </section>`;
  bindRunRows();
}
const tile = (label, value, sub) => `<div class="panel tile"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${esc(sub)}</div></div>`;
function keyStat(k, i) {
  const total = k.pass + k.review + k.reject + k.na || 1;
  const seg = (n, cls) => (n ? `<span class="seg ${cls}" style="width:${(n / total) * 100}%"></span>` : "");
  return `<div class="key-stat"><div class="ks-title"><span class="n">${i + 1}</span>${esc(k.title)}</div>
    <div class="stackbar">${seg(k.pass, "ok")}${seg(k.review, "warn")}${seg(k.reject, "bad")}${seg(k.na, "idle")}</div>
    <div class="ks-legend"><span><i class="ok"></i>${k.pass} passed</span><span><i class="warn"></i>${k.review} review</span><span><i class="bad"></i>${k.reject} rejected</span><span><i class="idle"></i>${k.na} n/a</span></div></div>`;
}
const REASON_NAMES = {
  dup_number: "Duplicate invoice number", dup_file: "Same file sent again", near_dup: "Possible duplicate",
  over_billed: "Billed beyond the PO", split_limit: "Split under approval limit", not_received: "Billed before delivery",
  missing_fields: "Missing fields", totals_conflict: "Amounts contradict", po_conflict: "Wrong or unclear PO", no_po: "No PO",
  vendor_conflict: "Vendor identity unclear", ocr_quality: "Hard-to-read scan",
  unknown_vendor: "Unknown vendor", blocked_vendor: "Blocked vendor", bank_changed: "Bank details changed",
  price_variance: "Price above PO", currency: "Currency mismatch", line_not_on_po: "Line not on PO", po_closed: "PO closed",
  tax_wrong: "Tax wrong", future_date: "Future-dated", stale: "Too old", approval_limit: "Above approval limit",
  not_pdf: "Not a PDF", unreadable: "Unreadable PDF", stage_error: "Processing error",
};
function reasonsPanel(top) {
  if (!top.length) return `<div class="empty">No exceptions yet.</div>`;
  const max = Math.max(...top.map((x) => x[1]));
  return `<div class="issue-bars">${top.map(([k, n]) => `<div><div class="ib-top"><span>${esc(REASON_NAMES[k] || k)}</span><span class="num">${n}</span></div>
    <div class="bar"><span class="now" style="width:${(n / max) * 100}%"></span></div></div>`).join("")}</div>`;
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
  $("#samples").innerHTML = groups.map((g) => `<div class="sample-group">${esc(g)}</div><ul class="samples">${list.filter((s) => s.group === g).map((s) => `
    <li data-file="${esc(s.file)}"><span class="idx">${String(++i).padStart(2, "0")}</span>
      <span style="min-width:0"><div class="s-label">${esc(s.label)}</div><div class="s-file">${esc(s.file)}</div></span>${pill(s.expect)}</li>`).join("")}</ul>`).join("");
  $("#samples").querySelectorAll("li[data-file]").forEach((li) => li.addEventListener("click", async () => {
    const { id } = await api("/api/runs/sample", { method: "POST", body: JSON.stringify({ file: li.dataset.file }) });
    location.hash = "process-run-" + id;
  }));
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
  es.addEventListener("done", () => es.close());
  es.onerror = () => { es.close(); if (!lastRun) api(`/api/runs/${id}`).then((r) => renderRun(r, open)).catch(() => toast("Run not found")); };
  current.cleanup = () => es.close();
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
      <a class="btn secondary" href="#process-run-${r.id}">Open the step-by-step run</a>`;
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
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Purchase order tracker</h1>
    <p class="lede">How much of each PO has been billed (ledger plus invoices approved here) and how much has been delivered. This is what the split-invoice and partial-delivery checks compare against.</p></div></div>
    <div class="po-grid" id="pos"></div>`;
  const { pos } = await api("/api/reference");
  $("#pos").innerHTML = pos.map((p) => {
    const cls = p.status !== "open" ? "" : p.pct > 100.01 ? "bad" : p.pct >= 90 ? "warn" : "ok";
    return `<section class="panel po-card ${p.status !== "open" ? "closed" : ""}">
      <div class="attn-top"><div><div class="attn-title">${esc(p.po_number)} · ${esc(p.vendor_name)}</div>
        <div class="cell-sub">Raised ${fmtDate(p.po_date)} by ${esc(p.buyer)} · ${esc(p.currency)}</div></div>
        <span class="pill ${p.status === "open" ? "ok" : "idle"}">${p.status === "open" ? "Open" : "Closed"}</span></div>
      <div class="meter ${cls}"><div class="meter-top"><span>Billed</span><span class="pct">${p.pct}%</span></div>
        <div class="bar"><span class="now" style="width:${Math.min(100, p.pct)}%"></span></div>
        <div class="meter-scale"><span>${money(p.billed_value, p.currency)}</span><span>${money(p.total, p.currency)}</span></div></div>
      <div class="table-wrap"><table class="mini"><thead><tr><th>Line</th><th class="num">Ordered</th><th class="num">Received</th><th class="num">Billed</th><th class="num">Unit price</th></tr></thead>
        <tbody>${p.lines.map((l) => `<tr><td>${esc(l.description)}${l.line_type === "service" ? ' <span class="cell-sub">service</span>' : ""}</td>
          <td class="num">${l.qty}</td><td class="num">${l.line_type === "service" ? "-" : l.received}</td>
          <td class="num" style="${l.billed > l.qty ? "color:var(--bad);font-weight:700" : ""}">${l.billed}</td><td class="num">${money(l.unit_price, p.currency)}</td></tr>`).join("")}</tbody></table></div>
    </section>`;
  }).join("");
}

/* ------------------------------------------------------------------ rules */
async function viewRules() {
  app.innerHTML = `<div class="page-head"><div><h1 class="page-title">Rules and thresholds</h1>
    <p class="lede">The extractor only reads the invoice. These rules make every decision, so a change here changes the outcome of the next run.</p></div></div>
    <div class="stack">
      <section class="panel"><div class="panel-head"><h2 class="section-title">Thresholds</h2><button class="btn" id="save">Save changes</button></div><div class="rules-grid" id="rules"></div></section>
      <section class="panel panel-pad"><h2 class="section-title" style="margin-bottom:16px">How a decision is made</h2>
        <ol class="checklist">
          <li><span class="n">A</span><span><b>Approved</b><span class="muted">All four key checks and every standard check passed. Paid on the due date to the bank account on file.</span></span></li>
          <li><span class="n">R</span><span><b>Needs review</b><span class="muted">Information is missing, something is ambiguous, or a discrepancy is still open. The run names who must act and what to ask the vendor.</span></span></li>
          <li><span class="n">X</span><span><b>Rejected</b><span class="muted">A confirmed violation: a duplicate, billing beyond the PO, or any failed standard check such as an unknown vendor or changed bank account.</span></span></li>
          <li><span class="n">O</span><span><b>Override</b><span class="muted">A reviewer can approve or reject an invoice that needs review. A note is required and kept in the audit trail.</span></span></li>
        </ol></section>
      <section class="panel panel-pad"><h2 class="section-title">Demo data</h2>
        <p class="lede" style="margin-bottom:14px">Clears processed invoices, uploaded files and threshold changes. Vendors, POs, goods receipts and the invoice ledger stay.</p>
        <div id="reset-area"><button class="btn danger" id="reset">Reset demo data</button></div></section>
    </div>`;
  const rules = await api("/api/rules");
  const draw = (rs) => ($("#rules").innerHTML = rs.map((r) => `<div class="rule"><div><label for="r-${r.key}"><b>${esc(r.label)}</b></label>
    <div class="muted" style="font-size:13.5px">${esc(r.help)}</div></div><input class="field" type="number" step="any" id="r-${r.key}" data-key="${r.key}" value="${r.value}"></div>`).join(""));
  draw(rules);
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
