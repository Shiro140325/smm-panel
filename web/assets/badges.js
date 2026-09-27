// Tier badges (Member, Pro, Elite) and the animated card that introduces a customer's badge.
// Shapes match brand/tiers/*.svg; they use currentColor, so they follow light and dark mode.

import { esc } from "./common.js";

const HEX = "32,5 55.38,18.5 55.38,45.5 32,59 8.62,45.5 8.62,18.5";

// outline and inner mark are separate so the intro can animate them one after the other
const PARTS = {
  member: {
    outline: `<polygon class="bi-outline" pathLength="1" points="${HEX}" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>`,
    mark: `<path class="bi-mark" d="M19 37.5 L32 26 L45 37.5" stroke="currentColor" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>`,
  },
  pro: {
    outline: `<polygon class="bi-outline" pathLength="1" points="${HEX}" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/>`,
    mark: `<polygon class="bi-mark" points="32,19 45,32 32,45 19,32" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>`,
  },
  elite: {
    outline: `<circle class="bi-outline" pathLength="1" cx="32" cy="32" r="27" stroke="currentColor" stroke-width="4" transform="rotate(-90 32 32)"/>`,
    mark: `<path class="bi-mark" fill="currentColor" d="M32,10.4 Q35.6,28.4 53.6,32 Q35.6,35.6 32,53.6 Q28.4,35.6 10.4,32 Q28.4,28.4 32,10.4 Z"/>`,
  },
};

export const TIERS = {
  member: {
    name: "Member", kicker: "Welcome to SMM Shiro", title: "You're a <em>Member</em>",
    perks: ["Order from 6,500+ services, paid in pesos", "Earn 5% when friends you refer top up",
            "Reach <strong>Pro</strong> at ₱10,000 spent for 3% off every order"],
    button: "Let's go",
  },
  pro: {
    name: "Pro", kicker: "New tier unlocked", title: "You're now <em>Pro</em>",
    perks: ["<strong>3% off</strong> every website order", "<strong>6%</strong> referral commission",
            "Yours to keep, forever", "Next: <strong>Elite</strong> at ₱25,000 spent"],
    button: "Nice!",
  },
  elite: {
    name: "Elite", kicker: "Top tier unlocked", title: "You're <em>Elite</em>",
    perks: ["<strong>5% off</strong> every website order", "<strong>7%</strong> referral commission",
            "<strong>+2% bonus</strong> on top-ups of ₱1,000 or more", "Yours to keep, forever"],
    button: "Let's go",
  },
};

/** Static badge, e.g. next to the customer's name: badgeSVG("pro", 20). */
export function badgeSVG(tier, size = 20) {
  const p = PARTS[tier] || PARTS.member;
  return `<svg class="tier-badge" viewBox="0 0 64 64" width="${size}" height="${size}" fill="none" aria-hidden="true">${p.outline}${p.mark}</svg>`;
}

/** The animated introduction card. Resolves when the customer closes it. */
export function showBadgeIntro(tier, { note = "" } = {}) {
  const t = TIERS[tier] ? tier : "member";
  const info = TIERS[t];
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = `bi bi-${t}`;
    const sparks = t === "member" ? "" : `<div class="bi-sparks" aria-hidden="true">${
      Array.from({ length: t === "elite" ? 12 : 8 }, (_, i) => `<i style="--a:${i * (360 / (t === "elite" ? 12 : 8))}deg"></i>`).join("")}</div>`;
    root.innerHTML = `
      <div class="bi-backdrop"></div>
      <div class="bi-card" role="dialog" aria-modal="true" aria-labelledby="bi-title" tabindex="-1">
        ${note ? `<div class="tour-note">${esc(note)}</div>` : ""}
        <div class="bi-stage">
          <div class="bi-glow" aria-hidden="true"></div>
          ${sparks}
          <svg class="bi-badge" viewBox="0 0 64 64" fill="none" role="img" aria-label="${info.name} badge">
            ${PARTS[t].outline}${PARTS[t].mark}
          </svg>
        </div>
        <div class="bi-kicker">${info.kicker}</div>
        <h2 id="bi-title">${info.title}</h2>
        <ul class="bi-perks">${info.perks.map((p, i) => `<li style="--i:${i}"><span>${p}</span></li>`).join("")}</ul>
        <button type="button" class="btn btn-primary btn-lg bi-close">${info.button}</button>
      </div>`;
    // only the button closes it: no tapping outside, no Esc
    const close = () => {
      root.classList.add("bi-out");
      setTimeout(() => { root.remove(); resolve(); }, 220);
    };
    root.querySelector(".bi-close").addEventListener("click", close, { once: true });
    document.body.appendChild(root);
    root.querySelector(".bi-card").focus({ preventScroll: true });
  });
}
