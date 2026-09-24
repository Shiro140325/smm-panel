// Shared helpers for every page.

// The API and this site are served by the same FastAPI service, so API calls are same-origin.
export const API_BASE = "";

export const PLATFORMS = {
  tiktok: "TikTok", facebook: "Facebook", instagram: "Instagram", youtube: "YouTube",
  x: "X", telegram: "Telegram", whatsapp: "WhatsApp", spotify: "Spotify",
};
export const PLATFORM_ORDER = ["tiktok", "facebook", "instagram", "youtube", "x", "telegram", "whatsapp", "spotify"];

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
  return Math.max(Math.ceil((per1k * qty) / 1000 * 100) / 100, 0.01);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function tierBadge(tier) {
  const t = String(tier || "");
  const cls = /ph/i.test(t) ? "badge-ph" : /hq/i.test(t) ? "badge-hq" : "badge-basic";
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
  if (meta) meta.content = resolvedTheme(mode) === "dark" ? "#0F1115" : "#F6F5F1";
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
