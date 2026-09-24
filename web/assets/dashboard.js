import {
  api, esc, fmtDate, initTheme, num, orderCharge, peso, PLATFORMS, PLATFORM_ORDER, refillText, tierBadge, toast,
} from "./common.js";
import { icons } from "./icons.js";

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

function route() {
  const [name, qs] = location.hash.replace(/^#/, "").split("?");
  return { name: ["new", "mass", "orders", "funds"].includes(name) ? name : "new", params: new URLSearchParams(qs || "") };
}

function render() {
  const { name, params } = route();
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  clearTimeout(state.ordersTimer);
  if (name === "new") renderNew();
  else if (name === "mass") renderMass();
  else if (name === "orders") renderOrders();
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

/* ------------------------------------------------------------ new order */

const selectedService = () => state.services.find((s) => s.id === state.serviceId) || null;

const LIST_LIMIT = 80;              // rows rendered at once; search narrows the rest
const RECOMMENDED = "__featured";
const PHILIPPINES = "__ph";
const isPH = (s) => /\bPH\b/.test(s.tier || "");   // "PH" and "Real · PH" tiers

/** Every word of the query must appear in the service's id, name, description, tier or category. */
function matches(s, q) {
  if (!q) return true;
  const hay = `${s.id} ${s.name} ${s.description || ""} ${s.tier} ${s.category || ""}${isPH(s) ? " philippines" : ""}`.toLowerCase();
  return q.split(/\s+/).every((w) => hay.includes(w));
}

function categoriesFor(platform) {
  const counts = new Map();
  let featured = 0, ph = 0;
  for (const s of state.services) {
    if (s.platform !== platform) continue;
    if (s.featured) featured++;
    if (isPH(s)) ph++;
    const c = s.category || "Other";
    counts.set(c, (counts.get(c) || 0) + 1);
  }
  const cats = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  // Philippines is a cross-category view: its services also stay in their own category
  return [...(featured ? [[RECOMMENDED, featured]] : []), ...(ph ? [[PHILIPPINES, ph]] : []), ...cats];
}

const categoryLabel = (c) => (c === RECOMMENDED ? "Recommended" : c === PHILIPPINES ? "Philippines" : c);

function servicesForPlatform() {
  const q = state.search.trim().toLowerCase();
  return state.services.filter((s) => s.platform === state.platform && (q
    ? matches(s, q)   // searching looks across every category
    : state.category === RECOMMENDED ? s.featured
      : state.category === PHILIPPINES ? isPH(s)
        : (s.category || "Other") === state.category));
}

function svcSub(s) {
  return [s.description, s.start_time, refillText(s.refill_days)].filter(Boolean).map(esc).join(" · ");
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
    <div class="page-head"><h1>New order</h1><a href="mailto:support@smmshiro.com" style="font-weight:600;text-decoration:none">Need help?</a></div>
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
            ${cats.map(([c, n]) => `<option value="${esc(c)}" ${c === state.category ? "selected" : ""}>${esc(categoryLabel(c))} (${num(n)})</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label for="svc-search">Service</label>
          <input class="input" id="svc-search" type="search" placeholder="Search all ${esc(PLATFORMS[state.platform] || "")} services" value="${esc(state.search)}">
          <div class="svc-list" id="svc-list" role="group" aria-label="Services">
            ${shown.length ? shown.map((s) => `
              <button type="button" class="svc-option" data-svc="${s.id}" aria-pressed="${s.id === state.serviceId}">
                <span class="grow"><span class="t">${esc(s.name)}</span><span class="s"><span class="mono">ID ${s.id}</span>${svcSub(s) ? ` · ${svcSub(s)}` : ""}</span></span>
                ${tierBadge(s.tier)}
                <span class="p">${peso(s.price_per_1k_php)}<span class="muted" style="font-weight:500"> /1K</span></span>
              </button>`).join("") : `<div class="empty">No services match "${esc(state.search)}".</div>`}
          </div>
          <span class="hint">${list.length > shown.length
            ? `Showing ${num(shown.length)} of ${num(list.length)}, cheapest first. Search to find the rest.`
            : state.search.trim() ? `${num(list.length)} match${list.length === 1 ? "" : "es"} across all categories.` : ""}</span>
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
  document.getElementById("svc-cat").addEventListener("change", (e) => {
    state.category = e.target.value;
    state.serviceId = servicesForPlatform()[0]?.id ?? null;
    renderNew();
  });
  view.querySelectorAll("[data-svc]").forEach((b) =>
    b.addEventListener("click", () => { state.serviceId = Number(b.dataset.svc); renderNew(); }));
  const search = document.getElementById("svc-search");
  search.addEventListener("input", () => {
    state.search = search.value;
    const hits = servicesForPlatform();
    if (hits.length && !hits.some((x) => x.id === state.serviceId)) state.serviceId = hits[0].id;
    const pos = search.selectionStart;
    renderNew();
    const s2 = document.getElementById("svc-search");
    s2.focus(); s2.setSelectionRange(pos, pos);
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
  const byId = new Map(state.services.map((s) => [s.id, s]));
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
  document.getElementById("id-search").addEventListener("input", (e) => { state.idSearch = e.target.value; renderIdList(); });
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
const FILTERS = [["", "All"], ["pending", "Pending"], ["in_progress", "In progress"], ["completed", "Completed"], ["partial", "Partial"], ["canceled", "Canceled"]];

function refillCell(o) {
  if (o.refill_state === "available") return `<button type="button" class="btn btn-outline-accent" data-refill="${o.id}">Request refill</button>`;
  if (o.refill_state === "requested") return `<span class="muted">Refill requested</span>`;
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
  let orders;
  try {
    orders = await api("/orders" + (qs.toString() ? `?${qs}` : ""));
  } catch (ex) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">${esc(ex.message)}</td></tr>`;
    return null;
  }
  const stamp = document.getElementById("orders-updated");
  if (stamp) stamp.textContent = `Updated ${new Date().toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;
  if (!orders.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">${state.ordersFilter || state.ordersQuery ? "No orders match." : `No orders yet. <a href="#new">Place your first order</a>.`}</td></tr>`;
    return orders;
  }
  tbody.innerHTML = orders.map((o) => {
    const [label, cls] = STATUS[o.status] || [o.status, "badge-pending"];
    return `<tr>
      <td data-col="id" class="mono" style="font-size:13px">#${o.id}</td>
      <td data-col="date" style="font-size:13px;color:var(--muted);white-space:nowrap">${fmtDate(o.created_at)}</td>
      <td data-col="svc"><div class="svc">${esc(o.service_name)} ${tierBadge(o.tier)}</div><div class="link">${esc(o.link)}</div></td>
      <td data-col="qty" class="num">${num(o.quantity)}</td>
      <td data-col="remains" class="num muted">${o.remains == null ? "–" : num(o.remains)}</td>
      <td data-col="charge" class="num" style="font-weight:600">${peso(o.price_php)}</td>
      <td data-col="status"><span class="badge ${cls}">${label}</span></td>
      <td data-col="refill">${refillCell(o)}</td>
    </tr>`;
  }).join("");
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
        <thead><tr><th>ID</th><th>Date</th><th>Service and link</th><th class="num">Qty</th><th class="num">Remains</th><th class="num">Charge</th><th>Status</th><th>Refill</th></tr></thead>
        <tbody id="orders-body"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody>
      </table>
    </div></div>
    <p class="hint"><span id="orders-updated"></span> · Running orders update every 10 seconds. Refill is available after an order completes, for the period shown on the service. Undelivered amounts are refunded to your balance automatically.</p>`;

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
    const credited = t.status === "credited";
    return `<div class="topup-item">
      <div style="display:flex;align-items:center;gap:10px"><span style="flex:1;font-weight:700">${peso(t.amount_php)}</span>
        <span class="badge ${credited ? "badge-completed" : "badge-pending"}">${credited ? "Credited" : "Awaiting payment"}</span></div>
      <div class="hint">${esc(METHOD_LABEL[t.method] || t.method)} · ${fmtDate(t.created_at)}</div>
      ${!credited && t.checkout_url ? `<a href="${esc(t.checkout_url)}" style="font-size:13px;font-weight:600">Resume payment</a>` : ""}
    </div>`;
  }).join("") : `<p class="muted" style="font-size:14px">No top-ups yet.</p>`;
  return items;
}

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
        <p class="hint" style="text-align:center">You'll pick GCash or Maya and pay on PayMongo's secure checkout. Your balance updates as soon as the payment is confirmed, usually within a minute.</p>
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

/* ----------------------------------------------------------------- boot */

(async () => {
  try {
    await refreshMe();
  } catch (e) {
    if (e.status === 401) { location.replace("/login/"); return; }
    view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
    return;
  }
  try {
    state.services = await api("/services");
  } catch { state.services = []; }
  render();
})();
