import { mountCommandFeedback } from "/command-feedback.js";
import { mountFaceCycle, updateFace } from "/face.js";
import { mountMenu } from "/menu.js";

const menu = mountMenu({
  async executeCommand(phrase) {
    let response;
    try {
      response = await fetch("/command", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify(phrase), signal: AbortSignal.timeout(1800),
      });
    } catch {
      throw new Error("Couldn’t reach the Iris client. Try again in a moment.");
    }
    if (!response.ok) throw new Error(response.status === 409
      ? "Iris is busy. Wait for this turn to finish." : "Command not accepted. Try again when Iris is ready.");
    menu.setBusy(true);
  },
  async selectPersonality(name) {
    try {
      const response = await fetch("/personality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(name),
        signal: AbortSignal.timeout(1800),
      });
      if (!response.ok) throw new Error();
    } catch {
      throw new Error("Couldn’t switch personality. Check the Iris client and try again.");
    }
  },
});

const commandFeedback = mountCommandFeedback();
let lastNotice = null;
let connected = false;
let cycling = false;
const cycleButton = mountFaceCycle(async () => {
  cycling = true;
  cycleButton.disabled = true;
  try {
    await fetch("/cycle-face", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "null",
      signal: AbortSignal.timeout(1800),
    });
  } catch {
    // The state poll handles reconnecting after a client restart.
  } finally {
    cycling = false;
    cycleButton.disabled = !connected;
  }
});
cycleButton.disabled = true;

async function poll() {
  let speaking = false;
  if (!document.hidden) {
    let state = null;
    try {
      const response = await fetch("/state", { signal: AbortSignal.timeout(1600) });
      if (response.ok) state = await response.json();
    } catch {
      // Keep the menus available while the voice client restarts.
    }
    connected = Boolean(state);
    speaking = state?.activity === "speaking";
    cycleButton.disabled = cycling || !connected;
    updateFace(state && { ...state, showIdle: state.preview || state.show_idle });
    menu.update(state);
    commandFeedback.companion(state?.companion_mode &&
      (state.activity !== "idle" || state.show_idle || state.preview));
    if (state?.notice?.id !== lastNotice) {
      lastNotice = state?.notice?.id;
      if (state?.notice) commandFeedback.show(state.notice, state.notice.status === "processing" ? 600000 : 6000);
      else commandFeedback.clear();
    }
  }
  setTimeout(poll, document.hidden ? 1000 : speaking ? 50 : 200);
}

poll();
