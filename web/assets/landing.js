import { api, esc, initTheme, rememberRef, showAnnouncement, num, peso, PLATFORMS, PLATFORM_ORDER, refillText, tierBadge } from "./common.js";
import { icons } from "./icons.js";

document.getElementById("warn-icon").innerHTML = icons.warn(24);
initTheme(icons);
rememberRef();
showAnnouncement();

let services = [];   // hand-picked: the preview card
let table = [];      // whole catalog, up to 65 per platform, followers first then cheapest (server-sorted)
let current = "tiktok";

function renderTabs(platforms) {
  const el = document.getElementById("price-tabs");
  el.innerHTML = platforms
    .map((p) => `<button type="button" class="pill" data-p="${p}" aria-pressed="${p === current}">${esc(PLATFORMS[p] || p)}</button>`)
    .join("");
  el.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      current = b.dataset.p;
      renderTabs(platforms);
      renderTable();
    }),
  );
}

function renderTable() {
  const rows = table.filter((s) => s.platform === current);
  document.getElementById("price-body").innerHTML = rows.length
    ? rows
        .map(
          (s) => `<tr>
            <td class="mono muted">${s.id}</td>
            <td style="font-weight:600">${esc(s.name)}</td>
            <td>${tierBadge(s.tier)}</td>
            <td class="num" style="font-weight:700">${peso(s.price_per_1k_php)}</td>
            <td class="muted">${num(s.min)} / ${num(s.max)}</td>
            <td class="muted">${refillText(s.refill_days)}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="6" class="muted">No services yet.</td></tr>`;
}

function renderHero() {
  // one per tier where possible, from the most popular platforms
  const picks = [];
  const want = [
    (s) => s.platform === "tiktok" && /follower/i.test(s.name) && /basic/i.test(s.tier),
    (s) => s.platform === "tiktok" && /follower/i.test(s.name) && /hq/i.test(s.tier),
    (s) => s.platform === "facebook" && /ph/i.test(s.tier) && /post likes/i.test(s.name),
    (s) => s.platform === "tiktok" && /views/i.test(s.name) && /hq/i.test(s.tier),
  ];
  for (const w of want) {
    const s = services.find((x) => w(x) && !picks.includes(x));
    if (s) picks.push(s);
  }
  const el = document.getElementById("hero-card");
  if (!picks.length) { el.innerHTML = ""; return; }
  el.innerHTML = picks
    .map(
      (s) => `<div class="svc-row">
        <div class="grow"><div class="t">${esc(s.name)}</div><div class="s">${esc(s.start_time || "")}${s.start_time ? " · " : ""}${refillText(s.refill_days)}</div></div>
        ${tierBadge(s.tier)}
        <div class="p">${peso(s.price_per_1k_php)}<span class="muted" style="font-weight:500"> /1K</span></div>
      </div>`,
    )
    .join("");
}

(async () => {
  try {
    [table, services] = await Promise.all([api("/services/top"), api("/services?featured=1").catch(() => [])]);
  } catch (e) {
    document.getElementById("price-body").innerHTML = `<tr><td colspan="6" class="muted">Couldn't load prices right now.</td></tr>`;
    document.getElementById("hero-card").innerHTML = "";
    return;
  }
  const platforms = PLATFORM_ORDER.filter((p) => table.some((s) => s.platform === p));
  if (!platforms.includes(current)) current = platforms[0];
  renderTabs(platforms);
  renderTable();
  renderHero();
  try {
    const { total } = await api("/services/count");
    if (total > table.length) document.getElementById("service-count").textContent = `all ${num(total)} services`;
  } catch { /* keep the generic wording */ }
})();
