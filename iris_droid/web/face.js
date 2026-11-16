"use strict";

const canvas = document.querySelector("canvas");
const ctx = canvas.getContext("2d");
const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
const phases = new Set(["idle", "listening", "thinking", "speaking"]);
let face = null;
let bust = null;
let activity = "idle";
let expression = "neutral";
let snow = false;
let frame = null;
let lastDraw = null;
const pose = { ...expressions.neutral };

// Nine slow flecks on a black field. Coordinates scale with the panel.
const flakes = Array.from({ length: 9 }, (_, index) => ({
  x: ((index * 29 + 7) % 101) / 101,
  y: ((index * 53 + 23) % 97) / 97,
  speed: 6 + (index * 3) % 11,
  size: index % 3 === 0 ? 2 : 1,
}));

function update(state) {
  const head = typeof state?.head === "string" && Object.hasOwn(portraits, state.head)
    ? portraits[state.head] : null;
  if (head !== face) {
    face = head;
    bust = face ? buildBust(face) : null;
  }
  const phase = phases.has(state?.activity) && face ? state.activity : "idle";
  const cue = typeof state?.expression === "string" && Object.hasOwn(expressions, state.expression)
    ? state.expression : "neutral";
  const snowfall = state?.screensaver === "snow";
  if (phase !== activity || cue !== expression || snowfall !== snow) {
    if (phase === "idle" || activity === "idle") Object.assign(pose, expressions.neutral);
    activity = phase;
    expression = cue;
    snow = snowfall;
    lastDraw = null;
    requestDraw();
  }
}

async function poll() {
  if (!document.hidden) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 1600);
    try {
      const response = await fetch("/state", { signal: abort.signal });
      if (!response.ok) throw new Error("Display state unavailable");
      update(await response.json());
    } catch {
      update(null);
    } finally {
      clearTimeout(timeout);
    }
  }
  setTimeout(poll, document.hidden ? 1000 : 200);
}

function requestDraw() {
  if (frame === null && !document.hidden) frame = requestAnimationFrame(draw);
}

function draw(now) {
  frame = null;
  if (document.hidden) return;
  const active = activity !== "idle" && face !== null;
  const still = motionPreference.matches;
  const interval = 1000 / (active ? 24 : 12);
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

  if (active) {
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
  if (!still && (active || snow)) requestDraw();
}

new ResizeObserver(requestDraw).observe(canvas);
motionPreference.addEventListener("change", requestDraw);
document.addEventListener("visibilitychange", requestDraw);
poll();
