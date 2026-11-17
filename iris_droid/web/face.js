"use strict";

const canvas = document.querySelector("canvas");
const ctx = canvas.getContext("2d");
const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
const phases = new Set(["idle", "listening", "thinking", "speaking"]);
const faceNames = Object.keys(portraits);
const faceStorageKey = "iris.face";
let face = null;
let bust = null;
let activity = "idle";
let expression = "neutral";
let snow = false;
let showIdle = false;
let frame = null;
let lastDraw = null;
const pose = { ...expressions.neutral };

export function savedFace() {
  try {
    const saved = localStorage.getItem(faceStorageKey);
    if (faceNames.includes(saved)) return saved;
  } catch {
    // The browser can still switch faces if storage is unavailable.
  }
  return "signal";
}

export function cycleSavedFace(current) {
  const next = faceNames[(faceNames.indexOf(current) + 1) % faceNames.length];
  try {
    localStorage.setItem(faceStorageKey, next);
  } catch {
    // Keep the selection for the current page.
  }
  return next;
}

export function mountFaceCycle(cycle) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "iris-face-cycle";
  button.setAttribute("aria-label", "Next face");
  const smile = document.createElement("span");
  smile.setAttribute("aria-hidden", "true");
  button.append(smile);
  button.addEventListener("click", cycle);
  canvas.parentElement.append(button);
  return button;
}

// Nine slow flecks on a black field. Coordinates scale with the panel.
const flakes = Array.from({ length: 9 }, (_, index) => ({
  x: ((index * 29 + 7) % 101) / 101,
  y: ((index * 53 + 23) % 97) / 97,
  speed: 6 + (index * 3) % 11,
  size: index % 3 === 0 ? 2 : 1,
}));

export function updateFace(state) {
  const head = typeof state?.head === "string" && Object.hasOwn(portraits, state.head)
    ? portraits[state.head] : null;
  const headChanged = head !== face;
  if (headChanged) {
    face = head;
    bust = face ? buildBust(face) : null;
  }
  const phase = phases.has(state?.activity) && face ? state.activity : "idle";
  const cue = typeof state?.expression === "string" && Object.hasOwn(expressions, state.expression)
    ? state.expression : "neutral";
  const snowfall = state?.screensaver === "snow";
  const idleFace = state?.showIdle === true;
  if (headChanged || phase !== activity || cue !== expression || snowfall !== snow ||
      idleFace !== showIdle) {
    if (phase === "idle" || activity === "idle") Object.assign(pose, expressions.neutral);
    activity = phase;
    expression = cue;
    snow = snowfall;
    showIdle = idleFace;
    lastDraw = null;
    requestDraw();
  }
}

function requestDraw() {
  if (frame === null && !document.hidden) frame = requestAnimationFrame(draw);
}

function draw(now) {
  frame = null;
  if (document.hidden) return;
  const visibleFace = face !== null && (showIdle || activity !== "idle");
  const still = motionPreference.matches;
  const interval = 1000 / (activity === "idle" ? 12 : 24);
  const elapsed = lastDraw === null ? interval : now - lastDraw;
  if (!still && elapsed < interval - 2) {
    requestDraw();
    return;
  }
  lastDraw = now;
  const density = Math.min(window.devicePixelRatio || 1, 1.5);
  const width = Math.max(1, Math.round(canvas.clientWidth * density));
  const height = Math.max(1, Math.round(canvas.clientHeight * density));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.resetTransform();
  ctx.clearRect(0, 0, width, height);
  const seconds = now / 1000;

  if (visibleFace) {
    const scale = Math.min(width / portraitWidth, height / portraitHeight);
    ctx.translate((width - portraitWidth * scale) / 2, (height - portraitHeight * scale) / 2);
    ctx.scale(scale, scale);
    const cue = activity === "thinking" ? "focused" : expression;
    if (still) Object.assign(pose, expressions[cue]);
    else blendPose(pose, cue, elapsed / 1000);
    drawPortrait(ctx, face, bust, pose, activity, faceMotion(seconds, activity, still), seconds);
  } else if (snow && !still) {
    ctx.fillStyle = "#8fa7ba38";
    for (const flake of flakes) {
      const x = flake.x * width + Math.sin(seconds * .35 + flake.x * 20) * 4 * density;
      const y = (flake.y * height + seconds * flake.speed * density) % height;
      ctx.fillRect(Math.round(x), Math.round(y), flake.size * density, flake.size * density);
    }
  }
  if (!still && (visibleFace || snow)) requestDraw();
}

new ResizeObserver(requestDraw).observe(canvas);
motionPreference.addEventListener("change", requestDraw);
document.addEventListener("visibilitychange", requestDraw);
