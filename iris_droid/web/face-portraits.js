"use strict";

const portraitWidth = 350;
const portraitHeight = 335;
const eyeY = 137;

function paint(ctx, path, fill, stroke, lineWidth = 1) {
  const shape = new Path2D(path);
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill(shape);
  }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lineWidth;
    ctx.stroke(shape);
  }
}

function shade(ctx, x1, y1, x2, y2, ...colors) {
  const gradient = ctx.createLinearGradient(x1, y1, x2, y2);
  colors.forEach((color, i) => gradient.addColorStop(i / (colors.length - 1), color));
  return gradient;
}

function disc(ctx, x, y, radius, color, stroke) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, 2 * Math.PI);
  ctx.fillStyle = color;
  ctx.fill();
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

const collar = "M0 334 L32 297 104 276 143 250 149 210 207 209 214 253 249 275 319 298 350 334Z";
const vesperNeck = "M148 206 L204 206 212 255 194 278 169 274 144 251Z";
const vesperEars = "M106 128 L96 122 94 139 104 165 118 173 M244 126 L254 121 258 141 250 162 235 173";
const vesperEarFolds = "M106 145 L113 156 110 165 M249 130 L244 150 245 158";
const vesperHair = "M100 143 L91 117 92 81 105 58 134 39 171 28 217 33 244 53 262 80 261 109 247 147 236 154 238 117 222 99 207 76 183 71 158 88 118 102 105 126 111 153Z";
const vesperSweep = "M97 81 L125 64 161 53 194 42 225 45 248 63 212 58 177 67 146 82 115 97 103 113Z";
const vesperCrown = "M116 58 L160 41 198 35 229 46 213 48 180 46 143 62 110 80Z";
const vesperStrands = "M96 109 L122 85 160 73 186 58 218 57 243 77 257 99 M101 83 L125 70 148 65 M210 68 L233 82 252 113 M245 120 L248 106";

function scanLines(ctx, outline, top, bottom, color) {
  ctx.save();
  ctx.clip(new Path2D(outline));
  for (let y = top; y < bottom; y += 6) {
    paint(ctx, `M0 ${y} H${portraitWidth}`, null, color, (y - top) % 12 === 0 ? 1.1 : 0.6);
  }
  ctx.restore();
}

function vesperProjection(ctx) {
  const outline = "M108 85 L133 57 176 47 220 60 245 89 248 136 Q246 167 235 193 Q224 214 199 228 Q179 239 158 230 Q132 220 119 199 Q107 177 102 136Z";
  paint(ctx, collar, shade(ctx, 169, 245, 177, 335, "#396477d9", "#1c3b50c9", "#15334818"));
  paint(ctx, "M4 331 L32 297 104 276 143 250 M214 253 L249 275 319 298 347 331",
    null, shade(ctx, 173, 255, 179, 335, "#98dce0b0", "#477f9a37"));
  scanLines(ctx, collar, 236, 330, "#8de5e015");
  paint(ctx, vesperNeck, shade(ctx, 148, 213, 209, 279, "#5d9bb5", "#386480", "#2d4d6c"), "#81c6d685");
  paint(ctx, "M56 308 L113 288 149 312 M293 308 L239 289 202 312 M140 261 L174 285 212 261",
    null, "#8bced580");
  paint(ctx, vesperEars, "#6491a8", "#a0dbe3aa");
  paint(ctx, outline, "#77a6ba", "#a5dde6b0");
  paint(ctx, "M173 149 Q172 158 168 164 M168 169 Q172 173 177 171 Q182 173 187 169",
    null, "#456c8990", 1.25);
  scanLines(ctx, outline, 53, 234, "#18395542");
  paint(ctx, vesperHair,
    shade(ctx, 117, 45, 243, 150, "#5689a3", "#365573", "#20384f"), "#96d8e5cc", 1.15);
  paint(ctx, vesperSweep, "#78c2d333");
  paint(ctx, vesperCrown, "#a0d9e132");
  scanLines(ctx, vesperHair, 29, 154, "#90d5e638");
  paint(ctx, vesperStrands, null, "#99dfe0a0", 1.05);
  paint(ctx, vesperEarFolds, null, "#a1e4e891", 1.15);
}

const portraits = {
  "humanoid-android": {
    bust: vesperProjection, features: androidFeatures,
    eyeWidth: 16.5, eyeHeight: 6.3, irisRadius: 6.9,
    brow: "#315271", browLight: "#a7dee9a0", socket: "#244a6b26",
    sclera: "#a8c8d6", iris: "#456e7a", pupil: "#284555", eyeLine: "#2b4b6e", eyeGlint: "#cee1e3",
    mouthWidth: 24, mouthHeight: 1.55, mouthRest: 1.9, lip: "#556a8d", lipLight: "#9bb8cf9c",
    mouth: "#354f69", accent: "#a2ffe8",
  },
  sprout: {
    bust: alienBust, features: roundedFeatures, tiltScale: 1.36, accent: "#bbdc9d",
    eyeSpacing: 39, eyeY: 142, eyeWidth: 23, eyeHeight: 17, irisWidth: 10.8,
    brow: "#487472", sclera: "#fff3db", eyeLine: "#41716c", iris: "#aa8757", pupil: "#4a4144",
    mouthY: 201, mouthWidth: 31, mouth: "#485456", faceLine: "#376763", mouthLight: "#cb8790",
  },
  signal: {
    bust: signalBust, features: signalFeatures, tiltScale: .55, accent: "#dce6a7",
  },
  rivet: {
    bust: robotBust, features: roundedFeatures, tiltScale: 1.15, accent: "#e9ba91",
    eyeSpacing: 39, eyeY: 149, eyeWidth: 20.5, eyeHeight: 15.5, irisWidth: 10.4,
    brow: "#637986", sclera: "#f7f1de", eyeLine: "#728996", iris: "#74a6ae", pupil: "#3c5f74",
    mouthY: 199, mouthWidth: 28, mouth: "#344f5e", faceLine: "#75878c", mouthLight: "#be8a75",
  },
};

// Build the static artwork once per concept. Every animation frame only adds
// the eyes, eyebrows and mouth, with a small transform on the finished bust.
function buildBust(theme) {
  const bust = document.createElement("canvas");
  bust.width = portraitWidth * 2;
  bust.height = portraitHeight * 2;
  const ctx = bust.getContext("2d");
  ctx.scale(2, 2);
  theme.bust(ctx);
  return bust;
}

function drawEye(ctx, theme, x, side, pose, motion) {
  const w = theme.eyeWidth;
  const irisRadius = theme.irisRadius ?? 5.9;
  const lid = pose.lid + Math.max(0, pose.inner - 1) * .012;
  const opening = Math.max(0.24, lid * (1 - 0.96 * motion.blink));
  const h = theme.eyeHeight * opening;
  const irisX = pose.gaze + motion.gaze;
  const innerBrow = -side * 1.25;
  ctx.save();
  ctx.translate(x, eyeY);
  paint(ctx, `M${-w - 6} 0 Q${-w} -12 2 -11 Q${w} -10 ${w + 6} 0 Q3 10 ${-w - 6} 0Z`, theme.socket);
  const curve = `M${-w} 0 Q${-w * .36} ${-h * 2} ${w * .4} ${-h} Q${w * .72} ${-h * .7} ${w} 0 Q${w * .25} ${h * 1.6} ${-w} 0Z`;
  paint(ctx, curve, theme.sclera, theme.eyeLine, 1.6);
  ctx.save();
  ctx.clip(new Path2D(curve));
  disc(ctx, irisX, -0.6, irisRadius, theme.iris, theme.eyeLine);
  disc(ctx, irisX, -0.6, irisRadius * (2.7 / 5.9), theme.pupil);
  disc(ctx, irisX - 1.9, -2.7, 1.3, theme.eyeGlint ?? "#fff3eb");
  ctx.restore();

  // Draw brows in mirrored coordinates so the inner ends can move together.
  ctx.scale(-side, 1);
  const browY = -16.5 + pose.brow + innerBrow;
  const tip = browY + (pose.inner > 0 ? pose.inner * .5 : pose.inner);
  const arch = browY - (side === -1 ? 5.5 : 4);
  paint(ctx, `M${-w - 3} ${browY + 2} Q${-w * .35} ${arch} ${w + 2} ${tip - 1} L${w + 3} ${tip + 2} Q-4 ${arch + 5} ${-w - 3} ${browY + 2}Z`, theme.brow);
  paint(ctx, `M${-w + 1} ${browY + 0.8} Q-5 ${arch + 1} ${w - 1} ${tip - 0.3}`, null, theme.browLight, 0.8);
  ctx.restore();
}

function drawMouth(ctx, theme, pose, jaw) {
  const w = (theme.mouthWidth ?? 17) * pose.width - jaw * 0.3;
  const smile = pose.smile > 0 ? pose.smile * .72 : pose.smile;
  const left = -smile * 0.73;
  const right = -smile;
  const mid = jaw * 0.18;
  const upper = mid - (theme.mouthRest ?? 0);
  ctx.save();
  ctx.translate(177, 193);
  ctx.scale(1, theme.mouthHeight ?? 1);
  paint(ctx, `M${-w} ${left} Q-7 -5 -2 -3 L1 -3 Q6 -5 ${w} ${right} Q6 ${7 + jaw} 0 ${5 + jaw} Q-9 ${6 + jaw * .7} ${-w} ${left}Z`, theme.lip);
  paint(ctx, `M${-w} ${left} Q-6 ${upper + 1} 0 ${upper} Q7 ${upper + 1.5} ${w} ${right} Q5 ${mid + 4 + jaw} 0 ${mid + 2 + jaw} Q-6 ${mid + 3 + jaw} ${-w} ${left}Z`, theme.mouth);
  paint(ctx, `M-7 ${5 + jaw} Q0 ${7 + jaw} 8 ${5 + jaw}`, null, theme.lipLight, 0.95);
  if (jaw > 4) {
    paint(ctx, `M-7 ${mid + 1.7} Q0 ${mid + 2.2} 7 ${mid + 1.7}`, null, "#f0d5d0a0", 1.2);
  }
  ctx.restore();
}

function androidFeatures(ctx, pose, motion, theme) {
  drawEye(ctx, theme, 142, -1, pose, motion);
  drawEye(ctx, theme, 212, 1, pose, motion);
  drawMouth(ctx, theme, pose, motion.jaw);
}

function drawPortrait(ctx, theme, bust, pose, activity, motion, seconds) {
  ctx.save();
  const drift = activity === "idle" ? 1.6 : 0.75;
  ctx.translate(175, 186 + motion.drift * drift);
  ctx.rotate(pose.tilt * (theme.tiltScale ?? 1) + motion.drift * 0.004);
  ctx.translate(-175, -186);
  ctx.drawImage(bust, 0, 0, portraitWidth, portraitHeight);
  theme.features(ctx, pose, motion, theme);
  ctx.restore();

  if (activity !== "idle") {
    const pulse = activity === "speaking" ? motion.jaw / 9 : (Math.cos(seconds * 3) + 1) / 2;
    ctx.globalAlpha = 0.45 + 0.45 * pulse;
    paint(ctx, "M151 317 H165 L170 313 H181 L187 317 H200", null, theme.accent, 1.4);
    ctx.globalAlpha = 1;
  }
}
