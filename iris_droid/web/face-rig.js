"use strict";

// Art-directed poses, shared by the live canvas and the Pi's sprite exporter.
// Positive inner lowers the inside of both brows; asymmetry lifts just one.
const restingPose = { lid: 1, brow: 0, inner: 0, smile: 1, width: 1, tilt: 0,
  gaze: 0, gazeY: 0, asymmetry: 0, cheek: 0, open: 0, round: 0 };
const expressionPose = (controls) => ({ ...restingPose, ...controls });
const expressions = {
  neutral: expressionPose({}),
  curious: expressionPose({ lid: 1.1, brow: -3, inner: -2, smile: 2, tilt: -.065, gaze: 2, asymmetry: 3 }),
  amused: expressionPose({ lid: .72, brow: -1, inner: 1, smile: 8, width: 1.13, tilt: .045, cheek: .8, asymmetry: 1.5 }),
  concerned: expressionPose({ lid: .9, brow: 1, inner: -8, smile: -4, width: .85, tilt: -.035, gazeY: 1 }),
  focused: expressionPose({ lid: .78, brow: 2, inner: 5, smile: -1, width: .86, gazeY: -1 }),
  delighted: expressionPose({ lid: .8, brow: -4, inner: -2, smile: 11, width: 1.15, open: 4, cheek: 1, tilt: -.035 }),
  surprised: expressionPose({ lid: 1.3, brow: -8, inner: -2, smile: 0, width: .56, open: 8, round: .9, gazeY: -1 }),
  skeptical: expressionPose({ lid: .78, brow: 1, inner: 2, smile: -.5, width: .85, asymmetry: 7, gaze: -3, tilt: .06 }),
  sleepy: expressionPose({ lid: .42, brow: 3, inner: -1, smile: .5, width: .8, gazeY: 2, tilt: -.045 }),
  sad: expressionPose({ lid: .78, brow: 2, inner: -10, smile: -6, width: .78, gazeY: 3, tilt: -.055 }),
  playful: expressionPose({ lid: .85, brow: -2, inner: 0, smile: 7, width: 1.05, asymmetry: 5, cheek: .7, gaze: 3, tilt: -.085 }),
  proud: expressionPose({ lid: .8, brow: -2, inner: -1, smile: 5, width: 1.1, cheek: .45, gazeY: -2, tilt: .025 }),
};

function blendPose(pose, expression, seconds) {
  const blend = 1 - Math.exp(-Math.min(.1, Math.max(0, seconds)) * 10);
  for (const key of Object.keys(restingPose)) {
    pose[key] += (expressions[expression][key] - pose[key]) * blend;
  }
}

function faceMotion(seconds, activity, reducedMotion) {
  if (reducedMotion) return { blink: 0, drift: 0, gaze: 0, gazeY: 0, jaw: 0, round: 0 };
  // Uneven blink intervals, including a small double blink, on a repeatable clock.
  const cycle = seconds % 13.7;
  let blink = 0;
  for (const start of [0, 4.63, 9.8, 10.12]) {
    const t = (cycle - start) / .19;
    if (t >= 0 && t <= 1) blink = Math.sin(Math.PI * t);
  }
  // Hold the gaze between saccades rather than swimming continuously side to side.
  const gazeStep = Math.floor(seconds / 2.7) % 5;
  const gaze = [0, -2.4, 0, 2.4, 0][gazeStep];
  const speech = Math.max(0, Math.sin(seconds * 18 + Math.sin(seconds * 9)));
  return {
    blink,
    drift: Math.sin(seconds * 1.4),
    gaze: activity === "thinking" ? 3.5 : gaze,
    gazeY: activity === "thinking" ? -1.5 : 0,
    jaw: activity === "speaking" ? Math.pow(speech, 1.4) * 9 : 0,
    round: activity === "speaking" ? Math.max(0, Math.sin(seconds * 7.3)) * .65 : 0,
  };
}
