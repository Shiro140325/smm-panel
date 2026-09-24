// Shared helpers for every page.

// The API and this site are served by the same FastAPI service, so API calls are same-origin.
export const API_BASE = "";

export const PLATFORMS = {
  tiktok: "TikTok", facebook: "Facebook", instagram: "Instagram", youtube: "YouTube",
  x: "X", telegram: "Telegram", whatsapp: "WhatsApp", spotify: "Spotify", threads: "Threads",
  shopee: "Shopee", linkedin: "LinkedIn", kick: "Kick", twitch: "Twitch", snapchat: "Snapchat",
  soundcloud: "SoundCloud", discord: "Discord", reddit: "Reddit", pinterest: "Pinterest", other: "Other",
};
export const PLATFORM_ORDER = [
  "tiktok", "facebook", "instagram", "youtube", "x", "telegram", "whatsapp", "spotify", "threads",
  "shopee", "linkedin", "kick", "twitch", "snapchat", "soundcloud", "discord", "reddit", "pinterest", "other",
];

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.", 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    let msg = `Something went wrong (${res.status})`;
    const d = data && data.detail;
    if (typeof d === "string") msg = d;
    else if (Array.isArray(d) && d.length) msg = d.map((e) => String(e.msg || "").replace(/^Value error, /, "")).join(". ");
    throw new ApiError(msg, res.status);
  }
  return data;
}

export function peso(n) {
  return "₱" + Number(n || 0).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function num(n) {
  return n == null ? "–" : Number(n).toLocaleString("en-PH");
}

/** Same rounding as the backend: ceil to the centavo, minimum ₱0.01. */
export function orderCharge(per1k, qty) {
  if (!qty || qty <= 0) return 0;
  return Math.max(Math.ceil(Number(((per1k * qty) / 1000 * 100).toFixed(6))) / 100, 0.01);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}


export function tierBadge(tier) {
  const t = String(tier || "");
  const cls = /non-drop/i.test(t) ? "badge-nd" : /ph/i.test(t) ? "badge-ph" : /hq/i.test(t) ? "badge-hq" : "badge-basic";
  return `<span class="badge ${cls}">${esc(t)}</span>`;
}

export function refillText(days) {
  return days > 0 ? `${days}-day refill` : "No refill";
}

let toastTimer;
export function toast(message, { bad = false } = {}) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

/* ------------------------------------------------------------------ theme
   "system" (default) follows the device and updates live; "light"/"dark" override it.
   The inline <head> script applies a saved override before first paint. */

const THEME_KEY = "theme";
const THEMES = ["system", "light", "dark"];
const THEME_LABEL = { system: "System", light: "Light", dark: "Dark" };
const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function getThemeMode() {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return THEMES.includes(t) ? t : "system";
  } catch { return "system"; }
}

function resolvedTheme(mode) {
  return mode === "system" ? (darkQuery && darkQuery.matches ? "dark" : "light") : mode;
}

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "system") delete root.dataset.theme;
  else root.dataset.theme = mode;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = resolvedTheme(mode) === "dark" ? "#0B0B0B" : "#F4F4F4";
}

export function initTheme(iconSet) {
  const buttons = [...document.querySelectorAll("[data-theme-toggle]")];
  const paint = () => {
    const mode = getThemeMode();
    const next = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
    const icon = mode === "system" ? iconSet.monitor : mode === "light" ? iconSet.sun : iconSet.moon;
    buttons.forEach((b) => {
      b.innerHTML = icon(18);
      const label = `Theme: ${THEME_LABEL[mode]}${mode === "system" ? ` (${resolvedTheme(mode)})` : ""}. Switch to ${THEME_LABEL[next]}`;
      b.setAttribute("aria-label", label);
      b.title = label;
    });
  };
  buttons.forEach((b) => b.addEventListener("click", () => {
    const mode = getThemeMode();
    const next = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
    try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode: still applies for this page */ }
    applyTheme(next);
    paint();
    toast(`Theme: ${THEME_LABEL[next]}${next === "system" ? " (follows your device)" : ""}`);
  }));
  // follow the device live while on System
  darkQuery?.addEventListener?.("change", () => { if (getThemeMode() === "system") { applyTheme("system"); paint(); } });
  applyTheme(getThemeMode());
  paint();
}

export function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-PH", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Replace a native <select> with the site's own dropdown (same look on every device and browser).
 * The native select stays in the DOM, hidden, as the value holder: picking an option sets it and
 * fires its "change" event, so existing handlers keep working. Options may carry data-count.
 */
export function enhanceSelect(sel) {
  if (!sel || sel.dataset.enhanced) return null;
  sel.dataset.enhanced = "1";
  const wrap = document.createElement("div");
  wrap.className = "dd";
  sel.parentNode.insertBefore(wrap, sel);
  wrap.appendChild(sel);
  sel.classList.add("dd-native");
  sel.tabIndex = -1;
  sel.setAttribute("aria-hidden", "true");

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "dd-btn";
  btn.id = `${sel.id}-btn`;
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  const list = document.createElement("ul");
  list.className = "dd-list";
  list.id = `${sel.id}-list`;
  list.setAttribute("role", "listbox");
  list.hidden = true;
  btn.setAttribute("aria-controls", list.id);
  const label = document.querySelector(`label[for="${sel.id}"]`);
  if (label) { label.htmlFor = btn.id; list.setAttribute("aria-labelledby", label.id || (label.id = `${sel.id}-label`)); }
  wrap.append(btn, list);

  const opts = [...sel.options];
  const optText = (o) => esc(o.dataset.label ?? o.textContent);
  const count = (o) => (o.dataset.count ? `<span class="dd-count">${esc(o.dataset.count)}</span>` : "");
  list.innerHTML = opts.map((o, i) => `<li role="option" id="${list.id}-${i}" data-i="${i}" aria-selected="${i === sel.selectedIndex}">
      <span class="dd-text">${optText(o)}</span>${count(o)}<svg class="dd-tick" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></li>`).join("");
  const items = [...list.children];
  let active = -1;

  const sync = () => {
    const o = sel.options[sel.selectedIndex];
    btn.innerHTML = `<span class="dd-text">${o ? optText(o) : ""}</span>${o ? count(o) : ""}<svg class="dd-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
    btn.disabled = sel.disabled;
  };
  const setActive = (i) => {
    items[active]?.classList.remove("active");
    active = Math.max(0, Math.min(items.length - 1, i));
    items[active]?.classList.add("active");
    btn.setAttribute("aria-activedescendant", items[active]?.id || "");
    items[active]?.scrollIntoView({ block: "nearest" });
  };
  const onOutside = (e) => { if (!wrap.contains(e.target)) close(); };
  function open() {
    if (btn.disabled || !list.hidden) return;
    const r = btn.getBoundingClientRect();
    wrap.classList.toggle("dd-up", window.innerHeight - r.bottom < 260 && r.top > window.innerHeight - r.bottom);
    list.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    setActive(sel.selectedIndex);
    document.addEventListener("pointerdown", onOutside, true);
  }
  function close() {
    if (list.hidden) return;
    list.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    btn.removeAttribute("aria-activedescendant");
    document.removeEventListener("pointerdown", onOutside, true);
  }
  function choose(i) {
    close();
    if (i === sel.selectedIndex) return;
    sel.selectedIndex = i;
    sync();
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }

  btn.addEventListener("click", () => (list.hidden ? open() : close()));
  list.addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (li) choose(Number(li.dataset.i)); });
  list.addEventListener("pointermove", (e) => { const li = e.target.closest("[data-i]"); if (li && Number(li.dataset.i) !== active) setActive(Number(li.dataset.i)); });
  let typed = "", typedAt = 0;
  btn.addEventListener("keydown", (e) => {
    const isOpen = !list.hidden;
    if (["ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      if (!isOpen) open(); else setActive(active + (e.key === "ArrowDown" ? 1 : -1));
    } else if (e.key === "Home" || e.key === "End") {
      if (isOpen) { e.preventDefault(); setActive(e.key === "Home" ? 0 : items.length - 1); }
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (isOpen) choose(active); else open();
    } else if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); close(); }
    } else if (e.key === "Tab") {
      close();
    } else if (e.key.length === 1 && /\S/.test(e.key)) {   // type to jump
      const now = Date.now();
      typed = now - typedAt > 700 ? e.key.toLowerCase() : typed + e.key.toLowerCase();
      typedAt = now;
      const hit = opts.findIndex((o) => (o.dataset.label ?? o.textContent).toLowerCase().startsWith(typed));
      if (hit >= 0) { if (!isOpen) open(); setActive(hit); }
    }
  });
  sync();
  return { btn, sync, close };
}
