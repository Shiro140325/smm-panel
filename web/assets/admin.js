import { api, esc, fmtDate, initTheme, num, peso, PLATFORMS, PLATFORM_ORDER, tierBadge, toast } from "./common.js";
import { icons } from "./icons.js";

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

/* any 401 mid-session (expired) → back to sign in */
async function call(path, opts) {
  try { return await api(path, opts); } catch (e) {
    if (e.status === 401) showLogin("Your session ended. Sign in again.");
    throw e;
  }
}

function render() {
  const name = route();
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  ({ overview: renderOverview, orders: renderOrders, customers: renderCustomers, topups: renderTopups, services: renderServices }[name]
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
    <p class="hint">Cost is what SMMGen reports charging per order, converted at ₱${Number(d.usd_to_php).toFixed(2)}/USD. "Today" is Philippine time.</p>`;
}

/* ------------------------------------------------------------ orders */

const ORDER_FILTERS = [["", "All"], ["needs_review", "Under review"], ["pending", "Pending"], ["in_progress", "In progress"],
  ["completed", "Completed"], ["partial", "Partial"], ["canceled", "Canceled"], ["failed", "Failed"]];

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
      <thead><tr><th>ID</th><th>Email</th><th>Joined</th><th class="num">Balance</th><th class="num">Orders</th><th class="num">Topped up</th><th></th></tr></thead>
      <tbody id="rows"><tr><td colspan="7" class="empty">Loading…</td></tr></tbody></table></div></div>
    <p class="hint">Balance changes are recorded in the ledger with your note, like every other movement of money.</p>`;
  const load = async () => {
    let rows;
    try { rows = await call(`/admin/api/users?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`); }
    catch (e) { $("rows").innerHTML = `<tr><td colspan="7" class="empty">${esc(e.message)}</td></tr>`; return; }
    $("rows").innerHTML = rows.length ? rows.map((u) => `<tr>
      <td class="mono muted">${u.id}</td><td style="font-weight:600">${esc(u.email)}</td>
      <td class="muted" style="white-space:nowrap">${fmtDate(u.created_at)}</td>
      <td class="num"><strong>${peso(u.balance_php)}</strong></td><td class="num">${num(u.orders)}</td>
      <td class="num">${peso(u.topped_up_php)}</td>
      <td><button type="button" class="btn btn-ghost btn-sm" data-adjust="${u.id}" data-email="${esc(u.email)}">Adjust balance</button></td>
    </tr>
    <tr class="hidden" id="adj-${u.id}"><td colspan="7">
      <form class="adjust-form" data-user="${u.id}">
        <input class="input" name="amount" type="number" step="0.01" placeholder="Amount, e.g. 50 or -50" required>
        <input class="input" name="note" type="text" maxlength="200" placeholder="Note (why), e.g. goodwill credit for delayed order #12" required>
        <button class="btn btn-primary btn-sm" type="submit">Apply</button>
      </form></td></tr>`).join("") : `<tr><td colspan="7" class="empty">No customers.</td></tr>`;
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

start();
