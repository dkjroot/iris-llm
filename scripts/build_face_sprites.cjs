"use strict";

// npm install --prefix .cache/face-art-build --ignore-scripts @napi-rs/canvas@0.1.100
// The Pi only needs the exported PNGs and manifest.
const { mkdirSync, readFileSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const { createCanvas, Path2D } = require(path.join(
  root, ".cache", "face-art-build", "node_modules", "@napi-rs", "canvas",
));
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
const output = path.join(root, "iris_droid", "face_sprites");
mkdirSync(output, { recursive: true });
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
  writeFileSync(path.join(output, `${name}.png`), atlas.toBuffer("image/png"));
}
writeFileSync(path.join(output, "sprites.json"), JSON.stringify({
  size: [width, height], crop, expressions: poses.map(([name]) => name),
  frames: frames.map(([name]) => name), tilts,
}, null, 2) + "\n");
console.log(`Exported ${Object.keys(portraits).length} Iris sprite sheets.`);
