"use strict";
// Catch clipped extreme poses before publishing the cropped native atlas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createCanvas, Path2D } = require('@napi-rs/canvas');
const root = path.join(__dirname, '..');
const art = ['face-rig.js', 'face-stylised.js', 'face-portraits.js']
  .map(file => fs.readFileSync(path.join(root, 'iris_droid/web', file), 'utf8')).join('\n');
const { portraits, expressions } = vm.runInNewContext(art + '\n({portraits, expressions})', {Path2D});
const {size, crop} = JSON.parse(fs.readFileSync(path.join(root, 'iris_droid/face_sprites/sprites.json')));
const [x, y, width, height] = crop;
const canvas = createCanvas(...size), ctx = canvas.getContext('2d');
let checked = 0;
for (const [name, theme] of Object.entries(portraits)) {
  for (const [expression, pose] of Object.entries(expressions)) {
    for (const blink of [0, .55, 1]) {
      ctx.resetTransform(); ctx.clearRect(0, 0, ...size); ctx.scale(1.6, 1.6);
      theme.features(ctx, pose, {blink, drift:0, gaze:3.5, gazeY:-1.5, jaw:9, round:0}, theme);
      const pixels = ctx.getImageData(0, 0, ...size).data;
      let count = 0;
      for (let row = 0; row < size[1]; row++) for (let col = 0; col < size[0]; col++) {
        if (!pixels[(row * size[0] + col) * 4 + 3]) continue;
        count++;
        assert.ok(col >= x && col < x + width && row >= y && row < y + height,
          `${name}/${expression}/${blink} clipped at ${col},${row}`);
      }
      assert.ok(count > 1000, `${name}/${expression} is blank`);
      checked++;
    }
  }
}
console.log(`Checked ${checked} extreme facial poses against the native crop.`);
