"use strict";

const signalScreen = "#151c18";

function signalBust(ctx) {
  paint(ctx, "M123 273 H227 L243 294 H107Z", "#353b28", "#a4a36c", 1.6);
  paint(ctx, "M91 295 H259 L272 306 V313 H78 V306Z", "#666849", "#c2bd80", 1.5);
  paint(ctx, "M68 63 H282 L307 89 V239 L281 266 H69 L43 240 V90Z",
    shade(ctx, 63, 75, 275, 250, "#a6a275", "#85835e", "#48503b"), "#d6d097", 2);
  paint(ctx, "M76 81 H274 L292 99 V231 L274 249 H76 L58 231 V99Z", "#313729", "#20271f", 3.5);
  paint(ctx, "M89 89 H261 Q282 89 282 110 V222 Q282 241 262 241 H88 Q68 241 68 222 V110 Q68 89 89 89Z",
    signalScreen, "#aaac77", 1.2);
  ctx.save();
  ctx.fillStyle = "#d9e58715";
  for (let y = 100; y <= 232; y += 6) {
    for (let x = 82; x <= 268; x += 6) ctx.fillRect(x, y, 1, 1);
  }
  ctx.restore();
  paint(ctx, "M88 256 H117 M126 256 H145 M258 256 H272", null, "#262d23", 2);
  disc(ctx, 245, 256, 2, "#e8dc84");
}

function signalFeatures(ctx, pose, motion) {
  const pixel = 6;
  const light = "#e4edaa";
  const dim = "#a0bb73";
  for (const side of [-1, 1]) {
    const x = side < 0 ? 124 : 226;
    ctx.save();
    ctx.translate(x, 148);
    ctx.fillStyle = dim;
    for (let col = -3; col <= 3; col++) {
      const inner = (3 - side * col) / 6;
      const offset = Math.round(pose.brow / 3 + pose.inner * inner / 4 + side * pose.tilt * 7);
      ctx.fillRect(col * pixel - 2.5, -36 + offset * pixel, 5, 5);
    }
    const rows = Math.round(3 * pose.lid * (1 - motion.blink));
    ctx.fillStyle = light;
    for (let row = -rows; row <= rows; row++) {
      const halfWidth = rows >= 2 && Math.abs(row) === rows ? 2 : 3;
      for (let col = -halfWidth; col <= halfWidth; col++) {
        ctx.fillRect(col * pixel - 2.5, row * pixel - 2.5, 5, 5);
      }
    }
    if (rows >= 2) {
      const pupil = Math.round((pose.gaze + motion.gaze) * .32) * pixel;
      ctx.fillStyle = signalScreen;
      ctx.fillRect(pupil - 6.5, -8.5, 13, 17);
      ctx.fillStyle = dim;
      ctx.fillRect(pupil - 2.5, -8.5, 5, 5);
    }
    ctx.restore();
  }
  ctx.save();
  ctx.fillStyle = light;
  const columns = Math.round(6 * pose.width - motion.jaw / 6);
  for (let col = -columns; col <= columns; col++) {
    const edge = col / columns;
    const curve = Math.round(-pose.smile * edge * edge / 3);
    const opening = Math.round(Math.max(0, 1 - edge * edge) * motion.jaw / 3);
    const x = 172.5 + col * pixel;
    const y = 197.5 + curve * pixel;
    ctx.fillRect(x, y, 5, 5);
    if (opening > 0) ctx.fillRect(x, y + opening * pixel, 5, 5);
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
    shade(ctx, 168, 108, 174, 181, "#9fd0ab", "#609c8f"), "#284f51", 2.5);
  paint(ctx, "M52 122 Q81 123 99 148 L96 164 Q69 158 52 122Z M297 124 Q272 124 251 148 L253 165 Q279 158 297 124Z",
    "#a88ca1");
  paint(ctx, "M77 147 C77 96 114 71 171 71 C226 71 272 94 273 149 C275 205 236 248 175 249 C115 249 76 215 77 147Z",
    shade(ctx, 110, 79, 232, 245, "#c2e1ad", "#93c6a5", "#6aaa99"), "#335b5a", 2.4);
  paint(ctx, "M100 113 Q121 87 158 84 M182 83 Q222 84 249 108", null, "#e0edbf9c", 2.3);
  oval(ctx, 111, 184, 16, 8, "#c58f9b74");
  oval(ctx, 239, 184, 16, 8, "#c58f9b74");
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
  paint(ctx, casing, shade(ctx, 97, 82, 248, 242, "#f4bf82", "#d59a6c", "#a76551"), "#4d4550", 2.7);
  paint(ctx, "M100 91 Q175 67 241 86 M253 91 Q264 97 267 114", null, "#f9d7a7b0", 2.8);
  const plate = "M115 96 Q175 86 235 96 Q254 100 257 118 Q263 152 256 190 Q250 220 225 230 Q175 243 125 230 Q100 222 94 196 Q86 164 93 121 Q96 101 115 96Z";
  paint(ctx, plate, shade(ctx, 149, 91, 211, 233, "#f3eee0", "#dbe1da", "#a5b7ba"), "#765d59", 2.4);
  paint(ctx, "M109 108 Q143 96 178 99 M195 99 Q221 100 241 107", null, "#fff6e3", 2.1);
  for (const x of [101, 249]) {
    disc(ctx, x, 196, 3.2, "#84989e", "#5e7986");
    paint(ctx, `M${x - 1.1} 196 H${x + 1.1}`, null, "#d0d5cc", 0.8);
  }
  paint(ctx, "M136 221 Q174 233 214 221", null, "#728d94", 1.4);
}

function roundedFeatures(ctx, pose, motion, theme) {
  for (const side of [-1, 1]) {
    const x = 175 + side * theme.eyeSpacing;
    const h = Math.max(.75, theme.eyeHeight * (pose.lid + side * pose.tilt) * (1 - motion.blink));
    const w = theme.eyeWidth;
    const brow = -theme.eyeHeight - 12 + pose.brow * 1.6 + side * pose.tilt * 30;
    const inner = brow + pose.inner;
    ctx.save();
    ctx.translate(x, theme.eyeY);
    ctx.scale(-side, 1);
    ctx.lineCap = "round";
    paint(ctx, `M${-w * .75} ${brow + 3} Q0 ${brow - 7} ${w * .77} ${inner + 2}`, null, theme.brow, 4.7);
    const eye = `M${-w} 0 C${-w} ${-h * 1.4} ${w} ${-h * 1.4} ${w} 0 C${w} ${h * 1.25} ${-w} ${h * 1.25} ${-w} 0Z`;
    paint(ctx, eye, theme.sclera, theme.eyeLine, 2.6);
    ctx.clip(new Path2D(eye));
    const gaze = -side * (pose.gaze + motion.gaze) * 1.35;
    oval(ctx, gaze, 0, theme.irisWidth, theme.irisWidth * 1.13, theme.iris);
    oval(ctx, gaze, 0, theme.irisWidth * .59, theme.irisWidth * .72, theme.pupil);
    disc(ctx, gaze - side * 3.8, -4.3, 2.8, "#fff8e9");
    disc(ctx, gaze + side * 3.2, 4.6, 1.2, "#fff8e99a");
    ctx.restore();
  }
  const smile = 2.5 + pose.smile * 1.34;
  const w = theme.mouthWidth * pose.width - motion.jaw * .42;
  const corner = -smile;
  const open = 3 + motion.jaw * 1.85 + Math.max(0, smile) * .41;
  ctx.save();
  ctx.translate(175, theme.mouthY);
  const mouth = `M${-w} ${corner} Q0 ${smile * .75 - open * .5} ${w} ${corner - 1} Q0 ${smile * .85 + open * 1.45} ${-w} ${corner}Z`;
  paint(ctx, mouth, theme.mouth, theme.faceLine, 2.2);
  if (open > 6.5) {
    ctx.clip(new Path2D(mouth));
    paint(ctx, `M${-w} ${corner - 3} Q0 ${smile * .5 - open * .5} ${w} ${corner - 3} Q0 ${smile * .73 - open * .3 + 5} ${-w} ${corner - 3}Z`,
      "#f7ead4");
    oval(ctx, 3, smile * .46 + open * .77, w * .5, open * .27, theme.mouthLight);
  }
  ctx.restore();
}
