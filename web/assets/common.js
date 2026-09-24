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

export function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-PH", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
