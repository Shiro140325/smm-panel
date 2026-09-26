import {
  api, esc, fmtDate, initTheme, num, orderCharge, peso, PLATFORMS, PLATFORM_ORDER, refillText, enhanceSelect, tierBadge, toast,
} from "./common.js";
import { icons } from "./icons.js";
import { startTour } from "./tour.js";

initTheme(icons);

const view = document.getElementById("view");
const state = {
  user: null,
  services: [],
  // new-order form
  platform: null,
  category: null,
  serviceId: null,
  search: "",
  link: "",
  quantity: "",
  comments: "",
  ack: false,
  placing: false,
  // mass order
  massText: "",
  massAck: false,
  massPlacing: false,
  idPlatform: null,
  idSearch: "",
  // orders
  ordersFilter: "",
  ordersQuery: "",
  // funds
  amount: "500",
};

/* ---------------------------------------------------------------- shell */

document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = icons[el.dataset.icon](18)));
document.querySelectorAll("[data-logout]").forEach((b) => {
  b.innerHTML = icons.logout(18);
  b.addEventListener("click", async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    location.replace("/login/");
  });
});

function setBalance(v) {
  document.querySelectorAll("[data-balance]").forEach((el) => (el.textContent = peso(v)));
}

async function refreshMe() {
  const me = await api("/auth/me");
  state.user = me;
  setBalance(me.balance_php);
  document.getElementById("user-email").textContent = me.email;
  document.getElementById("avatar").textContent = (me.email || "?").slice(0, 1).toUpperCase();
  return me;
}

const ROUTES = ["new", "mass", "orders", "funds", "recent", "support", "affiliate", "api", "more"];
const MORE = ["more", "recent", "support", "affiliate", "api"];   // on phones these sit behind the "More" tab

function route() {
  const [name, qs] = location.hash.replace(/^#/, "").split("?");
  return { name: ROUTES.includes(name) ? name : "new", params: new URLSearchParams(qs || "") };
}

let servicesReady = false, servicesLoad = Promise.resolve();

function render() {
  const { name, params } = route();
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name || (a.dataset.nav === "more" && MORE.includes(name))) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  clearTimeout(state.ordersTimer);
  if (!servicesReady && (name === "new" || name === "mass")) {
    servicesLoad.then(() => { if (route().name === name) render(); });
    return;
  }
  if (name === "new") renderNew();
  else if (name === "mass") renderMass();
  else if (name === "orders") renderOrders();
  else if (name === "support") renderSupport();
  else if (name === "recent") renderRecent();
  else if (name === "affiliate") renderAffiliate();
  else if (name === "api") renderApi();
  else if (name === "more") renderMore();
  else renderFunds(params);
  window.scrollTo(0, 0);
  // balance may have changed elsewhere (top-up credited, refunds): refresh it and re-check the form
  refreshMe().then(() => {
    const n = route().name;
    if (n === "new") updateCharge();
    else if (n === "mass") updateMass();
    else if (n === "funds") updateFunds();
  }).catch(() => {});
}

window.addEventListener("hashchange", render);

/* ------------------------------------------------------------ more (phones) */

function renderMore() {
  const item = (href, icon, title, sub) => `
    <a class="card more-item" href="${href}">
      <span class="more-icon">${icons[icon](20)}</span>
      <span class="grow"><span class="t">${title}</span><span class="s">${sub}</span></span>
    </a>`;
  view.innerHTML = `
    <div class="page-head"><h1>More</h1></div>
    <div class="more-list">
      ${item("#recent", "done", "Recently completed", "Orders just delivered for other customers")}
      ${item("#affiliate", "gift", "Affiliate", "Earn credit when friends top up")}
      ${item("#api", "code", "API", "Resell our services from your own panel")}
      ${item("#support", "chat", "Support", "support@smmshiro.com")}
    </div>`;
}

async function copy(text, done) {
  try { await navigator.clipboard.writeText(text); toast(done); } catch { toast(text); }
}

/* ------------------------------------------------------------ recently completed */

function took(sec) {
  if (sec == null || sec < 0) return "–";
  const m = Math.round(sec / 60);
  if (m < 60) return `${Math.max(m, 1)} min`;
  const h = Math.floor(m / 60), d = Math.floor(h / 24);
  return d >= 1 ? `${d}d ${h % 24}h` : `${h}h ${m % 60}m`;
}

function ago(iso) {
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return fmtDate(iso);
}

async function renderRecent() {
  view.innerHTML = `
    <div class="page-head"><h1>Recently completed</h1></div>
    <p class="muted" style="margin-top:-6px">Orders just delivered for SMM Shiro customers. Links and accounts stay private.</p>
    <div class="card table-card"><div class="table-scroll">
      <table class="table recent-table" aria-label="Recently completed orders">
        <thead><tr><th>Service</th><th class="num">Quantity</th><th class="num">Delivered in</th><th class="num">Completed</th></tr></thead>
        <tbody id="recent-body"><tr><td colspan="4" class="empty">Loading…</td></tr></tbody>
      </table>
    </div></div>`;
  let rows;
  try { rows = await api("/orders/recently-completed"); } catch (e) {
    document.getElementById("recent-body").innerHTML = `<tr><td colspan="4" class="empty">${esc(e.message)}</td></tr>`;
    return;
  }
  const body = document.getElementById("recent-body");
  if (!body) return;
  body.innerHTML = rows.length ? rows.map((r) => `<tr>
      <td data-col="svc"><div class="svc">${esc(r.service_name)} ${tierBadge(r.tier)}</div>
        <div class="muted" style="font-size:13px">${esc(PLATFORMS[r.platform] || r.platform)}${r.category ? ` · ${esc(r.category)}` : ""}</div></td>
      <td data-col="qty" class="num">${num(r.quantity)}</td>
      <td data-col="took" class="num">${took(r.took_seconds)}</td>
      <td data-col="when" class="num muted">${ago(r.completed_at)}</td>
    </tr>`).join("")
    : `<tr><td colspan="4" class="empty">No completed orders yet.</td></tr>`;
}

/* ------------------------------------------------------------ affiliate */

async function renderAffiliate() {
  view.innerHTML = `<div class="page-head"><h1>Affiliate</h1></div><p class="muted">Loading…</p>`;
  let a;
  try { a = await api("/account/affiliate"); } catch (e) { view.querySelector("p").textContent = e.message; return; }
  if (route().name !== "affiliate") return;
  const rows = a.recent.map((r) => `<tr><td>${fmtDate(r.created_at)}</td><td>${esc(r.email)}</td>
    <td class="num">${peso(r.topup_php)}</td><td class="num"><strong>+${peso(r.commission_php)}</strong></td></tr>`).join("");
  view.innerHTML = `
    <div class="page-head"><h1>Affiliate</h1></div>
    <div class="two-col">
      <div class="card panel primary support-card">
        <span class="kicker">Your referral link</span>
        <h2 class="aff-headline">Earn ${num(a.pct)}% of every top-up your friends make.</h2>
        <p style="color:var(--ink-2)">Share your link. When someone creates an account with it, ${num(a.pct)}% of each top-up they complete is added to your balance, for as long as they use SMM Shiro.</p>
        <div class="copy-row"><input class="input mono" id="ref-link" value="${esc(a.link)}" readonly aria-label="Referral link">
          <button type="button" class="btn btn-primary" id="copy-link">Copy link</button></div>
        <span class="hint">Code: <span class="mono">${esc(a.code)}</span></span>
      </div>
      <div class="aside">
        <div class="card details">
          <h3>Your stats</h3>
          <div class="kv aff-kv">
            <div><span class="k">Signed up</span><span class="v">${num(a.referred)}</span></div>
            <div><span class="k">Topped up</span><span class="v">${num(a.paying)}</span></div>
            <div><span class="k">Earned</span><span class="v">${peso(a.earned_php)}</span></div>
          </div>
        </div>
        <div class="card details">
          <h3>Rules</h3>
          <p style="color:var(--ink-2);font-size:14px">Commission is balance credit for orders, not cash. Referring yourself or your own accounts isn't allowed. See the <a href="/terms/">Terms</a>.</p>
        </div>
      </div>
    </div>
    <div class="card table-card">
      <div class="table-scroll"><table class="table">
        <thead><tr><th>Date</th><th>Friend</th><th class="num">Top-up</th><th class="num">You earned</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="muted">No commissions yet. Share your link to start earning.</td></tr>`}</tbody>
      </table></div>
    </div>`;
  document.getElementById("copy-link").addEventListener("click", () => copy(a.link, "Referral link copied"));
  document.getElementById("ref-link").addEventListener("focus", (e) => e.target.select());
}

/* ------------------------------------------------------------ reseller API */

async function renderApi() {
  view.innerHTML = `<div class="page-head"><h1>API</h1></div><p class="muted">Loading…</p>`;
  let k;
  try { k = await api("/account/api-key"); } catch (e) { view.querySelector("p").textContent = e.message; return; }
  if (route().name !== "api") return;
  const ex = (params, out) => `<div class="api-ex"><div class="api-params">${params}</div><pre class="api-out">${esc(out)}</pre></div>`;
  view.innerHTML = `
    <div class="page-head"><h1>API</h1></div>
    <div class="two-col">
      <div class="card panel primary support-card">
        <span class="kicker">Reseller API</span>
        <p style="color:var(--ink-2)">Connect your own panel or scripts. Standard SMM panel API v2, so most panel software works as is. Prices and balance are in PHP.</p>
        <div class="kv api-kv">
          <div><span class="k">API URL</span><span class="v mono">${esc(k.url)}</span></div>
          <div><span class="k">Method</span><span class="v mono">POST</span></div>
          <div><span class="k">Format</span><span class="v">Form fields or JSON</span></div>
          <div><span class="k">Limit</span><span class="v">120 requests / minute</span></div>
        </div>
        <div id="key-box"></div>
      </div>
      <div class="aside">
        <div class="card details">
          <h3>Keep your key secret</h3>
          <p style="color:var(--ink-2);font-size:14px">Anyone with your key can place orders with your balance. We only store a fingerprint of it, so it's shown once. Lost it or leaked it? Generate a new one: the old key stops working right away.</p>
        </div>
      </div>
    </div>
    <div class="card panel api-docs">
      <h3>Actions</h3>
      <p class="hint">Every request sends <span class="mono">key</span> and <span class="mono">action</span>. Errors come back as <span class="mono">{"error": "..."}</span>.</p>
      <h4>Services</h4>${ex("action=services", '[{"service": 1, "name": "TikTok Followers", "type": "Default", "category": "TikTok Followers", "rate": "52.20", "min": 10, "max": 100000, "refill": true, "cancel": false}]')}
      <h4>Add order</h4>${ex("action=add · service · link · quantity<br><span class='muted'>Custom comments: comments (one per line) instead of quantity</span>", '{"order": 23501}')}
      <h4>Order status</h4>${ex("action=status · order=23501<br><span class='muted'>or orders=1,10,100 (up to 100)</span>", '{"charge": "52.20", "start_count": "3572", "status": "Partial", "remains": "157", "currency": "PHP"}')}
      <h4>Refill</h4>${ex("action=refill · order=23501<br><span class='muted'>or orders=1,2,3</span>", '{"refill": 1}')}
      <h4>Refill status</h4>${ex("action=refill_status · refill=1<br><span class='muted'>or refills=1,2,3</span>", '{"status": "Completed"}')}
      <h4>Cancel</h4>${ex("action=cancel · orders=1,2,3", '[{"order": 1, "cancel": 1}, {"order": 2, "cancel": {"error": "This service can\'t be canceled once placed"}}]')}
      <h4>Balance</h4>${ex("action=balance", '{"balance": "1000.00", "currency": "PHP"}')}
      <p class="hint">Statuses: Pending, In progress, Completed, Partial, Canceled. Undelivered parts are refunded to your balance automatically, same as on the site.</p>
    </div>`;
  paintKey(k.has_key, null);
}

function paintKey(hasKey, fresh) {
  const box = document.getElementById("key-box");
  if (!box) return;
  box.innerHTML = fresh ? `
      <div class="alert alert-info" role="status">Copy your key now. It won't be shown again.</div>
      <div class="copy-row"><input class="input mono" id="api-key" value="${esc(fresh)}" readonly aria-label="API key">
        <button type="button" class="btn btn-primary" id="copy-key">Copy key</button></div>`
    : `<p class="hint">${hasKey ? "You have an active API key." : "You don't have an API key yet."}</p>`;
  box.insertAdjacentHTML("beforeend", `<div class="support-actions" style="margin-top:12px">
      <button type="button" class="btn ${fresh ? "btn-secondary" : "btn-primary"}" id="gen-key">${hasKey ? "Generate new key" : "Generate API key"}</button>
      ${hasKey ? `<button type="button" class="btn btn-ghost" id="revoke-key">Revoke</button>` : ""}</div>`);
  if (fresh) {
    document.getElementById("copy-key").addEventListener("click", () => copy(fresh, "API key copied"));
    document.getElementById("api-key").addEventListener("focus", (e) => e.target.select());
  }
  document.getElementById("gen-key").addEventListener("click", async () => {
    if (hasKey && !confirm("Generate a new key? Your current key will stop working.")) return;
    try { paintKey(true, (await api("/account/api-key", { method: "POST" })).key); }
    catch (e) { toast(e.message, { bad: true }); }
  });
  document.getElementById("revoke-key")?.addEventListener("click", async () => {
    if (!confirm("Revoke your API key? Scripts using it will stop working.")) return;
    try { await api("/account/api-key", { method: "DELETE" }); paintKey(false, null); toast("API key revoked"); }
    catch (e) { toast(e.message, { bad: true }); }
  });
}

/* ------------------------------------------------------------ support */

const SUPPORT_EMAIL = "support@smmshiro.com";

function renderSupport() {
  const subject = encodeURIComponent("SMM Shiro support");
  const body = encodeURIComponent(`Account: ${state.user?.email || ""}\nOrder # (if any): \n\nWhat happened:\n`);
  view.innerHTML = `
    <div class="page-head"><h1>Support</h1></div>
    <div class="two-col">
      <div class="card panel primary support-card">
        <span class="kicker">Email us</span>
        <a class="support-email" href="mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}">${SUPPORT_EMAIL}</a>
        <p style="color:var(--ink-2)">Questions about an order, a top-up or your account: send us an email and we'll get back to you as soon as we can.</p>
        <div class="support-actions">
          <a class="btn btn-primary" href="mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}">${icons.chat(18)}Email support</a>
          <button type="button" class="btn btn-secondary" id="copy-email">Copy address</button>
        </div>
      </div>
      <div class="aside">
        <div class="card details">
          <h3>To help us help you faster</h3>
          <ul class="support-tips">
            <li>Send it from <strong>${esc(state.user?.email || "the email you signed up with")}</strong>, so we can find your account.</li>
            <li>Include the <strong>order #</strong> (from your <a href="#orders">Orders</a> page) or the top-up amount and time.</li>
            <li>Say what you expected and what happened instead. A screenshot helps.</li>
            <li>Never send your social media password. We don't need it.</li>
          </ul>
        </div>
        <div class="card details">
          <h3>Before you write</h3>
          <p style="color:var(--ink-2);font-size:14px">Undelivered parts of an order are refunded to your balance automatically, and refills can be requested from <a href="#orders">Orders</a> once an order completes. Common questions are answered in the <a href="/#faq">FAQ</a>.</p>
        </div>
      </div>
    </div>`;
  document.getElementById("copy-email").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(SUPPORT_EMAIL); toast("Email address copied"); }
    catch { toast(SUPPORT_EMAIL); }
  });
}

/* ------------------------------------------------------------ new order */

const selectedService = () => state.services.find((s) => s.id === state.serviceId) || null;

const LIST_LIMIT = 80;              // rows rendered at once; search narrows the rest
const RECOMMENDED = "__featured";
const PHILIPPINES = "__ph";
const NON_DROP = "__nondrop";
const isPH = (s) => /\bPH\b/.test(s.tier || "");   // "PH" and "Real · PH" tiers

/** Build lookups once per load, so typing never re-derives them for thousands of services. */
function indexServices() {
  state.byId = new Map();
  for (const s of state.services) {
    state.byId.set(s.id, s);
    s._hay = `${s.id} ${s.name} ${s.description || ""} ${s.tier} ${s.category || ""}${isPH(s) ? " philippines" : ""}${s.non_drop ? " non-drop nondrop" : ""}`.toLowerCase();
  }
}

/** Every word of the query must appear in the service's id, name, description, tier or category. */
function matches(s, q) {
  if (!q) return true;
  return q.split(/\s+/).every((w) => s._hay.includes(w));
}

function categoriesFor(platform) {
  const counts = new Map();
  let featured = 0, ph = 0, nonDrop = 0;
  for (const s of state.services) {
    if (s.platform !== platform) continue;
    if (s.featured) featured++;
    if (isPH(s)) ph++;
    if (s.non_drop) nonDrop++;
    const c = s.category || "Other";
    counts.set(c, (counts.get(c) || 0) + 1);
  }
  const cats = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  // Philippines and Non-drop are cross-category views: their services also stay in their own category
  return [...(featured ? [[RECOMMENDED, featured]] : []), ...(ph ? [[PHILIPPINES, ph]] : []),
    ...(nonDrop ? [[NON_DROP, nonDrop]] : []), ...cats];
}

const CATEGORY_LABELS = { [RECOMMENDED]: "Recommended", [PHILIPPINES]: "Philippines", [NON_DROP]: "Non-drop" };
const categoryLabel = (c) => CATEGORY_LABELS[c] || c;

function servicesForPlatform() {
  const q = state.search.trim().toLowerCase();
  return state.services.filter((s) => s.platform === state.platform && (q
    ? matches(s, q)   // searching looks across every category
    : state.category === RECOMMENDED ? s.featured
      : state.category === PHILIPPINES ? isPH(s)
      : state.category === NON_DROP ? s.non_drop
        : (s.category || "Other") === state.category));
}

const days = (n) => `${num(n)} day${n === 1 ? "" : "s"}`;

function svcSub(s) {
  return [s.non_drop_days ? `Non-drop ${days(s.non_drop_days)}` : "", s.description, s.start_time, refillText(s.refill_days)].filter(Boolean).map(esc).join(" · ");
}

function orderQty(svc) {
  if (!svc) return 0;
  if (svc.custom_comments) return state.comments.split("\n").map((l) => l.trim()).filter(Boolean).length;
  const n = parseInt(state.quantity, 10);
  return Number.isFinite(n) ? n : 0;
}

function validate(svc) {
  if (!svc) return "Pick a service";
  const qty = orderQty(svc);
  if (svc.custom_comments && qty === 0) return "Add at least one comment";
  if (qty < svc.min) return `Minimum is ${num(svc.min)}`;
  if (qty > svc.max) return `Maximum is ${num(svc.max)}`;
  if (!/^https?:\/\/\S+$/i.test(state.link.trim())) return "Paste a link that starts with https://";
  const charge = orderCharge(svc.price_per_1k_php, qty);
  if (state.user && charge > state.user.balance_php) return "low-balance";
  if (!state.ack) return "Tick the box to confirm";
  return null;
}

function updateCharge() {
  const svc = selectedService();
  const qty = orderQty(svc);
  const charge = svc ? orderCharge(svc.price_per_1k_php, qty) : 0;
  const problem = validate(svc);
  const chargeEl = document.getElementById("charge");
  if (!chargeEl) return;
  chargeEl.textContent = peso(charge);
  document.getElementById("qty-count").textContent = svc?.custom_comments
    ? `${num(qty)} comment${qty === 1 ? "" : "s"} · min ${num(svc.min)}, max ${num(svc.max)}`
    : svc ? `Min ${num(svc.min)} · Max ${num(svc.max)}` : "";
  const msg = document.getElementById("charge-msg");
  if (problem === "low-balance") {
    msg.innerHTML = `<a href="#funds" style="font-weight:600;color:var(--bad)">Not enough balance. Add funds</a>`;
  } else if (problem && (state.link || state.quantity || state.comments)) {
    msg.innerHTML = `<span class="hint error">${esc(problem)}</span>`;
  } else {
    msg.innerHTML = "";
  }
  const btn = document.getElementById("place");
  btn.disabled = !!problem || state.placing;
  btn.textContent = state.placing ? "Placing order…" : "Place order";
}

function svcOptionsHTML(shown) {
  return shown.length ? shown.map((s) => `
    <button type="button" class="svc-option" data-svc="${s.id}" aria-pressed="${s.id === state.serviceId}">
      <span class="grow"><span class="t">${esc(s.name)}</span><span class="s"><span class="mono">ID ${s.id}</span>${svcSub(s) ? ` · ${svcSub(s)}` : ""}</span></span>
      ${tierBadge(s.tier)}
      <span class="p">${peso(s.price_per_1k_php)}<span class="muted" style="font-weight:500"> /1K</span></span>
    </button>`).join("") : `<div class="empty">No services match "${esc(state.search)}".</div>`;
}

function svcHintText(list, shown) {
  return list.length > shown.length
    ? `Showing ${num(shown.length)} of ${num(list.length)}, cheapest first. Search to find the rest.`
    : state.search.trim() ? `${num(list.length)} match${list.length === 1 ? "" : "es"} across all categories.` : "";
}

/** Typing in the search box only redraws the list, never the input itself (keeps mobile typing smooth). */
function renderSvcList() {
  const list = servicesForPlatform();
  const shown = list.slice(0, LIST_LIMIT);
  document.getElementById("svc-list").innerHTML = svcOptionsHTML(shown);
  document.getElementById("svc-hint").textContent = svcHintText(list, shown);
  document.getElementById("svc-cat").disabled = !!state.search.trim();
  catDropdown?.sync();
}

let catDropdown = null;

function renderNew() {
  if (!state.services.length) {
    view.innerHTML = `<div class="page-head"><h1>New order</h1></div><div class="card empty">No services available right now.</div>`;
    return;
  }
  const platforms = PLATFORM_ORDER.filter((p) => state.services.some((s) => s.platform === p));
  if (!platforms.includes(state.platform)) state.platform = platforms[0];
  const cats = categoriesFor(state.platform);
  if (!cats.some(([c]) => c === state.category)) state.category = cats[0]?.[0] ?? null;
  const list = servicesForPlatform();
  const shown = list.slice(0, LIST_LIMIT);
  if (!selectedService() || selectedService().platform !== state.platform) state.serviceId = list[0]?.id ?? null;
  const svc = selectedService();

  view.innerHTML = `
    <div class="page-head"><h1>New order</h1><a href="#support" style="font-weight:600;text-decoration:none">Need help?</a></div>
    <div class="two-col">
      <form class="card panel primary" id="order-form" novalidate>
        <div class="field">
          <span class="label" id="pf-label">Platform</span>
          <div class="pill-row" role="group" aria-labelledby="pf-label">
            ${platforms.map((p) => `<button type="button" class="pill" data-platform="${p}" aria-pressed="${p === state.platform}">${esc(PLATFORMS[p] || p)}</button>`).join("")}
          </div>
        </div>
        <div class="field">
          <label for="svc-cat">Category</label>
          <select class="input select" id="svc-cat" ${state.search.trim() ? "disabled" : ""}>
            ${cats.map(([c, n]) => `<option value="${esc(c)}" data-label="${esc(categoryLabel(c))}" data-count="${num(n)}" ${c === state.category ? "selected" : ""}>${esc(categoryLabel(c))} (${num(n)})</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label for="svc-search">Service</label>
          <input class="input" id="svc-search" type="search" placeholder="Search all ${esc(PLATFORMS[state.platform] || "")} services" value="${esc(state.search)}">
          <div class="svc-list" id="svc-list" role="group" aria-label="Services">${svcOptionsHTML(shown)}</div>
          <span class="hint" id="svc-hint">${svcHintText(list, shown)}</span>
        </div>
        <div class="field">
          <label for="link">Link</label>
          <input class="input" id="link" type="url" inputmode="url" autocomplete="off" placeholder="https://" value="${esc(state.link)}">
          <span class="hint">Public link to the profile, post or video. Never your password.</span>
        </div>
        ${svc?.custom_comments ? `
        <div class="field">
          <label for="comments">Comments <span class="muted" style="font-weight:500">(one per line)</span></label>
          <textarea class="textarea" id="comments" placeholder="Ganda nito!&#10;Solid 🔥&#10;Where can I buy this?">${esc(state.comments)}</textarea>
          <span class="hint" id="qty-count"></span>
        </div>` : `
        <div class="field">
          <label for="qty">Quantity</label>
          <input class="input" id="qty" type="number" inputmode="numeric" min="${svc?.min ?? 1}" max="${svc?.max ?? ""}" step="1" value="${esc(state.quantity)}" placeholder="${svc ? num(svc.min) : ""}">
          <span class="hint" id="qty-count"></span>
        </div>`}
        <div class="charge-box">
          <div style="flex:1"><div class="k">Charge</div><div class="v" id="charge">₱0.00</div></div>
          <div id="charge-msg"></div>
        </div>
        <label class="check"><input type="checkbox" id="ack" ${state.ack ? "checked" : ""}>
          <span>I understand this may go against ${esc(PLATFORMS[state.platform] || "the platform")}'s rules and that some drop-off can happen outside the refill terms.</span></label>
        <button class="btn btn-primary btn-lg btn-block" id="place" type="submit" disabled>Place order</button>
      </form>
      <div class="aside">
        ${svc ? `
        <div class="card details">
          <div style="display:flex;align-items:center;gap:10px"><h3>${esc(svc.name)}</h3>${tierBadge(svc.tier)}</div>
          ${svc.description ? `<p style="color:var(--ink-2);font-size:15px">${esc(svc.description)}</p>` : ""}
          <div class="kv">
            <div><span class="k">Service ID</span><span class="v mono">${svc.id}</span></div>
            <div><span class="k">Price</span><span class="v">${peso(svc.price_per_1k_php)} / 1,000</span></div>
            <div><span class="k">Starts</span><span class="v">${esc(svc.start_time || "Varies")}</span></div>
            <div><span class="k">Speed</span><span class="v">${esc(svc.speed || "Varies")}</span></div>
            <div><span class="k">Min / max</span><span class="v">${num(svc.min)} / ${num(svc.max)}</span></div>
            <div><span class="k">Refill</span><span class="v">${refillText(svc.refill_days)}</span></div>
            ${svc.non_drop ? `<div><span class="k">Non-drop</span><span class="v">${svc.non_drop_days ? `${days(svc.non_drop_days)} guaranteed` : "No time limit stated"}</span></div>` : ""}
            ${svc.drop_risk ? `<div><span class="k">Drop risk</span><span class="v">${esc(svc.drop_risk)}</span></div>` : ""}
          </div>
        </div>` : ""}
        <div class="card details">
          <h3>What happens next</h3>
          <p style="color:var(--ink-2);font-size:14px">Processing starts right after you place the order. Anything we can't deliver is refunded to your balance automatically. Track it in <a href="#orders">Orders</a>.</p>
        </div>
      </div>
    </div>`;

  view.querySelectorAll("[data-platform]").forEach((b) =>
    b.addEventListener("click", () => { state.platform = b.dataset.platform; state.search = ""; state.category = null; state.serviceId = null; renderNew(); }));
  catDropdown = enhanceSelect(document.getElementById("svc-cat"));
  document.getElementById("svc-cat").addEventListener("change", (e) => {
    state.category = e.target.value;
    state.serviceId = servicesForPlatform()[0]?.id ?? null;
    const hadFocus = document.activeElement === catDropdown?.btn;
    renderNew();
    if (hadFocus) document.getElementById("svc-cat-btn")?.focus();
  });
  document.getElementById("svc-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-svc]");
    if (!b) return;
    state.serviceId = Number(b.dataset.svc);
    // the redraw rebuilds the list: keep where the customer was scrolled to
    const listTop = e.currentTarget.scrollTop, pageY = window.scrollY;
    renderNew();
    document.getElementById("svc-list").scrollTop = listTop;
    window.scrollTo(0, pageY);
  });
  let searchTimer;
  document.getElementById("svc-search").addEventListener("input", (e) => {
    state.search = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderSvcList, 120);
  });
  document.getElementById("link").addEventListener("input", (e) => { state.link = e.target.value; updateCharge(); });
  document.getElementById("qty")?.addEventListener("input", (e) => { state.quantity = e.target.value; updateCharge(); });
  document.getElementById("comments")?.addEventListener("input", (e) => { state.comments = e.target.value; updateCharge(); });
  document.getElementById("ack").addEventListener("change", (e) => { state.ack = e.target.checked; updateCharge(); });
  document.getElementById("order-form").addEventListener("submit", placeOrder);
  updateCharge();
}

async function placeOrder(e) {
  e.preventDefault();
  const svc = selectedService();
  if (validate(svc) || state.placing) return;
  state.placing = true;
  updateCharge();
  try {
    const body = { service_id: svc.id, link: state.link.trim(), quantity: orderQty(svc) };
    if (svc.custom_comments) body.comments = state.comments;
    const res = await api("/orders", { method: "POST", body });
    toast(`Order #${res.id} placed · ${peso(res.charge_php)}`);
    state.link = ""; state.quantity = ""; state.comments = ""; state.ack = false;
    await refreshMe().catch(() => {});
    location.hash = "#orders";
  } catch (ex) {
    toast(ex.message, { bad: true });
    await refreshMe().catch(() => {});
  } finally {
    state.placing = false;
    if (route().name === "new") updateCharge();
  }
}

/* ----------------------------------------------------------- mass order */

const MASS_MAX = 100;

/** Mirrors the server's parser/validation so problems show up while typing. */
function parseMass(text) {
  const byId = state.byId;
  const rows = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    const r = { line: i + 1, raw: line };
    const parts = line.split("|").map((x) => x.trim());
    if (parts.length !== 3) { r.error = "Use service_id|link|quantity"; rows.push(r); return; }
    const [sid, link, qty] = parts;
    r.link = link;
    const id = Number(sid.replace(/^#/, ""));
    const q = Number(qty.replace(/,/g, ""));
    r.quantity = q;
    r.svc = byId.get(id);
    if (!/^\d+$/.test(sid.replace(/^#/, ""))) r.error = "Service ID must be a number";
    else if (!r.svc) r.error = `Unknown service ID ${id}`;
    else if (r.svc.custom_comments) r.error = "Custom comments: use New order";
    else if (!/^https?:\/\/\S+$/i.test(link)) r.error = "Link must start with https://";
    else if (!/^\d+$/.test(qty.replace(/,/g, "")) || q <= 0) r.error = "Quantity must be a whole number";
    else if (q < r.svc.min || q > r.svc.max) r.error = `Quantity must be ${num(r.svc.min)}–${num(r.svc.max)}`;
    else r.charge = orderCharge(r.svc.price_per_1k_php, q);
    rows.push(r);
  });
  return rows;
}

function updateMass() {
  if (!document.getElementById("mass-body")) return;
  const rows = parseMass(state.massText);
  const bad = rows.filter((r) => r.error);
  const total = rows.reduce((t, r) => t + (r.charge || 0), 0);
  const tbody = document.getElementById("mass-body");
  document.getElementById("mass-preview").classList.toggle("hidden", !rows.length);
  tbody.innerHTML = rows.map((r) => `<tr class="${r.error ? "bad" : ""}">
      <td class="mono muted">${r.line}</td>
      <td>${r.svc ? `<span style="font-weight:600">${esc(r.svc.name)}</span> ${tierBadge(r.svc.tier)}` : `<span class="muted">Unknown service</span>`}
        <div class="link">${esc(r.link || r.raw)}</div></td>
      <td class="num">${r.quantity && !Number.isNaN(r.quantity) ? num(r.quantity) : "–"}</td>
      <td class="num">${r.error ? "–" : `<strong>${peso(r.charge)}</strong>`}</td>
    </tr>${r.error ? `<tr class="bad err-row"><td></td><td colspan="3" class="err">${esc(r.error)}</td></tr>` : ""}`).join("");
  document.getElementById("mass-total").textContent = peso(total);
  document.getElementById("mass-count").textContent = rows.length ? `${rows.length} order${rows.length === 1 ? "" : "s"}` : "No orders yet";
  const msg = document.getElementById("mass-msg");
  let problem = null;
  if (!rows.length) problem = "empty";
  else if (rows.length > MASS_MAX) problem = `Up to ${MASS_MAX} orders at a time`;
  else if (bad.length) problem = `Fix ${bad.length} line${bad.length === 1 ? "" : "s"} marked in red`;
  else if (state.user && total > state.user.balance_php) problem = "low-balance";
  else if (!state.massAck) problem = "Tick the box to confirm";
  msg.innerHTML = problem === "low-balance"
    ? `<a href="#funds" style="font-weight:600;color:var(--bad)">Not enough balance. Add funds</a>`
    : problem && problem !== "empty" ? `<span class="hint error">${esc(problem)}</span>` : "";
  const btn = document.getElementById("mass-place");
  btn.disabled = !!problem || state.massPlacing;
  btn.textContent = state.massPlacing ? "Placing orders…" : rows.length > 1 ? `Place ${rows.length} orders` : "Place order";
}

function renderIdList() {
  const el = document.getElementById("id-list");
  if (!el) return;
  const q = state.idSearch.trim().toLowerCase();
  const list = state.services.filter((s) => s.platform === state.idPlatform && matches(s, q));
  const shown = list.slice(0, LIST_LIMIT);
  el.innerHTML = (shown.length ? shown.map((s) => `
    <button type="button" class="id-row" data-add="${s.id}" title="Add a line for service ${s.id}">
      <span class="id">${s.id}</span>
      <span class="n">${esc(s.name)} ${tierBadge(s.tier)}${s.custom_comments ? ` <span class="hint">(New order only)</span>` : ""}
        ${s.description || s.category ? `<span class="d">${esc([s.category, s.description].filter(Boolean).join(" · "))}</span>` : ""}</span>
      <span class="p">${peso(s.price_per_1k_php)}</span>
    </button>`).join("") : `<p class="hint" style="padding:8px 6px">No matches.</p>`)
    + (list.length > shown.length ? `<p class="hint" style="padding:8px 6px">Showing ${num(shown.length)} of ${num(list.length)}. Search to narrow it down.</p>` : "");
  el.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
    const ta = document.getElementById("mass-input");
    const prefix = ta.value && !ta.value.endsWith("\n") ? "\n" : "";
    ta.value += `${prefix}${b.dataset.add}|`;
    state.massText = ta.value;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    updateMass();
  }));
}

function renderMass() {
  const platforms = PLATFORM_ORDER.filter((p) => state.services.some((s) => s.platform === p));
  if (!platforms.includes(state.idPlatform)) state.idPlatform = platforms[0];
  view.innerHTML = `
    <div class="page-head"><h1>Mass order</h1><a href="#new" style="font-weight:600;text-decoration:none">Single order</a></div>
    <div class="two-col">
      <form class="card panel primary" id="mass-form" novalidate>
        <div class="field">
          <label for="mass-input">Orders <span class="muted" style="font-weight:500">(one per line)</span></label>
          <textarea class="textarea mass-input" id="mass-input" spellcheck="false" autocapitalize="off" autocomplete="off"
            placeholder="service_id|link|quantity&#10;22|https://www.tiktok.com/@yourpage|1000&#10;1|https://www.facebook.com/yourpage/posts/123|500">${esc(state.massText)}</textarea>
          <span class="hint">Format: <span class="mono">service_id|link|quantity</span>. Up to ${MASS_MAX} lines. Find IDs in the list${window.innerWidth > 980 ? " on the right" : " below"}. Tap one to start a line.</span>
        </div>
        <div class="card table-card hidden" id="mass-preview" style="border-radius:12px"><div class="table-scroll">
          <table class="table mass-preview" aria-label="Order preview">
            <thead><tr><th>Line</th><th>Service and link</th><th class="num">Qty</th><th class="num">Charge</th></tr></thead>
            <tbody id="mass-body"></tbody>
          </table>
        </div></div>
        <div class="charge-box">
          <div style="flex:1"><div class="k" id="mass-count">No orders yet</div><div class="v" id="mass-total">₱0.00</div></div>
          <div id="mass-msg"></div>
        </div>
        <label class="check"><input type="checkbox" id="mass-ack" ${state.massAck ? "checked" : ""}>
          <span>I understand these services may go against the platforms' rules and that some drop-off can happen outside the refill terms.</span></label>
        <button class="btn btn-primary btn-lg btn-block" id="mass-place" type="submit" disabled>Place order</button>
        <div id="mass-results"></div>
      </form>
      <div class="aside">
        <div class="card details">
          <h3>Service IDs</h3>
          <div class="pill-row" role="group" aria-label="Platform">
            ${platforms.map((p) => `<button type="button" class="pill" data-idp="${p}" aria-pressed="${p === state.idPlatform}">${esc(PLATFORMS[p] || p)}</button>`).join("")}
          </div>
          <label for="id-search" class="sr-only">Search services</label>
          <input class="input" id="id-search" type="search" placeholder="Search by name or ID" value="${esc(state.idSearch)}">
          <div class="id-list" id="id-list"></div>
        </div>
      </div>
    </div>`;

  const ta = document.getElementById("mass-input");
  ta.addEventListener("input", () => { state.massText = ta.value; updateMass(); });
  document.getElementById("mass-ack").addEventListener("change", (e) => { state.massAck = e.target.checked; updateMass(); });
  view.querySelectorAll("[data-idp]").forEach((b) => b.addEventListener("click", () => {
    state.idPlatform = b.dataset.idp;
    view.querySelectorAll("[data-idp]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    renderIdList();
  }));
  let idTimer;
  document.getElementById("id-search").addEventListener("input", (e) => {
    state.idSearch = e.target.value;
    clearTimeout(idTimer);
    idTimer = setTimeout(renderIdList, 120);
  });
  document.getElementById("mass-form").addEventListener("submit", placeMass);
  renderIdList();
  updateMass();
}

async function placeMass(e) {
  e.preventDefault();
  if (state.massPlacing) return;
  state.massPlacing = true;
  updateMass();
  const out = document.getElementById("mass-results");
  try {
    const res = await api("/orders/mass", { method: "POST", body: { orders: state.massText } });
    const failed = res.results.filter((r) => !r.ok);
    const lines = state.massText.split("\n");
    // keep only the lines that failed, so they can be fixed and resubmitted
    state.massText = failed.map((r) => lines[r.line - 1]).join("\n");
    document.getElementById("mass-input").value = state.massText;
    state.massAck = false;
    document.getElementById("mass-ack").checked = false;
    out.innerHTML = `
      <div class="alert ${failed.length ? "alert-warn" : "alert-ok"}" role="status" style="flex-direction:column;gap:6px">
        <strong>Placed ${res.placed} of ${res.results.length} order${res.results.length === 1 ? "" : "s"} · ${peso(res.charged_php)} charged</strong>
        ${failed.length ? `<span>These lines weren't placed and are still in the box above:</span>
          <ul style="margin:0;padding-left:18px">${failed.map((r) => `<li>Line ${r.line}: ${esc(r.error)}</li>`).join("")}</ul>` : ""}
        <a href="#orders" style="font-weight:600">View orders</a>
      </div>`;
    toast(`Placed ${res.placed} order${res.placed === 1 ? "" : "s"}`);
  } catch (ex) {
    out.innerHTML = `<div class="alert alert-bad" role="alert">${esc(ex.message)}</div>`;
  } finally {
    await refreshMe().catch(() => {});
    state.massPlacing = false;
    if (route().name === "mass") updateMass();
  }
}

/* --------------------------------------------------------------- orders */

const STATUS = {
  creating: ["Processing", "badge-pending"],
  pending: ["Pending", "badge-pending"],
  in_progress: ["In progress", "badge-progress"],
  completed: ["Completed", "badge-completed"],
  partial: ["Partial", "badge-partial"],
  canceled: ["Canceled", "badge-canceled"],
  failed: ["Failed", "badge-failed"],
  needs_review: ["Under review", "badge-review"],
};
const FILTERS = [["", "All"], ["pending", "Pending"], ["in_progress", "In progress"], ["completed", "Completed"], ["partial", "Partial"], ["canceled", "Canceled"], ["refilling", "Refilling"], ["refunded", "Refunded"]];

// Cancel needs a second tap within a few seconds; kept outside the row so the 10s refresh doesn't reset it.
let cancelArmed = { id: null, until: 0 };
const armed = (id) => cancelArmed.id === id && Date.now() < cancelArmed.until;

function refillCell(o) {
  if (o.cancel_requested) return `<span class="muted">Cancel requested</span>`;
  if (o.can_cancel) {
    return `<button type="button" class="btn btn-sm order-cancel${armed(o.id) ? " armed" : ""}" data-cancel-order="${o.id}">${armed(o.id) ? "Tap again to cancel" : "Cancel order"}</button>`;
  }
  if (o.refill_state === "available") return `<button type="button" class="btn btn-outline-accent" data-refill="${o.id}">Request refill</button>`;
  if (o.refill_state === "requested") return `<span class="muted">Refill in progress</span>`;
  if (Number(o.refunded_php) > 0) return `<span class="muted">${peso(o.refunded_php)} refunded</span>`;
  if (o.refill_state === "after_completion") return `<span class="muted">After completion</span>`;
  if (o.refill_state === "expired") return `<span class="muted">Refill period ended</span>`;
  return `<span class="muted refill-none">–</span>`;
}

async function loadOrders() {
  const tbody = document.getElementById("orders-body");
  if (!tbody) return;
  const qs = new URLSearchParams();
  if (state.ordersFilter) qs.set("status", state.ordersFilter);
  if (state.ordersQuery.trim()) qs.set("q", state.ordersQuery.trim());
  const stamp = document.getElementById("orders-updated");
  const key = qs.toString();
  let orders;
  try {
    orders = await api("/orders" + (key ? `?${key}` : ""));
  } catch (ex) {
    // a failed background refresh keeps the rows on screen (and the reader's place in them)
    if (tbody.dataset.key === key && tbody.dataset.sig) { if (stamp) stamp.textContent = "Couldn't update, retrying"; return null; }
    tbody.innerHTML = `<tr><td colspan="8" class="empty">${esc(ex.message)}</td></tr>`;
    delete tbody.dataset.sig;
    return null;
  }
  if (!document.body.contains(tbody)) return orders;   // left the page while loading
  if (stamp) stamp.textContent = `Updated ${new Date().toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
  // nothing changed since the last refresh: leave the rows alone, so scrolling is never disturbed
  const sig = JSON.stringify(orders);
  if (tbody.dataset.key === key && tbody.dataset.sig === sig) return orders;
  tbody.dataset.key = key;
  tbody.dataset.sig = sig;
  if (!orders.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">${state.ordersFilter || state.ordersQuery ? "No orders match." : `No orders yet. <a href="#new">Place your first order</a>.`}</td></tr>`;
    return orders;
  }
  // swapping the rows must not move the page (Safari can jump to the top when content is replaced)
  const y = window.scrollY;
  const card = tbody.closest(".orders-card");
  if (card) card.style.minHeight = `${card.offsetHeight}px`;
  tbody.innerHTML = orders.map((o) => {
    const [label, cls] = STATUS[o.status] || [o.status, "badge-pending"];
    return `<tr>
      <td data-col="id" class="mono" style="font-size:13px">#${o.id}</td>
      <td data-col="date" style="font-size:13px;color:var(--muted);white-space:nowrap">${fmtDate(o.created_at)}</td>
      <td data-col="svc"><div class="svc">${esc(o.service_name)} ${tierBadge(o.tier)}</div>${/^https?:\/\//i.test(o.link || "")
        ? `<a class="btn open-link" href="${esc(o.link)}" target="_blank" rel="noopener noreferrer" title="${esc(o.link)}">${icons.external(14)}Open link</a>`
        : ""}</td>
      <td data-col="qty" class="num">${num(o.quantity)}</td>
      <td data-col="remains" class="num muted">${o.remains == null ? "–" : num(o.remains)}</td>
      <td data-col="charge" class="num" style="font-weight:600">${peso(o.price_php)}</td>
      <td data-col="status"><span class="badge ${cls}">${label}</span></td>
      <td data-col="refill">${refillCell(o)}</td>
    </tr>`;
  }).join("");
  if (window.scrollY !== y) window.scrollTo(0, y);
  if (card) requestAnimationFrame(() => { card.style.minHeight = ""; });
  tbody.querySelectorAll("[data-refill]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await api(`/orders/${b.dataset.refill}/refill`, { method: "POST" });
      toast("Refill requested");
    } catch (ex) {
      toast(ex.message, { bad: true });
    }
    loadOrders();
  }));
  tbody.querySelectorAll("[data-cancel-order]").forEach((b) => b.addEventListener("click", async () => {
    const id = Number(b.dataset.cancelOrder);
    if (!armed(id)) {   // first tap: ask to confirm
      cancelArmed = { id, until: Date.now() + 5000 };
      b.classList.add("armed");
      b.textContent = "Tap again to cancel";
      setTimeout(() => { if (!armed(id) && b.isConnected) { b.classList.remove("armed"); b.textContent = "Cancel order"; } }, 5100);
      return;
    }
    cancelArmed = { id: null, until: 0 };
    b.disabled = true;
    b.textContent = "Canceling…";
    try {
      await api(`/orders/${id}/cancel`, { method: "POST" });
      toast("Cancel requested. Anything not delivered is refunded once the provider confirms.");
    } catch (ex) {
      toast(ex.message, { bad: true });
    }
    loadOrders();
  }));
  return orders;
}

const OPEN = ["creating", "pending", "in_progress"];

function scheduleOrders(delay) {
  clearTimeout(state.ordersTimer);
  state.ordersTimer = setTimeout(async () => {
    if (route().name !== "orders") return;
    const list = await loadOrders();
    refreshMe().catch(() => {});
    // poll fast while something is still running, slower otherwise
    scheduleOrders(list && list.some((o) => OPEN.includes(o.status)) ? 10000 : 30000);
  }, delay);
}

function renderOrders() {
  view.innerHTML = `
    <div class="page-head">
      <h1>Orders</h1>
      <button type="button" class="btn btn-secondary" id="refresh">${icons.refresh(16)} Refresh</button>
      <a class="btn btn-primary hide-mobile" href="#new">New order</a>
    </div>
    <div class="orders-toolbar">
      <div class="pill-row" role="group" aria-label="Filter by status">
        ${FILTERS.map(([v, l]) => `<button type="button" class="pill pill-round" data-filter="${v}" aria-pressed="${v === state.ordersFilter}">${l}</button>`).join("")}
      </div>
      <label for="order-search" class="sr-only">Search orders</label>
      <input class="input" id="order-search" type="search" placeholder="Search by order # or link" value="${esc(state.ordersQuery)}">
    </div>
    <div class="card table-card orders-card"><div class="table-scroll orders-scroll">
      <table class="table orders-table" aria-label="Orders">
        <thead><tr><th>ID</th><th>Date</th><th>Service and link</th><th class="num">Qty</th><th class="num">Remains</th><th class="num">Charge</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody id="orders-body"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody>
      </table>
    </div></div>
    <p class="hint"><span id="orders-updated"></span> · Running orders update every 10 seconds. Pending and in-progress orders can be canceled on services that allow it. Refill is available after an order completes, for the period shown on the service. Undelivered amounts are refunded to your balance automatically.</p>`;

  view.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    state.ordersFilter = b.dataset.filter;
    view.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    loadOrders();
  }));
  let t;
  document.getElementById("order-search").addEventListener("input", (e) => {
    state.ordersQuery = e.target.value;
    clearTimeout(t);
    t = setTimeout(loadOrders, 300);
  });
  document.getElementById("refresh").addEventListener("click", () => { loadOrders(); refreshMe().catch(() => {}); });
  scheduleOrders(0);
}

/* ---------------------------------------------------------------- funds */

const METHOD_LABEL = { gcash: "GCash", paymaya: "Maya", paymongo: "PayMongo", card: "Card", grab_pay: "GrabPay", qrph: "QR Ph" };
const PRESETS = [100, 500, 1000, 5000];
const TOPUP_MIN = 100, TOPUP_MAX = 50000;

function amountValue() {
  const n = parseInt(state.amount, 10);
  return Number.isFinite(n) ? n : 0;
}

function updateFunds() {
  if (!document.getElementById("sum-pay")) return;
  const amt = amountValue();
  const ok = amt >= TOPUP_MIN && amt <= TOPUP_MAX;
  document.getElementById("sum-pay").textContent = peso(ok ? amt : 0);
  document.getElementById("sum-add").textContent = peso(ok ? amt : 0);
  document.getElementById("sum-new").textContent = peso((state.user?.balance_php || 0) + (ok ? amt : 0));
  const hint = document.getElementById("amt-hint");
  hint.textContent = amt && amt < TOPUP_MIN ? `Minimum top-up is ${peso(TOPUP_MIN)}` : amt > TOPUP_MAX ? `Maximum top-up is ${peso(TOPUP_MAX)}` : `Min ${peso(TOPUP_MIN)} · Max ${peso(TOPUP_MAX)}`;
  hint.classList.toggle("error", !!amt && !ok);
  const btn = document.getElementById("pay");
  btn.disabled = !ok || state.paying;
  btn.innerHTML = state.paying ? "Opening checkout…" : ok ? `${icons.lock(18)} Pay ${peso(amt)}` : "Enter a valid amount";
  document.querySelectorAll("[data-preset]").forEach((b) => b.setAttribute("aria-pressed", Number(b.dataset.preset) === amt));
}

async function loadTopups() {
  const el = document.getElementById("topups");
  if (!el) return [];
  let items = [];
  try { items = await api("/topups"); } catch { el.innerHTML = `<p class="muted">Couldn't load top-ups.</p>`; return []; }
  el.innerHTML = items.length ? items.map((t) => {
    const [label, cls] = TOPUP_STATUS[t.status] || [t.status, "badge-pending"];
    const open = t.status === "pending";
    const mins = open ? Math.max(0, Math.ceil((new Date(t.expires_at) - Date.now()) / 60000)) : 0;
    return `<div class="topup-item">
      <div style="display:flex;align-items:center;gap:10px"><span style="flex:1;font-weight:700">${peso(t.amount_php)}</span>
        <span class="badge ${cls}">${label}</span></div>
      <div class="hint">${esc(METHOD_LABEL[t.method] || t.method)} · ${fmtDate(t.created_at)}${open ? ` · closes in ${mins} min` : ""}</div>
      ${open ? `<div class="topup-actions">
        ${t.checkout_url ? `<a class="btn btn-secondary btn-sm" href="${esc(t.checkout_url)}">Resume payment</a>` : ""}
        <button type="button" class="btn btn-ghost btn-sm" data-cancel-topup="${esc(t.id)}">Cancel</button>
      </div>` : ""}
    </div>`;
  }).join("") : `<p class="muted" style="font-size:14px">No top-ups yet.</p>`;
  el.querySelectorAll("[data-cancel-topup]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    b.textContent = "Canceling…";
    try {
      const r = await api(`/topups/${encodeURIComponent(b.dataset.cancelTopup)}/cancel`, { method: "POST" });
      if (r.status === "credited") {
        toast("That payment had already gone through, so it was added to your balance.");
        await refreshMe().catch(() => {});
      } else toast("Top-up canceled");
    } catch (e) { toast(e.message, { bad: true }); }
    loadTopups();
  }));
  // close the list's open top-ups on time, and keep the countdown fresh
  clearTimeout(topupTimer);
  if (items.some((t) => t.status === "pending")) topupTimer = setTimeout(() => { if (route().name === "funds") loadTopups(); }, 30000);
  return items;
}

let topupTimer;
const TOPUP_STATUS = {
  credited: ["Credited", "badge-completed"],
  pending: ["Awaiting payment", "badge-pending"],
  canceled: ["Canceled", "badge-partial"],
  expired: ["Expired", "badge-partial"],
};

async function pollAfterPayment(topupId) {
  const banner = document.getElementById("funds-banner");
  for (let i = 0; i < 20; i++) {
    if (topupId) {
      // ask the server to confirm with PayMongo directly (doesn't depend on the webhook)
      try { await api(`/topups/${encodeURIComponent(topupId)}/check`, { method: "POST" }); } catch { /* retry next loop */ }
    }
    const items = await loadTopups();
    const t = topupId ? items.find((x) => x.id === topupId) : null;
    if (t && t.status === "credited") {
      await refreshMe().catch(() => {});
      banner.className = "alert alert-ok";
      banner.innerHTML = `${icons.check(20)}<span><strong>${peso(t.amount_php)} added to your balance.</strong> Payment confirmed.</span>`;
      updateFunds();
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
    if (route().name !== "funds") return;
  }
  banner.className = "alert alert-warn";
  banner.textContent = "Still waiting for PayMongo to confirm your payment. It usually takes under a minute. Refresh this page in a bit.";
}

function renderFunds(params) {
  const status = params.get("status");
  const topupId = params.get("topup");
  view.innerHTML = `
    <div class="page-head"><h1>Add funds</h1></div>
    ${status === "success" ? `<div class="alert alert-info" id="funds-banner" role="status">Checking your payment…</div>` : ""}
    ${status === "cancel" ? `<div class="alert alert-warn" role="status">Payment canceled. Nothing was charged.</div>` : ""}
    <div class="two-col">
      <form class="card panel primary" id="funds-form" novalidate>
        <div class="field">
          <label for="amount">Amount</label>
          <div class="amount-row">
            ${PRESETS.map((p) => `<button type="button" class="pill" data-preset="${p}" aria-pressed="false">${peso(p).replace(".00", "")}</button>`).join("")}
            <div class="amount-input"><span class="muted" style="font-weight:600">₱</span><input id="amount" type="number" inputmode="numeric" min="${TOPUP_MIN}" max="${TOPUP_MAX}" value="${esc(state.amount)}"></div>
          </div>
          <span class="hint" id="amt-hint"></span>
        </div>
        <div class="summary">
          <div><span class="muted">You pay</span><span style="font-weight:600" id="sum-pay"></span></div>
          <div><span class="muted">Added to balance</span><span style="font-weight:600" id="sum-add"></span></div>
          <div style="border-top:1px solid var(--line);padding-top:10px"><span class="muted">New balance</span><span style="font-family:var(--font-display);font-weight:700;font-size:20px" id="sum-new"></span></div>
        </div>
        <button class="btn btn-primary btn-lg btn-block" id="pay" type="submit"></button>
        <p class="hint" style="text-align:center">You'll pay by QR Ph on PayMongo's secure checkout: scan it with GCash, Maya or your bank app. On your phone, save the QR image and upload it in your app. Your balance updates as soon as the payment is confirmed, usually within a minute.</p>
      </form>
      <div class="aside">
        <div class="card details"><h3>Recent top-ups</h3><div id="topups"><p class="muted" style="font-size:14px">Loading…</p></div></div>
      </div>
    </div>`;

  view.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    state.amount = b.dataset.preset;
    document.getElementById("amount").value = state.amount;
    updateFunds();
  }));
  document.getElementById("amount").addEventListener("input", (e) => { state.amount = e.target.value; updateFunds(); });
  document.getElementById("funds-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const amt = amountValue();
    if (amt < TOPUP_MIN || amt > TOPUP_MAX || state.paying) return;
    state.paying = true;
    updateFunds();
    try {
      const res = await api("/topups", { method: "POST", body: { amount_php: amt } });
      location.href = res.checkout_url;
    } catch (ex) {
      toast(ex.message, { bad: true });
      state.paying = false;
      updateFunds();
    }
  });
  updateFunds();
  if (status === "success") pollAfterPayment(topupId);
  else loadTopups();
}

/* ----------------------------------------------------------- welcome guide */

// The cheapest way to try the site with the welcome credit: TikTok views (then likes), 1,000 or
// as many as the credit covers, never below the service minimum.
function pickTrial(credit) {
  for (const cat of ["Views", "Likes", null]) {
    let best = null;
    for (const s of state.services) {
      if (s.custom_comments || (cat && (s.platform !== "tiktok" || s.category !== cat))) continue;
      const qty = Math.min(1000, s.max, Math.floor((credit / s.price_per_1k_php) * 1000 / 10) * 10);
      if (qty < s.min || orderCharge(s.price_per_1k_php, qty) > credit) continue;
      if (!best || s.price_per_1k_php < best.s.price_per_1k_php) best = { s, qty };
    }
    if (best) return best;
  }
  return null;
}

function applyTrial(trial) {
  if (!trial) return;
  Object.assign(state, { platform: trial.s.platform, category: trial.s.category || "Other", serviceId: trial.s.id,
                         search: "", quantity: String(trial.qty) });
  if (location.hash !== "#new") location.hash = "#new";
  else renderNew();
}

async function runWelcomeGuide({ credit, preview }) {
  await servicesLoad;
  const trial = pickTrial(credit || 15);
  const creditText = credit ? peso(credit) : "";
  const onPage = (hash) => () => new Promise((r) => {
    if (location.hash === hash) return r();
    location.hash = hash;
    setTimeout(r, 150);
  });
  const nav = (name) => [`.side-nav [data-nav="${name}"]`, `.tabbar [data-nav="${name}"]`];
  const steps = [
    { target: null, title: "Welcome to SMM Shiro!",
      text: credit
        ? `We added <strong>${creditText} free credit</strong> to your balance, so you can try a real order before paying anything. This quick guide shows you how. It takes 30 seconds.`
        : "This quick guide shows you how to place your first order. It takes 30 seconds.",
      next: "Show me", enter: onPage("#new") },
    { target: [".balance-card", ".topbar .bal"], title: "Your balance",
      text: credit ? `Your ${creditText} is already here. Orders are paid from this balance, and refunds come back to it.`
        : "Orders are paid from this balance, and refunds come back to it." },
    { target: ["#order-form .pill-row"], title: "Pick a platform",
      text: trial ? `We picked <strong>${esc(PLATFORMS[trial.s.platform] || trial.s.platform)} ${esc(trial.s.category || "")}</strong> for your free try. You can switch to any platform later.`
        : "Choose the platform you want to grow.",
      enter: () => applyTrial(trial) },
    { target: ["#svc-list .svc-option[aria-pressed=\"true\"]", "#svc-list"], title: "Know what you're buying",
      text: "Every service shows its quality label, price per 1,000, start time, drop risk and refill terms before you pay." },
    { target: ["#link"], title: "Paste your link",
      text: "Use the public link to your post, video or profile. We never need your password." },
    { target: [".charge-box"], title: "Check the charge",
      text: trial ? `${num(trial.qty)} ${esc((trial.s.category || "").toLowerCase())} cost <strong>${peso(orderCharge(trial.s.price_per_1k_php, trial.qty))}</strong>${credit ? ", covered by your free credit" : ""}. Tick the box, then Place order.`
        : "The charge updates as you type. Tick the box, then Place order." },
    { target: [`.side-nav [data-nav="recent"]`, `.tabbar [data-nav="more"]`], title: "See it working",
      text: `<strong>Recently completed</strong> shows orders we just delivered for other customers: what, how many and how fast.${matchMedia("(max-width: 760px)").matches ? " Find it under More." : ""}` },
    { target: nav("orders"), title: "Track delivery",
      text: "Follow progress in Orders. If something isn't fully delivered, the undelivered part is refunded to your balance automatically." },
    { target: nav("funds"), title: "Ready for more?",
      text: "Add funds by QR Ph with GCash, Maya or your bank app, from ₱100. Your balance updates within a minute.",
      next: credit ? "Try my free order" : "Start ordering" },
  ];
  startTour(steps, {
    note: preview ? "Preview: this is what new customers see. Nothing is added to your balance." : "",
    onClose: () => {
      try { localStorage.removeItem("tour"); } catch { /* private mode */ }
      if (route().name === "new") document.getElementById("link")?.scrollIntoView({ block: "center" });
    },
  });
}

/* ----------------------------------------------------------------- boot */

(async () => {
  // start the service list right away, alongside the account check (it's the slow one)
  servicesLoad = api("/services").catch(() => []).then((list) => {
    state.services = list;
    indexServices();
    servicesReady = true;
  });
  try {
    await refreshMe();
  } catch (e) {
    if (e.status === 401) { location.replace("/login/"); return; }
    view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
    return;
  }
  render();   // Orders and Add funds draw now; New order and Mass order draw when the list arrives

  // welcome guide: right after sign-up (the login page leaves a note), or ?tour=preview to see it
  let pending = null;
  try { pending = localStorage.getItem("tour"); } catch { /* private mode */ }
  const preview = new URLSearchParams(location.search).get("tour") === "preview";
  if (preview) runWelcomeGuide({ credit: 15, preview: true });
  else if (pending) runWelcomeGuide({ credit: Number(pending) || 0, preview: false });
})();
