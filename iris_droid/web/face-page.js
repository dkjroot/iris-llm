import { mountFaceCycle, updateFace } from "/face.js";
import { mountMenu } from "/menu.js";

const menu = mountMenu({
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
  if (!document.hidden) {
    let state = null;
    try {
      const response = await fetch("/state", { signal: AbortSignal.timeout(1600) });
      if (response.ok) state = await response.json();
    } catch {
      // Keep the menus available while the voice client restarts.
    }
    connected = Boolean(state);
    cycleButton.disabled = cycling || !connected;
    updateFace(state && { ...state, showIdle: state.preview || state.show_idle });
    menu.update(state);
  }
  setTimeout(poll, document.hidden ? 1000 : 200);
}

poll();
