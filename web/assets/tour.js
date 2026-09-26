// Step-by-step guide: dims the page, spotlights one element at a time and explains it.
// A step is { target: [selectors] | null, title, text, enter?(): Promise|void, next?: label }.
// The first visible match of `target` is spotlighted; no target shows a centered card.

import { esc } from "./common.js";

let active = null;

export function tourActive() { return !!active; }

export function startTour(steps, { note = "", onClose } = {}) {
  endTour();
  const root = document.createElement("div");
  root.className = "tour";
  root.innerHTML = `
    <div class="tour-block" aria-hidden="true"></div>
    <div class="tour-hole" aria-hidden="true"></div>
    <div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" tabindex="-1"></div>`;
  document.body.appendChild(root);
  const hole = root.querySelector(".tour-hole");
  const card = root.querySelector(".tour-card");
  let i = 0, target = null, busy = false;

  const visible = (el) => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const find = (sels) => (sels || []).map((s) => document.querySelector(s)).find(visible) || null;

  function place() {
    const vw = innerWidth, vh = innerHeight, pad = 8;
    if (!target) {
      hole.style.cssText = `top:${vh / 2}px;left:${vw / 2}px;width:0;height:0`;
      card.classList.add("centered");
      card.style.top = card.style.left = "";
      return;
    }
    card.classList.remove("centered");
    const r = target.getBoundingClientRect();
    hole.style.cssText = `top:${r.top - pad}px;left:${r.left - pad}px;width:${r.width + pad * 2}px;height:${r.height + pad * 2}px`;
    const cw = card.offsetWidth, ch = card.offsetHeight, gap = 16, m = 16;
    const below = r.bottom + pad + gap, above = r.top - pad - gap - ch;
    let top = below + ch <= vh - m ? below : above >= m ? above : Math.max(m, vh - ch - m);
    let left = Math.min(Math.max(m, r.left + r.width / 2 - cw / 2), vw - cw - m);
    card.style.top = `${top}px`;
    card.style.left = `${left}px`;
  }

  async function show(n) {
    if (busy) return;
    busy = true;
    i = n;
    const step = steps[i];
    try { await step.enter?.(); } catch { /* a step that can't prepare still shows its text */ }
    // wait (briefly) for the page to draw the element this step points at
    target = null;
    for (let t = 0; step.target && !target && t < 40; t++) {
      target = find(step.target);
      if (!target) await new Promise((r) => setTimeout(r, 50));
    }
    if (target) {
      target.scrollIntoView({ block: "center", behavior: "instant" });
      await new Promise((r) => requestAnimationFrame(r));
    }
    const last = i === steps.length - 1;
    card.innerHTML = `
      ${note ? `<div class="tour-note">${esc(note)}</div>` : ""}
      <div class="tour-step">${i + 1} of ${steps.length}</div>
      <h3 id="tour-title">${step.title}</h3>
      <p>${step.text}</p>
      <div class="tour-actions">
        ${last ? "" : `<button type="button" class="btn btn-ghost tour-skip">Skip</button>`}
        <span class="grow"></span>
        ${i > 0 ? `<button type="button" class="btn btn-secondary tour-back">Back</button>` : ""}
        <button type="button" class="btn btn-primary tour-next">${esc(step.next || (last ? "Done" : "Next"))}</button>
      </div>`;
    card.querySelector(".tour-skip")?.addEventListener("click", () => endTour());
    card.querySelector(".tour-back")?.addEventListener("click", () => show(i - 1));
    card.querySelector(".tour-next").addEventListener("click", () => (last ? endTour(true) : show(i + 1)));
    place();
    card.focus({ preventScroll: true });
    busy = false;
  }

  const onKey = (e) => {
    if (e.key === "Escape") endTour();
    else if (e.key === "ArrowRight" && !busy) card.querySelector(".tour-next")?.click();
    else if (e.key === "ArrowLeft" && !busy && i > 0) show(i - 1);
  };
  const onMove = () => { if (!busy) place(); };
  addEventListener("keydown", onKey);
  addEventListener("resize", onMove);
  addEventListener("scroll", onMove, true);
  active = { root, cleanup: () => {
    removeEventListener("keydown", onKey);
    removeEventListener("resize", onMove);
    removeEventListener("scroll", onMove, true);
  }, onClose, steps };
  show(0);
}

export function endTour(finished = false) {
  if (!active) return;
  const { root, cleanup, onClose, steps } = active;
  active = null;
  cleanup();
  root.remove();
  onClose?.(finished, steps);
}
