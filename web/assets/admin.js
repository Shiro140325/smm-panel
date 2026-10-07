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
  queued: ["Queued", "badge-review"], creating: ["Processing", "badge-pending"], pending: ["Pending", "badge-pending"],
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
    const me = await api("/admin/api/me");
    if (me.setup_required) showSetup(); else showPanel();
  } catch (e) {
    showLogin(e.status === 503 ? e.message : "");
  }
}

function showOnly(id) {
  for (const x of ["login", "setup", "panel"]) $(x).classList.toggle("hidden", x !== id);
  $("login-theme").classList.toggle("hidden", id === "panel");
}

function showLogin(notice) {
  showOnly("login");
  let step = "password";
  $("code-field").classList.add("hidden");
  $("admin-pass").closest(".field").classList.remove("hidden");
  $("login-btn").textContent = "Open control panel";
  const err = $("login-error");
  err.classList.add("hidden");
  if (notice) { err.textContent = notice; err.classList.remove("hidden"); }
  $("admin-pass").focus();
  $("login-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const btn = $("login-btn");
    btn.disabled = true;
    err.classList.add("hidden");
    try {
      if (step === "password") {
        const r = await api("/admin/api/login", { method: "POST", body: { password: $("admin-pass").value } });
        $("admin-pass").value = "";
        if (r.totp_required) {   // step 2: the authenticator code
          step = "code";
          $("admin-pass").closest(".field").classList.add("hidden");
          $("code-field").classList.remove("hidden");
          btn.textContent = "Verify";
          $("admin-code").value = "";
          $("admin-code").focus();
        } else if (r.setup_required) showSetup();
        else showPanel();
      } else {
        await api("/admin/api/login/totp", { method: "POST", body: { code: $("admin-code").value.trim() } });
        $("admin-code").value = "";
        showPanel();
      }
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hidden");
      if (step === "code" && e.status === 401 && /timed out/.test(e.message)) setTimeout(() => showLogin(e.message), 1200);
      else if (step === "code") $("admin-code").select();
    }
    btn.disabled = false;
  };
}

async function showSetup() {
  showOnly("setup");
  $("setup-step1").classList.remove("hidden");
  $("setup-step2").classList.add("hidden");
  const err = $("setup-error");
  err.classList.add("hidden");
  let d;
  try { d = await api("/admin/api/totp/setup"); } catch (e) {
    if (e.status === 401) return showLogin(e.message);
    err.textContent = e.message; err.classList.remove("hidden"); return;
  }
  $("setup-qr").innerHTML = d.qr_svg;
  $("setup-open").href = d.uri;
  $("setup-key").textContent = d.secret;
  $("setup-copy").onclick = async () => { try { await navigator.clipboard.writeText(d.secret.replace(/ /g, "")); toast("Key copied"); } catch { toast(d.secret); } };
  $("setup-form").onsubmit = async (ev) => {
    ev.preventDefault();
    err.classList.add("hidden");
    $("setup-go").disabled = true;
    try {
      const r = await api("/admin/api/totp/setup", { method: "POST", body: { code: $("setup-code").value.trim() } });
      $("setup-step1").classList.add("hidden");
      $("setup-step2").classList.remove("hidden");
      $("setup-backups").innerHTML = r.backup_codes.map((c) => `<code>${esc(c)}</code>`).join("");
      $("setup-copy-backups").onclick = async () => {
        const text = "SMM Shiro control panel backup codes (each works once):\n" + r.backup_codes.join("\n");
        try { await navigator.clipboard.writeText(text); toast("Backup codes copied"); } catch { toast("Copy them by hand"); }
      };
      $("setup-saved").checked = false;
      $("setup-done").disabled = true;
      $("setup-saved").onchange = () => { $("setup-done").disabled = !$("setup-saved").checked; };
      $("setup-done").onclick = () => showPanel();
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); $("setup-code").select(); }
    $("setup-go").disabled = false;
  };
  $("setup-code").focus();
}

function showPanel() {
  showOnly("panel");
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
    if (e.status === 403 && /authenticator/.test(e.message)) showSetup();
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
     cancels: renderCancels, bot: renderBot, growth: renderGrowth, affiliates: renderAffiliates, trials: renderTrials, ledger: renderLedger, errors: renderErrors,
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
    <div class="card panel sending-card" id="sending"><div class="empty" style="padding:0">Loading…</div></div>
    ${d.needs_review ? `<a class="alert alert-bad" href="#orders?status=needs_review" style="text-decoration:none;margin-bottom:16px">
      ${icons.warn(20)}<span><strong>${num(d.needs_review)} order${d.needs_review === 1 ? "" : "s"} under review.</strong> Check them against SMMGen and refund any that weren't placed.</span></a>` : ""}
    ${d.cancels_pending ? `<a class="alert alert-warn" href="#cancels" style="text-decoration:none;margin-bottom:16px">
      ${icons.warn(20)}<span><strong>${num(d.cancels_pending)} cancel request${d.cancels_pending === 1 ? "" : "s"} to take to SMMGen support.</strong> Open Cancellations.</span></a>` : ""}
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
  setupSending();
  paintCancelCount(d.cancels_pending);
}

/* ------------------------------------------------------------ cancellations */

function paintCancelCount(n) {
  const el = $("cancel-count");
  if (!el) return;
  el.textContent = n > 99 ? "99+" : String(n || "");
  el.classList.toggle("hidden", !n);
}

function renderCancels() {
  let view_ = "open";
  view.innerHTML = `<div class="page-head"><h1>Cancellations</h1></div>
    <p class="hint" style="margin-top:-6px">Customers asked to cancel these, but SMMGen has no cancel button for them (or refused it).
      Ask SMMGen support to cancel them. When SMMGen marks an order canceled or partial, the customer is refunded automatically and it leaves this list.</p>
    <div class="orders-toolbar">
      <div class="pill-row" role="group" aria-label="Show">${pill("open", "To do", "open", "data-cv")}${pill("done", "Done", "open", "data-cv")}</div>
      <button type="button" class="btn btn-secondary" id="copy-req">Copy request for SMMGen support</button>
    </div>
    <div class="card table-card"><div class="table-scroll"><table class="table">
      <thead><tr><th>Requested</th><th>Order</th><th>Customer and service</th><th>Status</th><th id="cv-last">Actions</th></tr></thead>
      <tbody id="rows"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody></table></div></div>`;
  let rows = [];
  const load = async () => {
    try { rows = await call(`/admin/api/cancellations?view=${view_}`); } catch (e) { $("rows").innerHTML = failRow(5, e); return; }
    if (view_ === "open") paintCancelCount(rows.length);
    $("copy-req").classList.toggle("hidden", view_ !== "open" || !rows.some((r) => r.provider_order_id && !r.cancel_contacted_at));
    $("cv-last").textContent = view_ === "open" ? "Actions" : "Outcome";
    $("rows").innerHTML = rows.length ? rows.map((r) => `<tr>
      <td class="muted" style="white-space:nowrap">${fmtDate(r.cancel_requested_at)}</td>
      <td class="mono" style="white-space:nowrap">#${r.id}<div class="hint" style="margin:2px 0 0">SMMGen #${r.provider_order_id ?? "–"}</div></td>
      <td><div class="svc">${esc(r.service_name)} ${tierBadge(r.tier)}</div><div class="link">${esc(r.email)}</div>
        <div class="hint" style="margin:2px 0 4px">${num(r.quantity)} ordered · ${r.remains == null ? "–" : num(r.remains)} left · ${peso(r.price_php)}</div>
        ${/^https?:\/\//i.test(r.link || "") ? `<a class="btn open-link" href="${esc(r.link)}" target="_blank" rel="noopener noreferrer">${icons.external(14)}Open link</a>` : ""}</td>
      <td>${badge(ORDER_STATUS, r.status)}</td>
      <td>${view_ === "open" ? `<div class="cv-actions">
          ${r.cancel_contacted_at
            ? `<span class="muted">Asked ${fmtDate(r.cancel_contacted_at)}</span><button type="button" class="btn btn-ghost btn-sm" data-act="uncontacted" data-id="${r.id}">Undo</button>`
            : `<button type="button" class="btn btn-secondary btn-sm" data-act="contacted" data-id="${r.id}">I asked SMMGen</button>`}
          <button type="button" class="btn btn-ghost btn-sm" data-act="declined" data-id="${r.id}">Couldn't cancel</button></div>`
        : r.cancel_declined_at ? `<span class="muted">Couldn't cancel (${fmtDate(r.cancel_declined_at)})</span>`
        : Number(r.refunded_php) > 0 ? `<strong>${peso(r.refunded_php)} refunded</strong>`
        : r.status === "completed" ? `<span class="muted">Finished before the cancel</span>` : `<span class="muted">–</span>`}</td></tr>`).join("")
      : `<tr><td colspan="5" class="empty">${view_ === "open" ? "No cancel requests waiting." : "Nothing here yet."}</td></tr>`;
    $("rows").querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.act === "declined" && !b.classList.contains("armed")) {   // second tap confirms
        b.classList.add("armed"); b.textContent = "Tap again: tell the customer it can't be canceled"; return;
      }
      b.disabled = true;
      try {
        await call(`/admin/api/cancellations/${b.dataset.id}`, { method: "POST", body: { action: b.dataset.act } });
        if (b.dataset.act === "declined") toast("Marked as couldn't cancel. The order keeps running.");
      } catch (e) { toast(e.message, { bad: true }); }
      load();
    }));
  };
  $("copy-req").onclick = async () => {
    const ids = rows.filter((r) => r.provider_order_id && !r.cancel_contacted_at).map((r) => r.provider_order_id);
    const text = `Hello, please cancel ${ids.length === 1 ? "this order" : "these orders"}: ${ids.join(", ")}. Thank you.`;
    try { await navigator.clipboard.writeText(text); toast(`Copied: ${text}`); } catch { toast(text); }
  };
  view.querySelectorAll("[data-cv]").forEach((b) => b.addEventListener("click", () => {
    view_ = b.dataset.cv;
    view.querySelectorAll("[data-cv]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    load();
  }));
  load();
}

/* ------------------------------------------------------------ Messenger bot */

let botSession = null;
function botSessionId() {
  if (!botSession) {
    try { botSession = sessionStorage.getItem("botSession"); } catch { /* private mode */ }
    if (!botSession) {
      botSession = "t" + Math.random().toString(36).slice(2, 12);
      try { sessionStorage.setItem("botSession", botSession); } catch { /* private mode */ }
    }
  }
  return botSession;
}

async function renderBot() {
  view.innerHTML = `<div class="page-head"><h1>Messenger bot</h1></div><div id="bot"><div class="card empty">Loading…</div></div>`;
  let d;
  try { d = await call("/admin/api/bot"); } catch (e) { $("bot").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  const s = d.settings, su = d.setup, st = d.stats, rate = d.usd_to_php;
  const php = (usd) => `₱${(usd * rate).toFixed(usd * rate < 1 ? 3 : 2)}`;
  const ready = su.openrouter && su.meta_token && su.meta_secret && su.verify_token;
  const tick = (ok, label, hint) => `<li class="${ok ? "ok" : ""}">${ok ? icons.check(16) : "○"} <span>${label}${ok ? "" : ` <span class="muted">· ${hint}</span>`}</span></li>`;
  $("bot").innerHTML = `
    <section class="card panel bot-card" style="margin-bottom:18px">
      <div class="bot-row"><h3>Bot menu</h3><button type="button" class="btn btn-secondary btn-sm" id="menu-add">Add item</button></div>
      <p class="hint" style="margin:0">The only things the bot sells, at your price, sent straight to the SMMGen service you pick. Nothing from the website's catalog, tiers or discounts.</p>
      <form class="menu-form hidden" id="menu-form" novalidate>
        <div class="menu-fields">
          <div class="field"><label for="mf-name">Name customers see</label><input class="input" id="mf-name" maxlength="60" placeholder="TikTok Followers"></div>
          <div class="field"><label for="mf-price">Price (₱)</label><input class="input" id="mf-price" type="number" step="0.01" min="0.01" placeholder="50"></div>
          <div class="field"><label for="mf-per">Per how many</label><input class="input" id="mf-per" type="number" min="1" value="1000"></div>
          <div class="field"><label for="mf-sid">SMMGen service ID</label><input class="input" id="mf-sid" type="number" min="1" placeholder="e.g. 4521"></div>
        </div>
        <div class="hint" id="mf-info" style="margin:0"></div>
        <div class="bot-row"><label class="check" style="margin:0"><input type="checkbox" id="mf-active" checked> <span>On the menu</span></label>
          <span><button type="button" class="btn btn-ghost btn-sm" id="mf-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary btn-sm" id="mf-save">Save item</button></span></div>
      </form>
      <div id="menu-list"><p class="hint" style="margin:0">Loading…</p></div>
    </section>
    <div class="bot-grid">
      <div class="bot-col">
        <section class="card panel bot-card">
          <h3>Test chat</h3>
          <p class="hint" style="margin:0">Chat as a customer to see exactly what the bot replies. Test mode: no payment link, no order.</p>
          <div class="bot-log" id="bot-log"><div class="muted bot-empty">Add menu items above, then say something like “pa 1k followers sa tiktok”.</div></div>
          <form class="bot-send" id="bot-form"><input class="input" id="bot-in" autocomplete="off" maxlength="800" placeholder="Type as a customer…">
            <button class="btn btn-primary" type="submit" id="bot-go">Send</button></form>
          <div class="bot-row"><span class="hint" id="bot-draft"></span><button type="button" class="btn btn-ghost btn-sm" id="bot-reset">New test chat</button></div>
        </section>
        <section class="card panel bot-card">
          <h3>Conversations <span class="muted" style="font-weight:500">· ${num(st.chats)} on Messenger${st.muted ? `, ${num(st.muted)} silenced` : ""}</span></h3>
          <div id="bot-chats">${d.chats.length ? d.chats.map((c) => `
            <div class="bot-chat" data-chat="${c.id}">
              <div class="grow"><strong>${esc(c.name || "Messenger user")}</strong> ${c.muted_at ? `<span class="badge badge-failed">Silenced</span>` : ""}
                <div class="hint" style="margin:2px 0 0">${c.last_message_at ? fmtDate(c.last_message_at) : ""} · ${num(c.orders)} order${c.orders === 1 ? "" : "s"} · ${esc((c.last_text || "").slice(0, 70))}</div></div>
              ${c.muted_at ? `<button type="button" class="btn btn-secondary btn-sm" data-unmute="${c.id}">Unmute</button>` : ""}
              <button type="button" class="btn btn-ghost btn-sm" data-open="${c.id}">View</button>
            </div><div class="bot-transcript hidden" id="tr-${c.id}"></div>`).join("") : `<p class="hint" style="margin:0">No Messenger chats yet.</p>`}</div>
        </section>
      </div>
      <div class="bot-col">
        <section class="card panel bot-card">
          <h3>Setup</h3>
          <ul class="bot-setup">
            ${tick(su.openrouter, "OpenRouter key", "add OPENROUTER_API_KEY in Render")}
            ${tick(su.meta_token, "Facebook Page token", "META_PAGE_TOKEN")}
            ${tick(su.meta_secret, "Meta app secret", "META_APP_SECRET")}
            ${tick(su.verify_token, "Webhook verify token", "META_VERIFY_TOKEN")}
          </ul>
          <div class="field"><span class="label">Webhook callback URL (for the Meta app)</span>
            <div class="setup-key"><code>${esc(su.webhook_url)}</code></div></div>
          <label class="check"><input type="checkbox" id="bot-on" ${s.enabled ? "checked" : ""} ${ready ? "" : "disabled"}>
            <span><strong>Reply on Messenger</strong><br><span class="muted">${ready ? "When off, messages are saved but not answered." : "Finish the setup above first."}</span></span></label>
        </section>
        <section class="card panel bot-card">
          <h3>How it talks</h3>
          <div class="field"><label for="bot-model">AI model (OpenRouter)</label>
            <input class="input" id="bot-model" value="${esc(s.model)}" maxlength="120"></div>
          <div class="field"><label for="bot-notes">Your notes for the bot</label>
            <textarea class="textarea" id="bot-notes" rows="7" maxlength="1500" placeholder="Facts it can use to answer questions, e.g.&#10;- Orders usually start within 1 hour.&#10;- Undelivered amounts become credit.&#10;- We don't need passwords, only the public link.&#10;- Be friendly, use Taglish if the customer does.">${esc(s.notes)}</textarea>
            <span class="hint" id="bot-notes-count"></span></div>
          <button type="button" class="btn btn-primary" id="bot-save">Save</button>
          <p class="hint" style="margin:0">Prices, services and payment always come from your bot menu, never from the AI. Notes are sent with every reply, so keep them short.</p>
        </section>
        <section class="card panel bot-card">
          <h3>Usage</h3>
          <div class="bot-stats">
            <div><span class="k">Replies (7 days)</span><span class="v">${num(st.replies_7d)}</span></div>
            <div><span class="k">AI cost (7 days)</span><span class="v">${php(st.cost_7d)}</span></div>
            <div><span class="k">AI cost (all time)</span><span class="v">${php(st.cost_all)}</span></div>
            <div><span class="k">Orders from chat</span><span class="v">${num(st.orders)}</span></div>
          </div>
        </section>
      </div>
    </div>`;

  /* bot menu */
  let editing = null, menuItems = [];
  const loadMenu = async () => {
    try { menuItems = await call("/admin/api/bot/menu"); } catch (e) { $("menu-list").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
    $("menu-list").innerHTML = menuItems.length ? `<div class="table-scroll"><table class="table menu-table"><thead><tr>
        <th>#</th><th>Item</th><th class="num">Your price</th><th>SMMGen service</th><th class="num">Your cost</th><th class="num">Profit</th><th></th></tr></thead><tbody>
      ${menuItems.map((m, i) => {
        const g = m.smmgen, profit = g && g.cost_php != null ? m.price_php - g.cost_php : null;
        return `<tr class="${m.active ? "" : "muted"}">
          <td class="muted menu-n">${i + 1}</td>
          <td class="menu-name"><strong>${esc(m.name)}</strong>${m.active ? "" : ` <span class="badge badge-canceled">Off</span>`}</td>
          <td class="num" data-k="Price">₱${m.price_php.toFixed(2)} <span class="muted">/ ${num(m.per_qty)}</span></td>
          <td data-k="SMMGen"><span class="mono">#${m.provider_service_id}</span><div class="hint" style="margin:2px 0 0;max-width:300px">${g ? `${esc(g.name)} · ${num(g.min)}–${num(g.max)}` : `<span style="color:var(--bad)">Not found at SMMGen</span>`}</div></td>
          <td class="num" data-k="Your cost">${g && g.cost_php != null ? `₱${g.cost_php.toFixed(2)}` : "–"}</td>
          <td class="num" data-k="Profit">${profit == null ? "–" : `<strong style="color:${profit < 0 ? "var(--bad)" : "inherit"}">₱${profit.toFixed(2)}</strong>`}</td>
          <td class="menu-acts" style="white-space:nowrap"><button type="button" class="btn btn-ghost btn-sm" data-medit="${m.id}">Edit</button>
            <button type="button" class="btn btn-ghost btn-sm" data-mdel="${m.id}">Remove</button></td></tr>`;
      }).join("")}</tbody></table></div>
      <p class="hint" style="margin:6px 0 0">Cost and profit are per the "per" amount, at today's SMMGen rate. Customers can order any amount within SMMGen's min–max; the price scales.</p>`
      : `<p class="hint" style="margin:0">No items yet. Add what the bot should sell.</p>`;
    $("menu-list").querySelectorAll("[data-medit]").forEach((b) => b.addEventListener("click", () => openForm(menuItems.find((x) => x.id === Number(b.dataset.medit)))));
    $("menu-list").querySelectorAll("[data-mdel]").forEach((b) => b.addEventListener("click", async () => {
      if (!b.classList.contains("armed")) { b.classList.add("armed"); b.textContent = "Tap again"; return; }
      try { await call(`/admin/api/bot/menu/${b.dataset.mdel}`, { method: "DELETE" }); toast("Removed from the menu"); } catch (e) { toast(e.message, { bad: true }); }
      loadMenu();
    }));
  };
  const lookup = debounce(async () => {
    const id = Number($("mf-sid").value), per = Number($("mf-per").value) || 1000;
    if (!id) { $("mf-info").textContent = ""; return; }
    try {
      const g = await call(`/admin/api/bot/lookup?id=${id}&per_qty=${per}`);
      const price = Number($("mf-price").value);
      $("mf-info").innerHTML = `SMMGen #${g.provider_service_id}: <strong>${esc(g.name)}</strong> · min ${num(g.min_qty)}, max ${num(g.max_qty)} · your cost ₱${g.cost_php.toFixed(2)} per ${num(per)}`
        + (g.custom_comments ? ` · <span style="color:var(--bad)">needs typed comments: can't be sold in chat</span>`
          : price ? ` · profit <strong style="color:${price < g.cost_php ? "var(--bad)" : "inherit"}">₱${(price - g.cost_php).toFixed(2)}</strong>${price < g.cost_php ? " (below cost!)" : ""}` : "");
    } catch (e) { $("mf-info").innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  }, 400);
  ["mf-sid", "mf-per", "mf-price"].forEach((id) => $(id).addEventListener("input", lookup));
  const openForm = (m) => {
    editing = m ? m.id : null;
    $("mf-name").value = m ? m.name : ""; $("mf-price").value = m ? m.price_php : ""; $("mf-per").value = m ? m.per_qty : 1000;
    $("mf-sid").value = m ? m.provider_service_id : ""; $("mf-active").checked = m ? m.active : true;
    $("mf-save").textContent = m ? "Save changes" : "Save item";
    $("menu-form").classList.remove("hidden");
    $("mf-info").textContent = "";
    if (m) lookup();
    $("mf-name").focus();
  };
  $("menu-add").onclick = () => openForm(null);
  $("mf-cancel").onclick = () => { $("menu-form").classList.add("hidden"); editing = null; };
  $("menu-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const body = { name: $("mf-name").value.trim(), price_php: Number($("mf-price").value), per_qty: Number($("mf-per").value),
      provider_service_id: Number($("mf-sid").value), active: $("mf-active").checked,
      sort: editing ? (menuItems.find((x) => x.id === editing)?.sort || 0) : menuItems.length };
    if (body.name.length < 2 || !(body.price_php > 0) || !(body.per_qty > 0) || !body.provider_service_id) { toast("Fill in all four fields", { bad: true }); return; }
    $("mf-save").disabled = true;
    try {
      await call(editing ? `/admin/api/bot/menu/${editing}` : "/admin/api/bot/menu", { method: editing ? "PUT" : "POST", body });
      toast(editing ? "Item updated" : "Added to the menu");
      $("menu-form").classList.add("hidden"); editing = null;
      loadMenu();
    } catch (e) { toast(e.message, { bad: true }); }
    $("mf-save").disabled = false;
  };
  loadMenu();

  /* settings */
  const paintCount = () => { $("bot-notes-count").textContent = `${$("bot-notes").value.length} / 1500 characters`; };
  $("bot-notes").addEventListener("input", paintCount); paintCount();
  $("bot-save").onclick = async () => {
    try { await call("/admin/api/bot", { method: "PUT", body: { model: $("bot-model").value.trim(), notes: $("bot-notes").value } }); toast("Saved"); }
    catch (e) { toast(e.message, { bad: true }); }
  };
  $("bot-on").onchange = async (ev) => {
    try { await call("/admin/api/bot", { method: "PUT", body: { enabled: ev.target.checked } }); toast(ev.target.checked ? "The bot now replies on Messenger" : "Bot replies are off"); }
    catch (e) { ev.target.checked = !ev.target.checked; toast(e.message, { bad: true }); }
  };

  /* test chat */
  const log = $("bot-log");
  const bubble = (who, text, meta = "") => {
    log.querySelector(".bot-empty")?.remove();
    const el = document.createElement("div");
    el.className = `bot-msg ${who}`;
    el.innerHTML = `<div class="bubble">${esc(text).replace(/\n/g, "<br>")}</div>${meta ? `<div class="bot-meta">${meta}</div>` : ""}`;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  };
  const paintDraft = (dr) => {
    const parts = [dr.item_name, dr.quantity && `×${num(dr.quantity)}`, dr.link && "link ✓", dr.step].filter(Boolean);
    $("bot-draft").textContent = parts.length ? `Draft: ${parts.join(" · ")}` : "";
  };
  $("bot-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const text = $("bot-in").value.trim();
    if (!text) return;
    $("bot-in").value = "";
    bubble("in", text);
    $("bot-go").disabled = true;
    try {
      const r = await call("/admin/api/bot/test", { method: "POST", body: { session: botSessionId(), text } });
      const a = r.debug.ai || {};
      const understood = a.intent ? `understood: ${esc(a.intent)}${a.item ? ` · item ${num(a.item)}` : ""}${a.quantity ? ` ×${num(a.quantity)}` : ""}` : "";
      const cost = r.debug.tokens_in != null ? ` · ${num((r.debug.tokens_in || 0) + (r.debug.tokens_out || 0))} tokens${r.debug.cost_usd != null ? ` · ${php(r.debug.cost_usd)}` : ""}` : "";
      if (!r.replies.length) bubble("out", "(no reply: the bot silenced this chat as spam)", understood + cost);
      r.replies.forEach((m, i) => bubble("out", m.text + (m.button ? `\n[${m.button.title}]` : ""), i === 0 ? understood + cost : ""));
      if (r.debug.error) bubble("out", `AI error: ${r.debug.error}`);
      paintDraft(r.draft || {});
    } catch (e) { bubble("out", e.message); }
    $("bot-go").disabled = false;
    $("bot-in").focus();
  };
  $("bot-reset").onclick = async () => {
    try { await call("/admin/api/bot/test/reset", { method: "POST", body: { session: botSessionId() } }); } catch { /* ignore */ }
    botSession = null;
    try { sessionStorage.removeItem("botSession"); } catch { /* private mode */ }
    log.innerHTML = `<div class="muted bot-empty">New test chat.</div>`;
    paintDraft({});
  };

  /* conversations */
  view.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => {
    const box = $(`tr-${b.dataset.open}`);
    if (!box.classList.contains("hidden")) { box.classList.add("hidden"); b.textContent = "View"; return; }
    try {
      const r = await call(`/admin/api/bot/chats/${b.dataset.open}`);
      box.innerHTML = r.messages.map((m) => `<div class="bot-msg ${m.direction}"><div class="bubble">${esc(m.text).replace(/\n/g, "<br>")}</div>
        <div class="bot-meta">${fmtDate(m.created_at)}</div></div>`).join("") || `<p class="hint">No messages.</p>`;
      box.classList.remove("hidden");
      b.textContent = "Hide";
    } catch (e) { toast(e.message, { bad: true }); }
  }));
  view.querySelectorAll("[data-unmute]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await call(`/admin/api/bot/chats/${b.dataset.unmute}/unmute`, { method: "POST" }); toast("Unmuted: the bot will reply again"); renderBot(); }
    catch (e) { toast(e.message, { bad: true }); b.disabled = false; }
  }));
}

/* pause / resume sending orders to SMMGen */
let sendingTimer = null;
async function setupSending() {
  clearTimeout(sendingTimer);
  const box = $("sending");
  if (!box) return;
  let st;
  try { st = await call("/admin/api/sending"); } catch (e) { box.innerHTML = `<p class="hint" style="margin:0">${esc(e.message)}</p>`; return; }
  const waiting = st.queued ? `<strong>${num(st.queued)} order${st.queued === 1 ? "" : "s"} waiting</strong> (${peso(st.queued_php)})` : "No orders waiting";
  const secs = st.queued;
  const text = st.paused
    ? `Paused since ${fmtDate(st.paused_since)}. Customers can still order: they're charged and the order waits as Pending. ${waiting}. When you resume, they go to SMMGen one per second, oldest first.`
    : st.queued
      ? `Sending the backlog to SMMGen, one per second: ${waiting}, about ${secs < 60 ? `${secs} seconds` : `${Math.ceil(secs / 60)} minutes`} left. New orders join the end of the line.`
      : "On. New orders go to SMMGen right away.";
  box.classList.toggle("paused", st.paused);
  box.innerHTML = `
    <div class="sending-head">
      <h3>Sending orders to SMMGen</h3>
      <span class="badge ${st.paused ? "badge-failed" : st.queued ? "badge-progress" : "badge-completed"}">${st.paused ? "Paused" : st.queued ? "Sending backlog" : "On"}</span>
    </div>
    <p class="hint" style="margin:0">${text}</p>
    <div><button type="button" class="btn ${st.paused ? "btn-primary" : "btn-secondary"}" id="send-toggle">${st.paused ? "Resume sending" : "Pause sending"}</button></div>`;
  const btn = $("send-toggle");
  btn.onclick = async () => {
    const ask = st.paused ? `Tap again: send ${st.queued ? `${num(st.queued)} waiting order${st.queued === 1 ? "" : "s"} and ` : ""}resume` : "Tap again: pause sending";
    if (btn.textContent !== ask) { btn.textContent = ask; btn.classList.add("armed"); return; }   // second tap confirms
    btn.disabled = true;
    try {
      await call("/admin/api/sending", { method: "POST", body: { paused: !st.paused } });
      toast(st.paused ? "Sending resumed" : "Sending paused. New orders will wait as Pending.");
    } catch (e) { toast(e.message, { bad: true }); }
    setupSending();
  };
  if (!st.paused && st.queued) sendingTimer = setTimeout(() => { if ($("sending")) setupSending(); }, 2000);   // live countdown
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

const ORDER_FILTERS = [["", "All"], ["needs_review", "Under review"], ["queued", "Queued"], ["pending", "Pending"], ["in_progress", "In progress"],
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
  ["cancels", "warn", "Cancellations", "Cancel requests to take to SMMGen support"],
  ["bot", "chat", "Messenger bot", "Test the bot, its notes, and chats"],
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
