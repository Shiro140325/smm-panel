import { api, esc, fmtDate, initTheme, num, toast } from "./common.js";
import { icons } from "./icons.js";

/* The Messenger bot's own panel (botfb.smmshiro.com), unlocked with a 6-digit PIN.
   Calls are relative ("api/..."), so it works at botfb.smmshiro.com/ and at /botfb/ alike. */

initTheme(icons);
document.querySelectorAll("[data-logout]").forEach((b) => {
  b.innerHTML = icons.logout(20);
  b.addEventListener("click", async () => {
    try { await api("api/logout", { method: "POST" }); } catch { /* ignore */ }
    location.reload();
  });
});

const view = document.getElementById("view");
const $ = (id) => document.getElementById(id);
const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function call(path, opts) {
  try { return await api(path, opts); } catch (e) {
    if (e.status === 401) showLock("Your session ended. Enter your PIN.");
    throw e;
  }
}

/* ------------------------------------------------------------ PIN */

function showLock(notice = "", needsPassword = false) {
  $("panel").classList.add("hidden");
  $("lock").classList.remove("hidden");
  $("lock-theme").classList.remove("hidden");
  const err = $("lock-error");
  const paint = (pw) => {
    $("pin-field").classList.toggle("hidden", pw);
    $("pw-field").classList.toggle("hidden", !pw);
    $("lock-sub").textContent = pw ? "3 wrong PINs. Enter the password of your SMM Shiro account to open the panel." : "Enter your 6-digit PIN";
    (pw ? $("lock-pw") : $("lock-pin")).focus();
  };
  const fail = (msg) => { err.textContent = msg; err.classList.remove("hidden"); };
  err.classList.add("hidden");
  if (notice) fail(notice);
  let pw = needsPassword;
  paint(pw);
  const submit = async () => {
    const btn = $("lock-go");
    btn.disabled = true;
    try {
      if (pw) await api("api/unlock", { method: "POST", body: { password: $("lock-pw").value } });
      else await api("api/login", { method: "POST", body: { pin: $("lock-pin").value } });
      $("lock-pin").value = ""; $("lock-pw").value = "";
      showPanel();
    } catch (e) {
      if (e.status === 423) { pw = true; paint(true); }
      $("lock-pin").value = "";
      fail(e.message);
      (pw ? $("lock-pw") : $("lock-pin")).focus();
    }
    btn.disabled = false;
  };
  $("lock-form").onsubmit = (ev) => { ev.preventDefault(); submit(); };
  $("lock-pin").oninput = () => {
    $("lock-pin").value = $("lock-pin").value.replace(/\D/g, "").slice(0, 6);
    if ($("lock-pin").value.length === 6) submit();
  };
}

function showPanel() {
  $("lock").classList.add("hidden");
  $("lock-theme").classList.add("hidden");
  $("panel").classList.remove("hidden");
  renderBot();
}

async function start() {
  let st;
  try { st = await api("api/state"); } catch (e) { showLock(e.message); return; }
  if (!st.ready) { showLock("The PIN isn't set up yet."); return; }
  try { await api("api/bot/menu"); showPanel(); } catch { showLock("", st.needs_password); }
}

/* ------------------------------------------------------------ the panel */

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
  view.innerHTML = `<div id="bot"><div class="card empty">Loading…</div></div>`;
  let d;
  try { d = await call("api/bot"); } catch (e) { $("bot").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
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
          <h3>Conversations <span class="muted" style="font-weight:500">· ${num(st.chats)} on Messenger${st.muted ? `, ${num(st.muted)} with AI off` : ""}</span></h3>
          <p class="hint" style="margin:0">Switch AI off for a customer to reply to them yourself. Their messages are still saved here.</p>
          <div id="bot-chats">${d.chats.length ? d.chats.map((c) => `
            <div class="bot-chat" data-chat="${c.id}">
              <div class="grow"><strong>${esc(c.name || "Messenger user")}</strong> ${c.muted_at ? (c.mute_reason === "owner" ? `<span class="badge badge-canceled">AI off</span>` : `<span class="badge badge-failed">Silenced: spam</span>`) : ""}
                <div class="hint" style="margin:2px 0 0">${c.last_message_at ? fmtDate(c.last_message_at) : ""} · ${num(c.orders)} order${c.orders === 1 ? "" : "s"} · ${esc((c.last_text || "").slice(0, 70))}</div></div>
              <label class="ai-switch" title="AI replies for this customer"><input type="checkbox" role="switch" data-ai="${c.id}" ${c.muted_at ? "" : "checked"}><span class="track" aria-hidden="true"></span><span class="lbl">AI</span></label>
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
          <div class="field"><label for="bot-notes">Custom instructions</label>
            <textarea class="textarea" id="bot-notes" rows="7" maxlength="1500" placeholder="How the bot should talk, and facts it can use to answer questions, e.g.&#10;- Always call the customer po/opo.&#10;- Orders usually start within 1 hour.&#10;- Undelivered amounts become credit.&#10;- We don't need passwords, only the public link.">${esc(s.notes)}</textarea>
            <span class="hint" id="bot-notes-count"></span></div>
          <button type="button" class="btn btn-primary" id="bot-save">Save</button>
          <p class="hint" style="margin:0">The bot follows these on every reply. Prices, services and payment always come from your bot menu, so instructions can't change those. They're sent with every reply, so shorter costs less.</p>
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
    try { menuItems = await call("api/bot/menu"); } catch (e) { $("menu-list").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
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
      try { await call(`api/bot/menu/${b.dataset.mdel}`, { method: "DELETE" }); toast("Removed from the menu"); } catch (e) { toast(e.message, { bad: true }); }
      loadMenu();
    }));
  };
  const lookup = debounce(async () => {
    const id = Number($("mf-sid").value), per = Number($("mf-per").value) || 1000;
    if (!id) { $("mf-info").textContent = ""; return; }
    try {
      const g = await call(`api/bot/lookup?id=${id}&per_qty=${per}`);
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
      await call(editing ? `api/bot/menu/${editing}` : "api/bot/menu", { method: editing ? "PUT" : "POST", body });
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
    try { await call("api/bot", { method: "PUT", body: { model: $("bot-model").value.trim(), notes: $("bot-notes").value } }); toast("Saved"); }
    catch (e) { toast(e.message, { bad: true }); }
  };
  $("bot-on").onchange = async (ev) => {
    try { await call("api/bot", { method: "PUT", body: { enabled: ev.target.checked } }); toast(ev.target.checked ? "The bot now replies on Messenger" : "Bot replies are off"); }
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
      const r = await call("api/bot/test", { method: "POST", body: { session: botSessionId(), text } });
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
    try { await call("api/bot/test/reset", { method: "POST", body: { session: botSessionId() } }); } catch { /* ignore */ }
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
      const r = await call(`api/bot/chats/${b.dataset.open}`);
      box.innerHTML = r.messages.map((m) => `<div class="bot-msg ${m.direction}"><div class="bubble">${esc(m.text).replace(/\n/g, "<br>")}</div>
        <div class="bot-meta">${fmtDate(m.created_at)}</div></div>`).join("") || `<p class="hint">No messages.</p>`;
      box.classList.remove("hidden");
      b.textContent = "Hide";
    } catch (e) { toast(e.message, { bad: true }); }
  }));
  view.querySelectorAll("[data-ai]").forEach((box) => box.addEventListener("change", async () => {
    const on = box.checked;
    box.disabled = true;
    try {
      await call(`api/bot/chats/${box.dataset.ai}/ai`, { method: "POST", body: { on } });
      toast(on ? "AI is on for this customer" : "AI is off for this customer: reply to them yourself in the Page inbox");
      renderBot();
    } catch (e) { box.checked = !on; box.disabled = false; toast(e.message, { bad: true }); }
  }));
}

start();
