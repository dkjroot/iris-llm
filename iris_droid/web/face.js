"use strict";

import { speechEnvelope, envelopeAt, outputTime } from "/speech-envelope.js";

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
let visibleFace = false;
let frame = null;
let lastDraw = null;
const pose = { ...expressions.neutral };
let audioMouth = null;
let localMouth = [];

export function followFaceAudio(audio, buffer, start) {
  const envelope = speechEnvelope(buffer);
  const sample = now => audio.state === "running"
    ? envelopeAt(envelope, outputTime(audio, now) - start) : 0;
  audioMouth = sample;
  requestDraw();
  return () => { if (audioMouth === sample) audioMouth = null; };
}

function localMouthAt(now) {
  let level = 0;
  for (const [at, value] of localMouth) {
    if (at > now) break;
    level = now - at < 150 ? value : 0;
  }
  return level;
}

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

// Nine flecks on a black field. Coordinates scale with the panel.
const flakes = Array.from({ length: 9 }, (_, index) => ({
  x: ((index * 29 + 7) % 101) / 101,
  y: ((index * 53 + 23) % 97) / 97,
  speed: (6 + (index * 3) % 11) * 1.25,
  size: index % 3 === 0 ? 3 : 2,
}));

export function updateFace(state) {
  const now = performance.now();
  localMouth = Array.isArray(state?.mouth) && state.mouth.length <= 128
    ? state.mouth.filter(point => Array.isArray(point) && point.length === 2 &&
      point.every(Number.isFinite) && Math.abs(point[0]) <= 3 && point[1] >= 0 && point[1] <= 1)
      .map(([offset, level]) => [now + offset * 1000, level]) : [];
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
  const nextVisible = face !== null && (state?.showIdle === true || phase !== "idle");
  if (headChanged || phase !== activity || cue !== expression || snowfall !== snow ||
      nextVisible !== visibleFace) {
    if (phase === "idle" || activity === "idle") Object.assign(pose, expressions.neutral);
    activity = phase;
    expression = cue;
    snow = snowfall;
    visibleFace = nextVisible;
    canvas.parentElement.classList.toggle("iris-face-visible", visibleFace);
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
    const motion = faceMotion(seconds, activity, still);
    // Live displays never invent syllables while waiting for audio or in pauses.
    motion.jaw = activity === "speaking" && !still
      ? (audioMouth ? audioMouth(now) : localMouthAt(now)) * 9 : 0;
    motion.round = 0; // Energy does not identify vowel/phoneme shape.
    drawPortrait(ctx, face, bust, pose, activity, motion, seconds);
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
