"use strict";

const signalScreen = "#151c18";

function signalBust(ctx) {
  oval(ctx, 175, 313, 100, 9, "#101d16");
  paint(ctx, "M134 264 H216 L229 292 H121Z", shade(ctx, 130, 270, 215, 290, "#243c39", "#7c9a7c", "#263c36"), "#657a62", 1.2);
  paint(ctx, "M106 290 H244 Q259 290 263 303 L266 310 H84 L87 303 Q91 290 106 290Z", shade(ctx, 175, 290, 175, 311, "#a1ae83", "#3a5143"), "#b3c09b", 1.2);
  const shell = "M82 57 H268 Q302 57 310 91 L314 227 Q314 259 281 271 H69 Q36 259 36 227 L40 91 Q48 57 82 57Z";
  paint(ctx, shell, radial(ctx, 102, 70, 280, "#e3d4a0", "#8d9c75", "#324b43"), "#d6dfb7", 1.6);
  paint(ctx, "M60 88 Q63 69 86 68 H263 Q283 68 291 85", null, "#f4f2cbaf", 2.5);
  paint(ctx, "M47 113 L45 224 Q45 248 66 256 M304 113 L306 224 Q306 248 285 256", null, "#173e3f", 2);
  const bezel = "M86 82 H264 Q290 82 293 108 L295 220 Q295 248 266 251 H84 Q55 248 55 220 L57 108 Q60 82 86 82Z";
  paint(ctx, bezel, shade(ctx, 175, 82, 175, 251, "#263e35", "#102522", "#82947a"), "#354b3a", 2);
  const glass = "M89 91 H261 Q281 91 283 112 L285 216 Q285 238 262 241 H88 Q65 238 65 216 L67 112 Q69 91 89 91Z";
  paint(ctx, glass, radial(ctx, 163, 155, 145, "#244637", "#10271f", "#071511"), "#bbcbaa66", 1);
  ctx.save();
  ctx.clip(new Path2D(glass));
  for (let y = 98; y <= 238; y += 4) paint(ctx, `M65 ${y} H285`, null, "#b2f4a308", .6);
  paint(ctx, "M75 123 Q174 93 279 116 L279 95 H75Z", "#d9fff20b");
  ctx.restore();
  paint(ctx, "M89 261 H119 M129 261 H148", null, "#29443a", 2);
  disc(ctx, 260, 261, 3.2, "#183e2b");
  disc(ctx, 260, 260, 1.8, "#cfff9c");
  for (const x of [53, 297]) {
    disc(ctx, x, 96, 3.4, "#3b5147", "#bed0a5");
    paint(ctx, `M${x - 1.5} 96 H${x + 1.5}`, null, "#a6b78c", .8);
  }
}

function signalFeatures(ctx, pose, motion) {
  const light = "#deffc1";
  const eyeInk = "#b6ef96";
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const side of [-1, 1]) {
    const x = 175 + side * 48;
    const h = Math.max(1, 17 * (pose.lid + side * pose.asymmetry * .018) * (1 - motion.blink));
    const brow = 113 + pose.brow + side * pose.asymmetry * .5;
    const inner = brow + pose.inner * .65;
    paint(ctx, `M${x - side * 21} ${inner} Q${x} ${brow - 5} ${x + side * 20} ${brow}`, null, "#86bb71", 4);
    // Soft phosphor halo is geometry, not a per-frame blur.
    oval(ctx, x, 150, 24.5, h + 2.5, "#a1ff8c12");
    const eye = `M${x - 22} 150 Q${x - 22} ${150 - h} ${x - 13} ${150 - h} H${x + 13} Q${x + 22} ${150 - h} ${x + 22} 150 Q${x + 22} ${150 + h} ${x + 13} ${150 + h} H${x - 13} Q${x - 22} ${150 + h} ${x - 22} 150Z`;
    paint(ctx, eye, eyeInk);
    if (h > 4) {
      ctx.save(); ctx.clip(new Path2D(eye));
      const gaze = pose.gaze + motion.gaze;
      oval(ctx, x + gaze * 1.7, 150 + pose.gazeY, 8, 12, "#163a2a");
      disc(ctx, x + gaze * 1.7 - 3, 145 + pose.gazeY, 2.4, light);
      ctx.restore();
    }
  }
  const round = Math.max(pose.round, motion.round || 0);
  const w = 35 * pose.width * (1 - round * .45);
  const open = pose.open + motion.jaw * 1.4;
  const corner = 201 - pose.smile * .8;
  const path = round > .5
    ? `M${175 - w} 201 C${175 - w} ${201 - open} ${175 + w} ${201 - open} ${175 + w} 201 C${175 + w} ${201 + open} ${175 - w} ${201 + open} ${175 - w} 201Z`
    : `M${175 - w} ${corner} Q175 ${203 - open * .45} ${175 + w} ${corner - pose.asymmetry * .22} Q175 ${204 + pose.smile + open * 1.5} ${175 - w} ${corner}Z`;
  paint(ctx, path, open > 2 || pose.smile > 4 ? "#8fc879" : null, light, 3.2);
  if (pose.cheek > .4) {
    paint(ctx, "M102 184 L108 187 M242 187 L248 184", null, "#82bc6c", 2.3);
  }
  ctx.restore();
}

function oval(ctx, x, y, rx, ry, color) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(rx, ry);
  disc(ctx, 0, 0, 1, color);
  ctx.restore();
}

function alienBust(ctx) {
  oval(ctx, 175, 312, 108, 10, "#150f21a0");
  paint(ctx, "M69 302 Q81 270 125 253 L150 243 H200 L226 253 Q264 268 281 302 Q277 313 175 313 Q78 313 69 302Z",
    shade(ctx, 174, 245, 186, 313, "#70648d", "#494667", "#34354e"), "#29283e", 2.4);
  paint(ctx, "M144 230 H206 L210 252 Q175 282 140 250Z", "#64ab99");
  paint(ctx, "M111 265 Q120 249 141 248 Q145 266 175 274 Q205 266 210 248 Q233 252 238 267 Q214 282 175 292 Q136 281 111 265Z",
    "#8d799e", "#534864", 2);
  paint(ctx, "M155 285 L148 302 M194 285 L201 302", null, "#c1a9be", 2.2);
  paint(ctx, "M126 93 Q108 63 82 51 M220 87 Q227 55 257 43", null, "#284f51", 9);
  paint(ctx, "M126 93 Q108 63 82 51 M220 87 Q227 55 257 43", null, "#7cba9c", 4.5);
  disc(ctx, 82, 50, 11, "#b6d893", "#396a66");
  disc(ctx, 257, 43, 11, "#b6d893", "#396a66");
  disc(ctx, 79, 47, 3.8, "#dfedb3");
  disc(ctx, 254, 40, 3.8, "#dfedb3");
  paint(ctx, "M92 124 Q60 105 38 114 Q44 148 68 171 Q80 182 108 180Z M254 124 Q285 105 312 117 Q306 148 281 170 Q267 186 244 181Z",
    shade(ctx, 168, 108, 174, 181, "#c8e6b1", "#4b9187"), "#284f51", 2.5);
  paint(ctx, "M52 122 Q81 123 99 148 L96 164 Q69 158 52 122Z M297 124 Q272 124 251 148 L253 165 Q279 158 297 124Z",
    shade(ctx, 165, 121, 165, 169, "#d7a7b6", "#9c7a9d"));
  paint(ctx, "M77 147 C77 96 114 71 171 71 C226 71 272 94 273 149 C275 205 236 248 175 249 C115 249 76 215 77 147Z",
    radial(ctx, 144, 112, 154, "#e5efb9", "#9acda5", "#397e7c"), "#335b5a", 2.4);
  paint(ctx, "M100 113 Q121 87 158 84 M182 83 Q222 84 249 108", null, "#e0edbf9c", 2.3);
  oval(ctx, 107, 184, 20, 17, radial(ctx, 107, 184, 20, "#dc9fa46b", "#dc9fa400"));
  oval(ctx, 243, 184, 20, 17, radial(ctx, 243, 184, 20, "#dc9fa46b", "#dc9fa400"));
  paint(ctx, "M88 161 Q89 211 131 230 M263 161 Q261 210 231 228", null, "#b7e7c178", 2);
  oval(ctx, 157, 100, 35, 12, radial(ctx, 151, 99, 36, "#ffffd92b", "#ffffd900"));
  for (const [x, y, r] of [[101, 174, 2], [110, 177, 2.7], [121, 174, 1.8], [229, 174, 1.8], [240, 177, 2.7], [249, 174, 2]]) {
    disc(ctx, x, y, r, "#4c897e");
  }
  paint(ctx, "M167 172 Q175 167 183 172", null, "#689f88", 2.4);
}

function robotBust(ctx) {
  oval(ctx, 175, 315, 107, 10, "#111c268c");
  paint(ctx, "M68 304 Q76 270 113 264 L144 252 H206 L237 264 Q273 272 282 304 Q267 315 175 315 Q83 315 68 304Z",
    shade(ctx, 91, 261, 239, 319, "#729ea9", "#496b7d", "#314a5c"), "#253e4e", 2.5);
  paint(ctx, "M130 262 L156 249 H193 L220 262 210 282 140 282Z", "#537281", "#2e4b5b", 2);
  paint(ctx, "M144 232 H206 V264 Q175 277 144 264Z",
    shade(ctx, 144, 250, 206, 250, "#405865", "#94a6aa", "#526773"), "#293e49", 2);
  paint(ctx, "M148 256 H201 M149 262 H198", null, "#293e4980", 2.2);
  paint(ctx, "M89 292 Q107 278 124 275 M261 292 Q244 278 226 275", null, "#9bc0cb80", 2);
  paint(ctx, "M161 293 Q161 289 165 289 H185 Q189 289 189 293 V298 Q189 302 185 302 H165 Q161 302 161 298Z",
    "#324952", "#89adba");
  paint(ctx, "M166 295 H184", null, "#b1dfc6", 2.4);
  for (const [x, y] of [[71, 153], [279, 153]]) {
    disc(ctx, x, y, 26, "#334a59", "#1c3342");
    disc(ctx, x, y, 22, shade(ctx, x - 11, y - 24, x + 16, y + 22, "#ebaa70", "#be805a", "#84514a"), "#734f4c");
    disc(ctx, x, y, 14, "#52717e", "#915e4e");
    disc(ctx, x, y, 8, "#83a4af", "#304c5c");
    disc(ctx, x - 2, y - 2, 3.2, "#bfd6d8");
  }
  const casing = "M112 76 Q175 64 238 76 Q268 81 274 108 Q282 146 272 193 Q263 233 231 245 Q175 259 118 245 Q85 238 76 204 Q66 161 76 111 Q82 83 112 76Z";
  paint(ctx, casing, radial(ctx, 125, 96, 185, "#ffe0a0", "#dca16d", "#8a4a48"), "#4d4550", 2.7);
  paint(ctx, "M100 91 Q175 67 241 86 M253 91 Q264 97 267 114", null, "#f9d7a7b0", 2.8);
  const plate = "M115 96 Q175 86 235 96 Q254 100 257 118 Q263 152 256 190 Q250 220 225 230 Q175 243 125 230 Q100 222 94 196 Q86 164 93 121 Q96 101 115 96Z";
  paint(ctx, plate, radial(ctx, 147, 123, 142, "#ffffef", "#dfe8df", "#829fa7"), "#765d59", 2.4);
  paint(ctx, "M109 108 Q143 96 178 99 M195 99 Q221 100 241 107", null, "#fff6e3", 2.1);
  for (const x of [101, 249]) {
    disc(ctx, x, 196, 3.2, "#84989e", "#5e7986");
    paint(ctx, `M${x - 1.1} 196 H${x + 1.1}`, null, "#d0d5cc", 0.8);
  }
  paint(ctx, "M136 226 Q174 238 214 226", null, "#789aa2", 1.4);
  paint(ctx, "M80 140 Q74 194 97 219 M271 137 Q277 193 254 219", null, "#ffddb582", 1.7);
  for (const x of [109, 241]) {
    oval(ctx, x, 181, 11, 5, shade(ctx, x, 177, x, 186, "#ecb29188", "#c2746600"));
  }
}

// One continuous eye silhouette keeps the iris inside both lids, even at
// extreme poses. Independent brows, cheek lift and mouth corners carry emotion.
function roundedFeatures(ctx, pose, motion, theme) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const side of [-1, 1]) {
    const x = 175 + side * theme.eyeSpacing;
    const h = Math.max(.55, theme.eyeHeight * (pose.lid + side * pose.asymmetry * .018) * (1 - motion.blink));
    const w = theme.eyeWidth;
    const cheek = pose.cheek * 2;
    const brow = -theme.eyeHeight - 10 + pose.brow + side * pose.asymmetry * .65;
    const inner = brow + pose.inner;
    ctx.save();
    ctx.translate(x, theme.eyeY);
    ctx.scale(-side, 1);
    oval(ctx, 0, 1, w + 3, theme.eyeHeight + 4, "#20435712");
    paint(ctx, `M${-w * .8} ${brow + 2} Q0 ${brow - 6} ${w * .8} ${inner + 1}`, null, "#f2ffe836", 6.7);
    paint(ctx, `M${-w * .8} ${brow} Q0 ${brow - 8} ${w * .8} ${inner - 1}`, null, theme.brow, 4.3);
    const bottom = Math.max(.5, h * .96 - cheek);
    const eye = `M${-w} 0 C${-w} ${-h * 1.36} ${w * .9} ${-h * 1.36} ${w} 0 C${w * .8} ${bottom * 1.3} ${-w * .85} ${bottom * 1.3} ${-w} 0Z`;
    paint(ctx, eye, shade(ctx, 0, -h, 0, bottom, "#93b1b4", theme.sclera, "#fffbee"), theme.eyeLine, 1.6);
    ctx.save();
    ctx.clip(new Path2D(eye));
    const gaze = -side * (pose.gaze + motion.gaze) * 1.2;
    const gy = pose.gazeY + (motion.gazeY || 0);
    const r = theme.irisWidth;
    disc(ctx, gaze, gy, r, theme.pupil);
    disc(ctx, gaze, gy, r - 1, radial(ctx, gaze, gy + r * .4, r * 1.5, theme.iris, theme.iris, theme.pupil));
    // Radial striations read as a textured iris at panel resolution.
    for (let i = 0; i < 12; i++) {
      const a = i * Math.PI / 6;
      paint(ctx, `M${gaze + Math.cos(a) * r * .72} ${gy + Math.sin(a) * r * .72} L${gaze + Math.cos(a) * r * .9} ${gy + Math.sin(a) * r * .9}`, null, "#ecffd94a", .65);
    }
    disc(ctx, gaze, gy, r * .53, theme.pupil);
    oval(ctx, gaze - side * r * .3, gy - r * .38, r * .25, r * .19, "#ffffffee");
    disc(ctx, gaze + side * r * .35, gy + r * .38, r * .11, "#eaffffb0");
    paint(ctx, `M${-w} 0 C${-w} ${-h * 1.36} ${w * .9} ${-h * 1.36} ${w} 0`, null, "#17364d48", 5);
    ctx.restore();
    paint(ctx, `M${-w} 0 C${-w} ${-h * 1.36} ${w * .9} ${-h * 1.36} ${w} 0`, null, theme.eyeLine, 2);
    if (pose.cheek > .15) paint(ctx, `M${-w + 1} ${theme.eyeHeight + 6 - cheek} Q-3 ${theme.eyeHeight + 10 - cheek} 7 ${theme.eyeHeight + 6 - cheek}`, null, "#46776e50", 1.3);
    ctx.restore();
  }
  const smile = pose.smile * 1.1;
  const round = Math.max(pose.round, motion.round || 0);
  const w = theme.mouthWidth * pose.width * (1 - round * .48) - motion.jaw * .12;
  const left = -smile + pose.asymmetry * .24;
  const right = -smile - pose.asymmetry * .24;
  const open = 1 + pose.open + motion.jaw * 1.6 + Math.max(0, smile) * .3;
  ctx.save();
  ctx.translate(175, theme.mouthY);
  // Closed smiles and frowns stay readable; the jaw opens independently.
  const mouth = round > .5
    ? `M${-w} 0 C${-w} ${-open * 1.3} ${w} ${-open * 1.3} ${w} 0 C${w} ${open * 1.3} ${-w} ${open * 1.3} ${-w} 0Z`
    : `M${-w} ${left} Q0 ${-open * .55} ${w} ${right} Q0 ${smile * .8 + open * 1.5} ${-w} ${left}Z`;
  paint(ctx, `M${-w * .65} ${open * .9 + 5} Q0 ${open * 1.15 + 9} ${w * .65} ${open * .9 + 5}`, null, "#f3ffe235", 2);
  paint(ctx, mouth, theme.mouth, theme.faceLine, 1.7);
  ctx.save(); ctx.clip(new Path2D(mouth));
  if (open > 3.5) {
    paint(ctx, `M${-w - 1} ${left - 2} Q0 ${-open * .55 - 2} ${w + 1} ${right - 2} L${w + 1} ${right + 2} Q0 ${-open * .32 + 5} ${-w - 1} ${left + 2}Z`, shade(ctx, 0, -4, 0, 7, "#bfcfc9", "#fff8e5"));
    oval(ctx, 1, smile * .3 + open * .92, w * .6, open * .34, theme.mouthLight);
    paint(ctx, `M-5 ${smile * .3 + open * .8} Q1 ${smile * .3 + open * .72} 7 ${smile * .3 + open * .8}`, null, "#ffd9d260", 1.5);
  }
  ctx.restore();
  if (pose.cheek > .2) {
    for (const side of [-1, 1]) paint(ctx, `M${side * (w + 3)} ${-smile - 3} q${side * 3} 2 ${side * 2} 5`, null, theme.faceLine, 1.3);
  }
  ctx.restore();
  ctx.restore();
}
