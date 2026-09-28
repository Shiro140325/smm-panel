import { api, esc, fmtDate, initTheme, num, peso, PLATFORMS, PLATFORM_ORDER, tierBadge, toast } from "./common.js";
import { icons } from "./icons.js";
import { badgeSVG } from "./badges.js";

initTheme(icons);
document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = icons[el.dataset.icon](18)));
document.querySelectorAll("[data-logout]").forEach((b) => {
  b.innerHTML = icons.logout(20);
  b.addEventListener("click", async () => {
    try { await api("/admin/api/logout", { method: "POST" }); } catch { /* ignore */ }
    location.reload();
  });
});

const view = document.getElementById("view");
const $ = (id) => document.getElementById(id);
const ORDER_STATUS = {
  creating: ["Processing", "badge-pending"], pending: ["Pending", "badge-pending"],
  in_progress: ["In progress", "badge-progress"], completed: ["Completed", "badge-completed"],
  partial: ["Partial", "badge-partial"], canceled: ["Canceled", "badge-canceled"],
  failed: ["Failed", "badge-failed"], needs_review: ["Under review", "badge-review"],
};
const TOPUP_STATUS = {
  credited: ["Credited", "badge-completed"], pending: ["Awaiting payment", "badge-pending"],
  canceled: ["Canceled", "badge-partial"], expired: ["Expired", "badge-partial"],
};
const badge = (map, s) => { const [l, c] = map[s] || [s, "badge-pending"]; return `<span class="badge ${c}">${esc(l)}</span>`; };
const pill = (value, label, current, attr) =>
  `<button type="button" class="pill pill-round" ${attr}="${esc(value)}" aria-pressed="${value === current}">${esc(label)}</button>`;
const route = () => (location.hash.replace(/^#/, "") || "overview").split("?")[0];
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ------------------------------------------------------------ sign in */

async function start() {
  try {
    await api("/admin/api/me");
    showPanel();
  } catch (e) {
    showLogin(e.status === 503 ? e.message : "");
  }
}

function showLogin(notice) {
  $("panel").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("login-theme").classList.remove("hidden");
  const err = $("login-error");
  if (notice) { err.textContent = notice; err.classList.remove("hidden"); }
  $("admin-pass").focus();
  $("login-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const btn = $("login-btn");
    btn.disabled = true;
    err.classList.add("hidden");
    try {
      await api("/admin/api/login", { method: "POST", body: { password: $("admin-pass").value } });
      $("admin-pass").value = "";
      showPanel();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
    btn.disabled = false;
  };
}

function showPanel() {
  $("login").classList.add("hidden");
  $("login-theme").classList.add("hidden");
  $("panel").classList.remove("hidden");
  render();
}

/* any 401 mid-session (expired) → back to sign in. A reply that arrives after switching tabs is
   dropped (the promise never settles), so it can't draw into the tab now showing. */
async function call(path, opts) {
  const at = route();
  const stale = () => route() !== at;
  try {
    const r = await api(path, opts);
    return stale() ? new Promise(() => {}) : r;
  } catch (e) {
    if (e.status === 401) showLogin("Your session ended. Sign in again.");
    if (stale()) return new Promise(() => {});
    throw e;
  }
}

function render() {
  const name = route();
  const inMore = MORE.some(([n]) => n === name);
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const on = a.dataset.nav === name || (inMore && a.dataset.nav === "more" && a.closest(".tabbar"));
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  ({ overview: renderOverview, orders: renderOrders, customers: renderCustomers, topups: renderTopups, services: renderServices,
     growth: renderGrowth, affiliates: renderAffiliates, trials: renderTrials, ledger: renderLedger, errors: renderErrors,
     more: renderMore }[name]
    || renderOverview)();
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => { if (!$("panel").classList.contains("hidden")) render(); });

/* ------------------------------------------------------------ overview */

async function renderOverview() {
  view.innerHTML = `<div class="page-head"><h1>Overview</h1><button class="btn btn-secondary" id="refresh">${icons.refresh(16)}Refresh</button></div>
    <div id="ov"><div class="card empty">Loading…</div></div>`;
  $("refresh").onclick = renderOverview;
  let d;
  try { d = await call("/admin/api/overview"); } catch (e) { $("ov").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  const prov = d.providers.map((p) => p.error
    ? `<div class="stat"><span class="k">${esc(p.name)} balance</span><span class="v small">Couldn't load</span><span class="s">${esc(p.error)}</span></div>`
    : `<div class="stat"><span class="k">${esc(p.name)} balance</span><span class="v">${p.currency === "USD" ? "$" : ""}${num(p.balance.toFixed(2))}</span>
         <span class="s">≈ ${peso(p.balance * (p.currency === "USD" ? d.usd_to_php : 1))}</span></div>`).join("");
  const owed = d.balances_php;
  const covered = d.providers.reduce((t, p) => t + (p.error ? 0 : p.balance * (p.currency === "USD" ? d.usd_to_php : 1)), 0);
  const span = (k, label) => {
    const s = d.sales[k] || { orders: 0, revenue_php: 0, cost_php: 0, profit_php: 0, cost_unknown: 0 };
    return `<tr><td>${label}</td><td class="num">${num(s.orders)}</td><td class="num">${peso(s.revenue_php)}</td>
      <td class="num">${peso(s.cost_php)}${s.cost_unknown ? ` <span class="muted">(${s.cost_unknown} not reported yet)</span>` : ""}</td>
      <td class="num"><strong>${peso(s.profit_php)}</strong></td></tr>`;
  };
  $("ov").innerHTML = `
    ${d.needs_review ? `<a class="alert alert-bad" href="#orders?status=needs_review" style="text-decoration:none;margin-bottom:16px">
      ${icons.warn(20)}<span><strong>${num(d.needs_review)} order${d.needs_review === 1 ? "" : "s"} under review.</strong> Check them against SMMGen and refund any that weren't placed.</span></a>` : ""}
    <div class="stats">
      ${prov}
      <div class="stat"><span class="k">Customer balances</span><span class="v">${peso(owed)}</span>
        <span class="s">${covered >= owed ? "Covered by your provider balance" : `<span style="color:var(--bad)">Provider balance is ${peso(owed - covered)} short</span>`}</span></div>
      <div class="stat"><span class="k">Customers</span><span class="v">${num(d.customers)}</span><span class="s">${num(d.customers_7d)} new in 7 days</span></div>
      <div class="stat"><span class="k">Top-ups today</span><span class="v">${peso(d.topups_today)}</span><span class="s">${peso(d.topups_7d)} in 7 days · ${peso(d.topups_all)} all time</span></div>
    </div>
    <div class="card table-card" style="margin-top:18px"><div class="table-scroll">
      <table class="table"><thead><tr><th>Sales</th><th class="num">Orders</th><th class="num">Charged (after refunds)</th><th class="num">SMMGen cost</th><th class="num">Profit</th></tr></thead>
      <tbody>${span("today", "Today")}${span("7d", "Last 7 days")}${span("all", "All time")}</tbody></table>
    </div></div>
    <p class="hint">Cost is what SMMGen reports charging per order, converted at ₱${Number(d.usd_to_php).toFixed(2)}/USD. "Today" is Philippine time.</p>
    <div class="card panel" id="announce-card" style="margin-top:18px">
      <h3>Announcement bar</h3>
      <p class="hint" style="margin:0">One short line in a gray bar at the top of the dashboard, for logged-in customers only. Leave it empty to hide the bar.</p>
      <textarea class="textarea" id="announce-text" rows="2" placeholder="e.g. Maintenance tonight at 11 PM. Orders may start late."></textarea>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span class="hint" id="announce-count" style="flex:1"></span>
        <button type="button" class="btn btn-secondary" id="announce-clear">Remove bar</button>
        <button type="button" class="btn btn-primary" id="announce-save">Save</button>
      </div>
    </div>`;
  setupAnnouncement();
}

async function setupAnnouncement() {
  const box = $("announce-text"), count = $("announce-count");
  if (!box) return;
  let a;
  try { a = await call("/admin/api/announcement"); } catch (e) { count.textContent = e.message; return; }
  const max = a.max_chars;
  box.maxLength = max;
  box.value = a.text;
  const paint = () => {
    const n = box.value.replace(/\s+/g, " ").trim().length;
    count.textContent = `${n} / ${max} characters (spaces count)${a.text ? ` · live since ${fmtDate(a.updated_at)}` : " · not showing"}`;
  };
  box.addEventListener("input", paint);
  paint();
  const save = async (text) => {
    try {
      a = await call("/admin/api/announcement", { method: "PUT", body: { text } });
      box.value = a.text;
      paint();
      toast(a.text ? "Announcement is live" : "Announcement bar removed");
    } catch (e) { toast(e.message, { bad: true }); }
  };
  $("announce-save").onclick = () => save(box.value);
  $("announce-clear").onclick = () => save("");
}

/* ------------------------------------------------------------ orders */

const ORDER_FILTERS = [["", "All"], ["needs_review", "Under review"], ["pending", "Pending"], ["in_progress", "In progress"],
  ["completed", "Completed"], ["partial", "Partial"], ["canceled", "Canceled"], ["failed", "Failed"],
  ["refilling", "Refilling"], ["refunded", "Refunded"]];

function renderOrders() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  let status = params.get("status") || "", q = "";
  view.innerHTML = `<div class="page-head"><h1>Orders</h1></div>
    <div class="orders-toolbar">
      <div class="pill-row" role="group" aria-label="Filter">${ORDER_FILTERS.map(([v, l]) => pill(v, l, status, "data-f")).join("")}</div>
      <input class="input" id="q" type="search" placeholder="Order #, email or link">
    </div>
    <div class="card table-card orders-card"><div class="table-scroll orders-scroll"><table class="table orders-table">
      <thead><tr><th>ID</th><th>Date</th><th>Customer and service</th><th class="num">Qty</th><th class="num">Remains</th><th class="num">Charge</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody id="rows"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody></table></div></div>`;
  const load = async () => {
    const qs = new URLSearchParams({ limit: "100", ...(status && { status }), ...(q && { q }) });
    let rows;
    try { rows = await call(`/admin/api/orders?${qs}`); } catch (e) { $("rows").innerHTML = `<tr><td colspan="8" class="empty">${esc(e.message)}</td></tr>`; return; }
    $("rows").innerHTML = rows.length ? rows.map((o) => `<tr>
      <td data-col="id" class="mono">#${o.id}</td>
      <td data-col="date" style="font-size:13px;color:var(--muted);white-space:nowrap">${fmtDate(o.created_at)}</td>
      <td data-col="svc"><div class="svc">${esc(o.service_name)} ${tierBadge(o.tier)}</div>
        <div class="link">${esc(o.email)} · SMMGen #${o.provider_order_id ?? "—"}</div>
        ${/^https?:\/\//i.test(o.link || "") ? `<a class="btn open-link" href="${esc(o.link)}" target="_blank" rel="noopener noreferrer" title="${esc(o.link)}">${icons.external(14)}Open link</a>` : ""}</td>
      <td data-col="qty" class="num">${num(o.quantity)}</td>
      <td data-col="remains" class="num">${o.remains == null ? "–" : num(o.remains)}</td>
      <td data-col="charge" class="num">${peso(o.price_php)}</td>
      <td data-col="status">${badge(ORDER_STATUS, o.status)}</td>
      <td data-col="refill">${o.status === "needs_review"
        ? `<button type="button" class="btn btn-sm order-cancel" data-refund="${o.id}">Refund ${peso(o.price_php)}</button>`
        : o.refill_status === "pending" ? `<span class="muted">Refill in progress</span>`
        : Number(o.refunded_php) > 0 ? `<span class="muted">${peso(o.refunded_php)} refunded</span>`
          : o.cancel_requested_at ? `<span class="muted">Cancel requested</span>` : `<span class="muted refill-none">–</span>`}</td>
    </tr>`).join("") : `<tr><td colspan="8" class="empty">No orders.</td></tr>`;
    $("rows").querySelectorAll("[data-refund]").forEach((b) => b.addEventListener("click", async () => {
      if (!b.classList.contains("armed")) { b.classList.add("armed"); b.textContent = "Tap again: not placed on SMMGen?"; return; }
      b.disabled = true;
      try { await call(`/admin/api/orders/${b.dataset.refund}/refund`, { method: "POST" }); toast("Refunded to the customer's balance"); }
      catch (e) { toast(e.message, { bad: true }); }
      load();
    }));
  };
  view.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => {
    status = b.dataset.f;
    view.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  $("q").addEventListener("input", debounce((e) => { q = e.target.value.trim(); load(); }));
  load();
}

/* ------------------------------------------------------------ customers */

function renderCustomers() {
  let q = "";
  view.innerHTML = `<div class="page-head"><h1>Customers</h1></div>
    <input class="input" id="q" type="search" placeholder="Search by email or ID" style="max-width:360px">
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>ID</th><th>Email</th><th>Joined</th><th class="num">Balance</th><th class="num">Orders</th><th class="num">Topped up</th><th>Tier</th><th></th></tr></thead>
      <tbody id="rows"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Balance changes are recorded in the ledger with your note, like every other movement of money.</p>`;
  const load = async () => {
    let rows;
    try { rows = await call(`/admin/api/users?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`); }
    catch (e) { $("rows").innerHTML = `<tr><td colspan="8" class="empty">${esc(e.message)}</td></tr>`; return; }
    $("rows").innerHTML = rows.length ? rows.map((u) => `<tr>
      <td class="mono muted">${u.id}</td><td style="font-weight:600">${esc(u.email)}</td>
      <td class="muted" style="white-space:nowrap">${fmtDate(u.created_at)}</td>
      <td class="num"><strong>${peso(u.balance_php)}</strong></td><td class="num">${num(u.orders)}</td>
      <td class="num">${peso(u.topped_up_php)}</td>
      <td style="white-space:nowrap" title="Website spending after refunds: ${peso(u.spent_php)}">${badgeSVG(u.tier, 18)} ${esc({ member: "Member", pro: "Pro", elite: "Elite" }[u.tier] || u.tier)}
        <div class="muted" style="font-size:12px">${peso(u.spent_php)} spent</div></td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-adjust="${u.id}" data-email="${esc(u.email)}">Adjust balance</button></td>
    </tr>
    <tr class="hidden" id="adj-${u.id}"><td colspan="8">
      <form class="adjust-form" data-user="${u.id}">
        <input class="input" name="amount" type="number" step="0.01" placeholder="Amount, e.g. 50 or -50" required>
        <input class="input" name="note" type="text" maxlength="200" placeholder="Note (why), e.g. goodwill credit for delayed order #12" required>
        <button class="btn btn-primary btn-sm" type="submit">Apply</button>
      </form></td></tr>`).join("") : `<tr><td colspan="8" class="empty">No customers.</td></tr>`;
    $("rows").querySelectorAll("[data-adjust]").forEach((b) => b.addEventListener("click", () => {
      $(`adj-${b.dataset.adjust}`).classList.toggle("hidden");
    }));
    $("rows").querySelectorAll(".adjust-form").forEach((f) => f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const amount = Number(f.amount.value), note = f.note.value.trim();
      if (!amount || note.length < 3) { toast("Enter an amount and a short note", { bad: true }); return; }
      const email = f.closest("tr").previousElementSibling.querySelector("[data-email]").dataset.email;
      const btn = f.querySelector("button");
      const ask = `Tap again: ${amount > 0 ? "add" : "remove"} ${peso(Math.abs(amount))} ${amount > 0 ? "to" : "from"} ${email}`;
      if (btn.textContent !== ask) { btn.textContent = ask; btn.classList.add("armed"); return; }   // second tap confirms
      btn.disabled = true;
      try {
        const r = await call(`/admin/api/users/${f.dataset.user}/adjust`, { method: "POST", body: { amount_php: amount, note } });
        toast(`Done. New balance ${peso(r.balance_php)}`);
        load();
      } catch (e) { toast(e.message, { bad: true }); }
    }));
  };
  $("q").addEventListener("input", debounce((e) => { q = e.target.value.trim(); load(); }));
  load();
}

/* ------------------------------------------------------------ top-ups */

function renderTopups() {
  let status = "";
  const F = [["", "All"], ["credited", "Credited"], ["pending", "Awaiting payment"], ["canceled", "Canceled"], ["expired", "Expired"]];
  view.innerHTML = `<div class="page-head"><h1>Top-ups</h1></div>
    <div class="pill-row" role="group" aria-label="Filter">${F.map(([v, l]) => pill(v, l, status, "data-f")).join("")}</div>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>Date</th><th>Customer</th><th class="num">Amount</th><th>Method</th><th>Status</th><th>Credited</th></tr></thead>
      <tbody id="rows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody></table></div></div>`;
  const METHOD = { gcash: "GCash", paymaya: "Maya", paymongo: "PayMongo", qrph: "QR Ph", card: "Card", grab_pay: "GrabPay" };
  const load = async () => {
    let rows;
    try { rows = await call(`/admin/api/topups?limit=100${status ? `&status=${status}` : ""}`); }
    catch (e) { $("rows").innerHTML = `<tr><td colspan="6" class="empty">${esc(e.message)}</td></tr>`; return; }
    $("rows").innerHTML = rows.length ? rows.map((t) => `<tr>
      <td class="muted" style="white-space:nowrap">${fmtDate(t.created_at)}</td><td>${esc(t.email)}</td>
      <td class="num"><strong>${peso(t.amount_php)}</strong></td><td>${esc(METHOD[t.method] || t.method)}</td>
      <td>${badge(TOPUP_STATUS, t.status)}</td><td class="muted" style="white-space:nowrap">${t.credited_at ? fmtDate(t.credited_at) : "–"}</td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">No top-ups.</td></tr>`;
  };
  view.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => {
    status = b.dataset.f;
    view.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  load();
}

/* ------------------------------------------------------------ services */

function renderServices() {
  let q = "", platform = "", hidden = false;
  view.innerHTML = `<div class="page-head"><h1>Services</h1></div>
    <div class="orders-toolbar">
      <div class="pill-row" role="group" aria-label="Show">${pill("0", "Visible", "0", "data-h")}${pill("1", "Hidden", "0", "data-h")}</div>
      <input class="input" id="q" type="search" placeholder="Search name, category or ID">
    </div>
    <div class="pill-row" role="group" aria-label="Platform">${pill("", "All platforms", "", "data-p")}${PLATFORM_ORDER.map((p) => pill(p, PLATFORMS[p] || p, "", "data-p")).join("")}</div>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>ID</th><th>Service</th><th>SMMGen</th><th class="num">Rate /1K</th><th class="num">Orders</th><th></th></tr></thead>
      <tbody id="rows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Hidden services disappear from the site and can't be ordered. Existing orders aren't affected. Showing the 50 most-ordered matches; search to find others.</p>`;
  const load = async () => {
    const qs = new URLSearchParams({ hidden: String(hidden), ...(q && { q }), ...(platform && { platform }) });
    let rows;
    try { rows = await call(`/admin/api/services?${qs}`); } catch (e) { $("rows").innerHTML = `<tr><td colspan="6" class="empty">${esc(e.message)}</td></tr>`; return; }
    $("rows").innerHTML = rows.length ? rows.map((s) => `<tr>
      <td class="mono muted">${s.id}</td>
      <td><div style="font-weight:600">${esc(s.name)} ${tierBadge(s.tier)}</div>
        <div class="hint">${esc(PLATFORMS[s.platform] || s.platform)} · ${esc(s.category || "Other")}${s.auto ? "" : " · Recommended"}</div></td>
      <td><div class="hint" style="max-width:340px">#${s.provider_service_id} · ${esc(s.provider_name)}</div></td>
      <td class="num">$${Number(s.rate).toFixed(4)}</td><td class="num">${num(s.orders)}</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-toggle="${s.id}">${s.hidden ? "Show" : "Hide"}</button></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">No services match.</td></tr>`;
    $("rows").querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try { await call(`/admin/api/services/${b.dataset.toggle}/hidden`, { method: "POST", body: { hidden: !hidden } }); toast(hidden ? "Service is visible again" : "Service hidden"); }
      catch (e) { toast(e.message, { bad: true }); }
      load();
    }));
  };
  view.querySelectorAll("[data-h]").forEach((b) => b.addEventListener("click", () => {
    hidden = b.dataset.h === "1";
    view.querySelectorAll("[data-h]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  view.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => {
    platform = b.dataset.p;
    view.querySelectorAll("[data-p]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  $("q").addEventListener("input", debounce((e) => { q = e.target.value.trim(); load(); }));
  load();
}

/* ------------------------------------------------------------ more (phones: the tabs that don't fit the bar) */

const MORE = [
  ["services", "grid", "Services", "Hide or show services"],
  ["growth", "monitor", "Growth", "Sign-ups, orders and payments by day"],
  ["affiliates", "gift", "Affiliates", "Customers who brought in sign-ups"],
  ["trials", "done", "Free trials", "Who claimed a trial, and from where"],
  ["ledger", "check", "Ledger", "Every movement of customer money"],
  ["errors", "warn", "Error reports", "Problems customers' browsers reported"],
];

function renderMore() {
  view.innerHTML = `<div class="page-head"><h1>More</h1></div>
    <div class="more-list">${MORE.map(([n, icon, t, sub]) => `
      <a class="card more-item" href="#${n}"><span class="more-icon">${icons[icon](20)}</span>
        <span class="grow"><span class="t">${t}</span><span class="s">${sub}</span></span></a>`).join("")}</div>`;
}

const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : "–");
const dayLabel = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-PH", { weekday: "short", month: "short", day: "numeric" });
const failRow = (cols, e) => `<tr><td colspan="${cols}" class="empty">${esc(e.message)}</td></tr>`;

/* ------------------------------------------------------------ growth */

async function renderGrowth() {
  let days = 14;
  view.innerHTML = `<div class="page-head"><h1>Growth</h1></div>
    <div id="funnel"><div class="card empty">Loading…</div></div>
    <div class="pill-row" role="group" aria-label="Period" style="margin-top:18px">${[7, 14, 30].map((d) => pill(String(d), `${d} days`, String(days), "data-d")).join("")}</div>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>Day</th><th class="num">Sign-ups</th><th class="num">…ordered</th><th class="num">…paid</th>
        <th class="num">Top-ups</th><th class="num">Topped up</th><th class="num">Orders</th><th class="num">Buyers</th></tr></thead>
      <tbody id="rows"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Philippine time. "…ordered" and "…paid" count that day's sign-ups who have since placed an order or had a top-up credited (so recent days can still go up). Top-ups, orders and buyers are what happened on that day.</p>`;
  const load = async () => {
    let d;
    try { d = await call(`/admin/api/growth?days=${days}`); } catch (e) { $("rows").innerHTML = failRow(8, e); return; }
    const f = d.funnel;
    const step = (k, v, s) => `<div class="stat"><span class="k">${k}</span><span class="v">${num(v)}</span><span class="s">${s}</span></div>`;
    $("funnel").innerHTML = `<div class="stats">
      ${step("Signed up", f.signed_up, "All time")}
      ${step("Placed an order", f.ordered, `${pct(f.ordered, f.signed_up)} of sign-ups · ${num(f.used_trial)} used the free trial`)}
      ${step("Started a top-up", f.tried_topup, `${pct(f.tried_topup, f.signed_up)} of sign-ups`)}
      ${step("Paid", f.paid, `${pct(f.paid, f.signed_up)} of sign-ups · ${pct(f.paid, f.tried_topup)} of those who started`)}
      ${step("Paid again", f.paid_twice, `${pct(f.paid_twice, f.paid)} of paying customers`)}
    </div>`;
    $("rows").innerHTML = d.days.map((r) => `<tr>
      <td style="white-space:nowrap">${dayLabel(r.day)}</td>
      <td class="num"><strong>${num(r.signups)}</strong></td>
      <td class="num">${num(r.signups_ordered)} <span class="muted">${r.signups ? pct(r.signups_ordered, r.signups) : ""}</span></td>
      <td class="num">${num(r.signups_paid)} <span class="muted">${r.signups ? pct(r.signups_paid, r.signups) : ""}</span></td>
      <td class="num">${num(r.topups)}</td><td class="num">${peso(r.topups_php)}</td>
      <td class="num">${num(r.orders)}</td><td class="num">${num(r.buyers)}</td></tr>`).join("");
  };
  view.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", () => {
    days = Number(b.dataset.d);
    view.querySelectorAll("[data-d]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  load();
}

/* ------------------------------------------------------------ affiliates */

async function renderAffiliates() {
  view.innerHTML = `<div class="page-head"><h1>Affiliates</h1></div>
    <div id="aff-stats"></div>
    <div class="card table-card" style="margin-top:18px"><div class="table-scroll"><table class="table">
      <thead><tr><th>Affiliate</th><th class="num">Signed up</th><th class="num">Paying</th><th class="num">Their top-ups</th><th class="num">Commission earned</th><th>Last sign-up</th></tr></thead>
      <tbody id="rows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Customers who brought in at least one sign-up with their referral link. Commission is paid to their balance on every top-up their referrals make.</p>`;
  let d;
  try { d = await call("/admin/api/affiliates"); } catch (e) { $("rows").innerHTML = failRow(6, e); return; }
  const a = d.affiliates;
  const tot = (k) => a.reduce((t, x) => t + x[k], 0);
  $("aff-stats").innerHTML = `<div class="stats">
    <div class="stat"><span class="k">Affiliates</span><span class="v">${num(a.length)}</span><span class="s">${num(a.filter((x) => x.paying).length)} brought a paying customer</span></div>
    <div class="stat"><span class="k">Referred sign-ups</span><span class="v">${num(tot("referred"))}</span><span class="s">${num(tot("paying"))} of them paid</span></div>
    <div class="stat"><span class="k">Referred top-ups</span><span class="v">${peso(tot("referred_topups_php"))}</span><span class="s">All time</span></div>
    <div class="stat"><span class="k">Commission paid</span><span class="v">${peso(tot("earned_php"))}</span><span class="s">Base rate ${d.referral_pct}% (higher for Pro and Elite)</span></div>
  </div>`;
  $("rows").innerHTML = a.length ? a.map((x) => `<tr>
      <td><div style="font-weight:600">${esc(x.email)}</div><div class="hint" style="margin:2px 0 6px">#${x.id} · code ${esc(x.ref_code || "–")} · balance ${peso(x.balance_php)}</div>
        <button type="button" class="btn btn-ghost btn-sm" data-people="${x.id}">Show sign-ups</button></td>
      <td class="num"><strong>${num(x.referred)}</strong></td><td class="num">${num(x.paying)}</td>
      <td class="num">${peso(x.referred_topups_php)}</td><td class="num"><strong>${peso(x.earned_php)}</strong></td>
      <td class="muted" style="white-space:nowrap">${fmtDate(x.last_referral_at)}</td></tr>
    <tr class="hidden" id="people-${x.id}"><td colspan="6" style="white-space:normal">
      <div class="hint" style="margin:0 0 6px;font-weight:600">Signed up with ${esc(x.email)}'s link</div>
      ${x.people.map((p) => `<div style="padding:6px 0;border-top:1px solid var(--line)">
        <strong>${esc(p.email)}</strong> <span class="muted">#${p.id} · joined ${fmtDate(p.created_at)}</span>
        <div class="hint" style="margin:2px 0 0">${num(p.orders)} order${p.orders === 1 ? "" : "s"} · topped up ${peso(p.topped_up_php)} · earned them ${peso(p.commission_php)}</div>
      </div>`).join("")}
    </td></tr>`).join("") : `<tr><td colspan="6" class="empty">No one has brought in a sign-up with their link yet.</td></tr>`;
  $("rows").querySelectorAll("[data-people]").forEach((b) => b.addEventListener("click", () => {
    const row = $(`people-${b.dataset.people}`);
    row.classList.toggle("hidden");
    b.textContent = row.classList.contains("hidden") ? "Show sign-ups" : "Hide sign-ups";
  }));
}

/* ------------------------------------------------------------ free trials */

async function renderTrials() {
  view.innerHTML = `<div class="page-head"><h1>Free trials</h1></div>
    <p class="hint" id="trial-sum" style="margin-top:-6px"></p>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>Date</th><th>Customer</th><th class="num">Qty</th><th>Status</th><th>Network</th><th>Paid later</th></tr></thead>
      <tbody id="rows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">One trial per account, link and device, and one per network every 30 days. "Shares network" means other trial accounts came from the same IP address: often one person with several accounts, sometimes a shared connection like mobile data.</p>`;
  let rows;
  try { rows = await call("/admin/api/trials"); } catch (e) { $("rows").innerHTML = failRow(6, e); return; }
  const paid = rows.filter((r) => r.paid_after).length;
  if (rows.length) $("trial-sum").textContent = `${num(rows.length)} trial${rows.length === 1 ? "" : "s"} · ${num(paid)} of those customers topped up afterwards (${pct(paid, rows.length)}).`;
  $("rows").innerHTML = rows.length ? rows.map((r) => `<tr>
    <td class="muted" style="white-space:nowrap">${fmtDate(r.created_at)}</td>
    <td><div style="font-weight:600">${esc(r.email)} <span class="muted">#${r.user_id}</span></div>
      ${/^https?:\/\//i.test(r.link || "") ? `<a class="btn open-link" href="${esc(r.link)}" target="_blank" rel="noopener noreferrer" title="${esc(r.link)}">${icons.external(14)}Open link</a>` : ""}</td>
    <td class="num">${num(r.quantity)}</td><td>${badge(ORDER_STATUS, r.status)}</td>
    <td><span class="mono" style="font-size:13px">${esc(r.trial_ip || "–")}</span>
      ${r.shared_ip ? `<div><span class="badge badge-failed">Shares network with ${r.shared_ip} other${r.shared_ip === 1 ? "" : "s"}</span></div>` : ""}</td>
    <td>${r.paid_after ? `<span class="badge badge-completed">Paid</span>` : `<span class="muted">No</span>`}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">No free trials claimed yet.</td></tr>`;
}

/* ------------------------------------------------------------ ledger */

const REASON = { topup: "Top-up", order: "Order", refund: "Refund", adjustment: "Adjustment", referral: "Referral commission",
  welcome: "Welcome credit", tier_bonus: "Tier bonus" };

function renderLedger() {
  let reason = "", q = "";
  view.innerHTML = `<div class="page-head"><h1>Ledger</h1></div>
    <div class="orders-toolbar">
      <div class="pill-row" role="group" aria-label="Type">${pill("", "All", "", "data-r")}${Object.entries(REASON).map(([v, l]) => pill(v, l, "", "data-r")).join("")}</div>
      <input class="input" id="q" type="search" placeholder="Email, customer # or reference">
    </div>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>Date</th><th>Customer</th><th>Type</th><th>Reference</th><th class="num">Amount</th></tr></thead>
      <tbody id="rows"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Every change to a customer's balance; a balance is the sum of its rows. Showing the latest 100 matches.</p>`;
  const load = async () => {
    const qs = new URLSearchParams({ ...(reason && { reason }), ...(q && { q }) });
    let rows;
    try { rows = await call(`/admin/api/ledger?${qs}`); } catch (e) { $("rows").innerHTML = failRow(5, e); return; }
    const refText = (r) => (r.reason === "order" || r.reason === "refund") ? `Order #${esc(r.ref)}`
      : r.reason === "adjustment" ? esc(r.ref || "") : `<span class="mono muted" style="font-size:12px">${esc((r.ref || "").slice(0, 13))}</span>`;
    $("rows").innerHTML = rows.length ? rows.map((r) => `<tr>
      <td class="muted" style="white-space:nowrap">${fmtDate(r.created_at)}</td>
      <td>${esc(r.email)} <span class="muted">#${r.user_id}</span></td>
      <td>${esc(REASON[r.reason] || r.reason)}</td><td style="max-width:320px">${refText(r)}</td>
      <td class="num"><strong>${r.delta > 0 ? "+" : "−"}${peso(Math.abs(r.delta))}</strong></td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">Nothing matches.</td></tr>`;
  };
  view.querySelectorAll("[data-r]").forEach((b) => b.addEventListener("click", () => {
    reason = b.dataset.r;
    view.querySelectorAll("[data-r]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  $("q").addEventListener("input", debounce((e) => { q = e.target.value.trim(); load(); }));
  load();
}

/* ------------------------------------------------------------ error reports */

async function renderErrors() {
  view.innerHTML = `<div class="page-head"><h1>Error reports</h1></div>
    <div id="errs"><div class="card empty">Loading…</div></div>
    <p class="hint">Sent automatically when something breaks in a customer's browser, and by the ?debug=1 panel. The latest 100.</p>`;
  let rows;
  try { rows = await call("/admin/api/errors"); } catch (e) { $("errs").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  const device = (ua = "") => /iPad/.test(ua) ? "iPad" : /iPhone/.test(ua) ? "iPhone" : /Android/.test(ua) ? "Android"
    : /Macintosh/.test(ua) ? "Mac or iPad" : /Windows/.test(ua) ? "Windows" : "Other";
  const browser = (ua = "") => /FBAN|FBAV/.test(ua) ? "Facebook app" : /CriOS|Chrome/.test(ua) ? "Chrome"
    : /Firefox|FxiOS/.test(ua) ? "Firefox" : /Safari/.test(ua) ? "Safari" : "";
  $("errs").innerHTML = rows.length ? `<div class="more-list">${rows.map((r) => `
    <div class="card" style="padding:14px 16px">
      <div class="hint" style="margin:0 0 6px">${fmtDate(r.created_at)} · ${esc(r.page || "–")} · ${esc(device(r.user_agent))} ${esc(browser(r.user_agent))}</div>
      <div class="mono" style="font-size:13px;white-space:pre-wrap;word-break:break-word">${esc((r.message || "").slice(0, 1200))}</div>
    </div>`).join("")}</div>` : `<div class="card empty">No errors reported.</div>`;
}

start();
