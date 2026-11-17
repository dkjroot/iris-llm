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

// All material lighting is painted once into the cached bust. No live blur,
// shaders, textures or network assets are needed on the display.
function radial(ctx, x, y, radius, ...colors) {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
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

function vesperProjection(ctx) {
  const outline = "M108 85 Q128 49 176 47 Q222 48 245 89 L248 136 Q246 174 232 198 Q217 224 191 234 Q175 240 157 231 Q128 217 116 193 Q104 169 102 136Z";
  paint(ctx, collar, shade(ctx, 90, 260, 243, 340, "#426b84", "#193347", "#081322"));
  paint(ctx, "M4 331 L32 297 104 276 143 250 M214 253 L249 275 319 298 347 331", null, "#8ee8f490", 1.4);
  paint(ctx, vesperNeck, shade(ctx, 148, 211, 208, 268, "#203a57", "#7cabbf", "#29435e"));
  paint(ctx, "M141 256 Q176 286 214 256 L208 274 Q176 303 145 274Z", "#142739", "#6696ad", 1.4);
  paint(ctx, "M56 308 L113 288 149 312 M293 308 L239 289 202 312", null, "#699ab380", 1.2);
  paint(ctx, vesperEars, shade(ctx, 98, 125, 249, 166, "#92c5d4", "#426584"), "#87c9db", 1.3);
  paint(ctx, outline, radial(ctx, 146, 112, 147, "#dcf0ec", "#91b9ce", "#3c587c"), "#99d0df", 1.2);
  ctx.save();
  ctx.clip(new Path2D(outline));
  oval(ctx, 118, 169, 30, 65, radial(ctx, 103, 156, 67, "#32c8e95c", "#44b1da00"));
  oval(ctx, 220, 177, 32, 35, radial(ctx, 225, 177, 35, "#687db33b", "#687db300"));
  paint(ctx, "M107 154 Q108 192 137 213 M237 158 Q233 194 211 215", null, "#bcecf77d", 1.5);
  paint(ctx, "M114 164 L123 176 123 190 M237 164 L228 176 228 190", null, "#507c9a80", 1);
  disc(ctx, 123, 192, 1.8, "#9dffff");
  disc(ctx, 228, 192, 1.8, "#9dffff");
  ctx.restore();
  paint(ctx, "M172 150 Q172 161 166 170 Q175 175 184 169", null, "#527a9690", 1.6);
  paint(ctx, "M175 151 L174 165", null, "#e8fbf5ab", 2);
  paint(ctx, vesperHair, shade(ctx, 120, 31, 233, 152, "#70aec1", "#254b68", "#101f39"), "#9cdfe7", 1.3);
  paint(ctx, vesperSweep, shade(ctx, 135, 44, 174, 105, "#c0ebe58c", "#73b5c500"));
  paint(ctx, vesperCrown, "#bfeee32b");
  paint(ctx, vesperStrands, null, "#a1e4ea6b", 1.1);
  paint(ctx, "M108 76 Q159 33 219 45 M101 96 Q143 66 174 62", null, "#ccf9ee81", 1.5);
  paint(ctx, vesperEarFolds, null, "#2b536d", 1.8);
  disc(ctx, 103, 156, 3, "#a6ffe3");
  disc(ctx, 249, 156, 3, "#a6ffe3");
}

const portraits = {
  "humanoid-android": {
    bust: vesperProjection, features: androidFeatures,
    eyeSpacing: 35, eyeY: 138, eyeWidth: 21, eyeHeight: 11.8, irisWidth: 10,
    brow: "#284860", socket: "#244a6b26",
    sclera: "#e2f2f1", iris: "#47b6c1", pupil: "#0c2d48", eyeLine: "#42627c",
    mouthY: 198, mouthWidth: 24, mouth: "#20374e", faceLine: "#53758d", mouthLight: "#9c91b8", accent: "#a2ffe8",
  },
  sprout: {
    bust: alienBust, features: roundedFeatures, tiltScale: 1.36, accent: "#bbdc9d",
    eyeSpacing: 39, eyeY: 142, eyeWidth: 26, eyeHeight: 21, irisWidth: 14,
    brow: "#487472", sclera: "#fff3db", eyeLine: "#41716c", iris: "#c58c40", pupil: "#252b36",
    mouthY: 201, mouthWidth: 31, mouth: "#485456", faceLine: "#376763", mouthLight: "#cb8790",
  },
  signal: {
    bust: signalBust, features: signalFeatures, tiltScale: .55, accent: "#dce6a7",
  },
  rivet: {
    bust: robotBust, features: roundedFeatures, tiltScale: 1.15, accent: "#e9ba91",
    eyeSpacing: 39, eyeY: 149, eyeWidth: 24, eyeHeight: 19, irisWidth: 12.8,
    brow: "#637986", sclera: "#f7f1de", eyeLine: "#728996", iris: "#31a6b7", pupil: "#12394b",
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

function androidFeatures(ctx, pose, motion, theme) {
  roundedFeatures(ctx, pose, motion, theme);
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
    const pulse = activity === "speaking" ? motion.jaw / 9 : .5;
    ctx.globalAlpha = 0.45 + 0.45 * pulse;
    paint(ctx, "M151 317 H165 L170 313 H181 L187 317 H200", null, theme.accent, 1.4);
    ctx.globalAlpha = 1;
  }
}
