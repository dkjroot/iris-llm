"use strict";

// npm ci --ignore-scripts; npm run check:faces (or build:faces to regenerate)
// The Pi only needs the exported PNGs and manifest.
const { mkdirSync, readFileSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const { createCanvas, Path2D, loadImage } = require("@napi-rs/canvas");
const web = path.join(root, "iris_droid", "web");
const art = ["face-rig.js", "face-stylised.js", "face-portraits.js"]
  .map((name) => readFileSync(path.join(web, name), "utf8")).join("\n");
const { portraits, expressions, portraitWidth, portraitHeight } = vm.runInNewContext(
  art + "\n({ portraits, expressions, portraitWidth, portraitHeight })",
  { Path2D },
);

const scale = 1.6;
const width = Math.round(portraitWidth * scale);
const height = Math.round(portraitHeight * scale);
// The moving eyes, brows and mouths occupy only this part of the portrait.
const crop = [96, 120, 368, 272];
const [left, top, tileWidth, tileHeight] = crop;
const poses = Object.entries(expressions);
const frames = [
  ["rest", {}],
  ["half-blink", { blink: .55 }],
  ["blink", { blink: 1 }],
  ["jaw-small", { jaw: 3 }],
  ["jaw-medium", { jaw: 6 }],
  ["jaw-wide", { jaw: 9 }],
  ["thinking", { gaze: 3.5 }],
];
const args = process.argv.slice(2);
const check = args.includes("--check");
const outputFlag = args.indexOf("--output");
if (args.some((arg, index) => arg !== "--check" && arg !== "--output" && !(outputFlag >= 0 && index === outputFlag + 1)) ||
    (outputFlag >= 0 && (!args[outputFlag + 1] || args[outputFlag + 1].startsWith("--")))) {
  throw new Error("Usage: build_face_sprites.cjs [--check] [--output DIRECTORY]");
}
const output = outputFlag >= 0 ? path.resolve(args[outputFlag + 1]) : path.join(root, "iris_droid", "face_sprites");
if (!check) mkdirSync(output, { recursive: true });
let mismatches = 0;
const comparisons = [];
function publish(name, content) {
  const bytes = Buffer.from(content);
  if (!check) { writeFileSync(path.join(output, name), bytes); return; }
  let existing;
  try { existing = readFileSync(path.join(output, name)); } catch { existing = Buffer.alloc(0); }
  if (bytes.equals(existing)) return;
  comparisons.push((async () => {
    // Native Skia rounds edge coverage slightly differently across platforms.
    // Compare displayed (premultiplied) channels, not invisible RGB at alpha 0.
    let matches = false;
    if (name.endsWith(".png") && existing.length) {
      const images = await Promise.all([loadImage(bytes), loadImage(existing)]);
      if (images[0].width === images[1].width && images[0].height === images[1].height) {
        const pixels = images.map((image) => {
          const canvas = createCanvas(image.width, image.height);
          const context = canvas.getContext("2d");
          context.drawImage(image, 0, 0);
          return context.getImageData(0, 0, image.width, image.height).data;
        });
        let max = 0, total = 0;
        for (let i = 0; i < pixels[0].length; i += 4) {
          const a = pixels[0][i + 3], b = pixels[1][i + 3];
          const differences = [Math.abs(a - b)];
          for (let c = 0; c < 3; c++) differences.push(Math.abs(pixels[0][i+c]*a/255 - pixels[1][i+c]*b/255));
          for (const difference of differences) { max = Math.max(max, difference); total += difference; }
        }
        matches = max <= 4 && total / pixels[0].length <= 0.02;
      }
    }
    if (!matches) { console.error(`Generated asset differs: ${name}`); mismatches += 1; }
  })());
}
const tilts = {};

for (const [name, theme] of Object.entries(portraits)) {
  const atlas = createCanvas(tileWidth * frames.length, height + tileHeight * poses.length);
  const ctx = atlas.getContext("2d");
  ctx.save();
  ctx.scale(scale, scale);
  theme.bust(ctx);
  ctx.restore();
  tilts[name] = {};
  poses.forEach(([expression, pose], row) => {
    tilts[name][expression] = -pose.tilt * (theme.tiltScale ?? 1) * 180 / Math.PI;
    frames.forEach(([, frame], column) => {
      const layer = createCanvas(tileWidth, tileHeight);
      const features = layer.getContext("2d");
      features.translate(-left, -top);
      features.scale(scale, scale);
      theme.features(features, pose, { blink: 0, drift: 0, gaze: 0, jaw: 0, ...frame }, theme);
      ctx.drawImage(layer, tileWidth * column, height + tileHeight * row);
    });
  });
  publish(`${name}.png`, atlas.toBuffer("image/png"));
}
publish("sprites.json", JSON.stringify({
  size: [width, height], crop, expressions: poses.map(([name]) => name),
  frames: frames.map(([name]) => name), tilts,
}, null, 2) + "\n");
console.log(`${check ? "Checked" : "Exported"} ${Object.keys(portraits).length} Iris sprite sheets.`);
Promise.all(comparisons).then(() => { if (mismatches) process.exitCode = 1; })
  .catch((error) => { console.error(error); process.exitCode = 1; });
