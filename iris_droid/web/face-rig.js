"use strict";

// Each portrait interprets the same set of controls, in its own proportions.
const expressions = {
  neutral: { lid: 1, brow: 0, inner: 0, smile: 0, width: 1, tilt: 0, gaze: 0 },
  curious: { lid: 1.16, brow: -3, inner: -1, smile: 1.6, width: 1, tilt: -0.052, gaze: 1.8 },
  amused: { lid: 0.71, brow: -1, inner: 1, smile: 7, width: 1.13, tilt: 0.043, gaze: -1 },
  concerned: { lid: 0.88, brow: 2, inner: -8, smile: -3.6, width: 0.89, tilt: -0.028, gaze: 0 },
  focused: { lid: 0.76, brow: 1, inner: 6, smile: -1.3, width: 0.94, tilt: 0.012, gaze: 0 },
};

function blendPose(pose, expression, seconds) {
  const blend = 1 - Math.exp(-Math.max(0, seconds) * 11);
  for (const key of Object.keys(pose)) {
    pose[key] += (expressions[expression][key] - pose[key]) * blend;
  }
}

function faceMotion(seconds, activity, reducedMotion) {
  if (reducedMotion) return { blink: 0, drift: 0, gaze: 0, jaw: 0 };
  const blink = seconds % 4.63;
  return {
    blink: blink < 0.19 ? Math.sin(Math.PI * blink / 0.19) : 0,
    drift: Math.sin(seconds * 1.4),
    gaze: activity === "thinking" ? 3.5 : Math.sin(seconds * 1.15) * 0.9,
    jaw: activity === "speaking"
      ? Math.pow(Math.max(0, Math.sin(seconds * 18 + Math.sin(seconds * 9))), 1.4) * 9
      : 0,
  };
}
