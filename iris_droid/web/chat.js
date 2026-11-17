import { ChatController } from "/chat-controller.js";
import { cycleSavedFace, mountFaceCycle, savedFace, updateFace } from "/face.js";
import { mountMenu } from "/menu.js";
import { SpeechCapture, recordingWav } from "/speech.js";

const form = document.querySelector("#chat");
const key = document.querySelector("#key");
const message = document.querySelector("#message");
const send = document.querySelector("#send");
const mic = document.querySelector("#mic");
const status = document.querySelector("#status");
const messages = document.querySelector("#messages");
const connection = document.querySelector("#connection");
const remember = document.querySelector("#remember");
const rememberOption = document.querySelector("#remember-option");
const rememberStatus = document.querySelector("#remember-status");
const storageKey = "iris.droid.connection-key";
const phraseLimit = Number(document.body.dataset.phraseLimit);
const turnTimeout = Number(document.body.dataset.turnTimeout);
const controller = new ChatController(newSession);
let savedToken = null;
let faceTimer;
const face = { head: savedFace(), activity: "idle", expression: "neutral", showIdle: true };

const menu = mountMenu({
  web: true,
  async loadPersonalities() {
    const token = key.value;
    let response;
    try {
      response = await fetch("/v1/web-personalities", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        redirect: "error",
        signal: AbortSignal.timeout(5000),
      });
    } catch {
      throw new Error("Couldn’t reach Iris. Check the connection and try again.");
    }
    if (response.status === 401) {
      connection.open = true;
      if (token === savedToken) forgetSavedKey();
      throw new Error(token ? "Check the connection key below, then tap Retry." :
        "Enter the connection key below, then tap Retry.");
    }
    if (!response.ok) throw new Error("Couldn’t load personalities. Try again.");
    const names = await response.json();
    if (key.value !== token) throw new Error("The connection key changed. Tap Retry.");
    if (!names.includes(controller.personality)) controller.personality = names[0] || null;
    return { names, active: controller.personality };
  },
  selectPersonality(name) {
    controller.selectPersonality(name);
  },
});

mountFaceCycle(() => {
  face.head = cycleSavedFace(face.head);
  updateFace(face);
});

function showFace(activity, expression = "neutral", milliseconds = 0) {
  clearTimeout(faceTimer);
  Object.assign(face, { activity, expression });
  updateFace(face);
  if (milliseconds) faceTimer = setTimeout(() => showFace(controller.voice ? "listening" : "idle"), milliseconds);
}

showFace("idle");

if (location.protocol === "https:") {
  try {
    savedToken = localStorage.getItem(storageKey);
    if (savedToken) {
      key.value = savedToken;
      remember.checked = true;
      connection.open = false;
    }
    rememberOption.hidden = false;
  } catch {
    showRememberStatus("This browser won’t let Iris save the connection key.");
  }
}

function showRememberStatus(text) {
  rememberStatus.textContent = text;
  rememberStatus.hidden = !text;
}

function forgetSavedKey() {
  try {
    localStorage.removeItem(storageKey);
    savedToken = null;
    showRememberStatus("");
  } catch {
    remember.checked = true;
    showRememberStatus("Couldn’t remove the saved key. Clear this site’s data in your browser.");
  }
}

remember.addEventListener("change", () => {
  if (!remember.checked) forgetSavedKey();
});

window.addEventListener("storage", event => {
  if (event.key === storageKey || event.key === null) {
    savedToken = event.newValue;
    if (!savedToken) remember.checked = false;
  }
});

function newSession() {
  // getRandomValues also works for typed chat over plain HTTP on the LAN.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)]
    .join("-");
}

function show(text) {
  status.textContent = controller.privateMode ? `Private mode · ${text}` : text;
}

function addMessage(speaker, text, exchange) {
  const entry = document.createElement("article");
  const name = document.createElement("strong");
  const body = document.createElement("p");
  entry.dataset.speaker = speaker;
  if (exchange) entry.dataset.exchange = exchange;
  name.textContent = speaker;
  body.textContent = text;
  entry.append(name, body);
  messages.append(entry);
  messages.scrollTop = messages.scrollHeight;
}

function disableMic() {
  const active = controller.voice;
  controller.voice = null;
  mic.setAttribute("aria-pressed", "false");
  if (!controller.pending) showFace("idle");
  if (!active) return;
  active.finishPlayback?.();
  active.stream?.getTracks().forEach(track => track.stop());
  active.node?.disconnect();
  void active.audio.close().catch(() => {});
}

async function playReply(active, wav) {
  const bytes = Uint8Array.from(atob(wav), value => value.charCodeAt(0));
  const buffer = await active.audio.decodeAudioData(bytes.buffer);
  if (controller.voice !== active) return;
  if (active.audio.state !== "running") throw new Error("Tap Microphone to resume phone audio.");
  const source = active.audio.createBufferSource();
  source.buffer = buffer;
  source.connect(active.audio.destination);
  await new Promise(resolve => {
    source.onended = resolve;
    active.finishPlayback = resolve;
    source.start();
  });
  active.finishPlayback = null;
}

async function turn(body, active = null) {
  const spoken = active !== null;
  const token = key.value;
  const request = controller.begin();
  if (!request) return;
  send.disabled = true;
  key.readOnly = true;
  const track = controller.voice?.stream?.getAudioTracks()[0];
  if (track) track.enabled = false;
  controller.voice?.capture?.reset();
  show("Waiting for Iris…");
  showFace("thinking");
  let feedback = "";
  let writtenExpression = null;
  try {
    const response = await fetch(spoken ? "/v1/voice" : "/v1/text", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": spoken ? "audio/wav" : "text/plain",
        "X-Iris-Session": controller.session,
        "X-Iris-Face": "1",
        "X-Iris-Web-Personality": encodeURIComponent(controller.personality || ""),
      },
      body,
      redirect: "error",
      signal: AbortSignal.any([request.abort.signal, AbortSignal.timeout(turnTimeout * 1000)]),
    });
    const reply = await response.json();
    if (!controller.current(request)) return;
    if (!response.ok) {
      if (response.status === 401) {
        connection.open = true;
        if (token === savedToken) forgetSavedKey();
      }
      if (response.status === 422 && spoken) {
        feedback = "I didn’t catch that. Try again.";
        return;
      }
      throw new Error(reply.error || "Iris couldn’t answer.");
    }
    controller.acknowledge(request, reply, response.headers.get("X-Iris-Private") === "1");
    if (reply.selected_personality) menu.setActive(controller.personality);
    connection.open = false;
    if (remember.checked && !rememberOption.hidden && token !== savedToken) {
      try {
        localStorage.setItem(storageKey, token);
        savedToken = token;
        showRememberStatus("");
      } catch {
        connection.open = true;
        showRememberStatus("Couldn’t save the key. You can still chat in this tab.");
      }
    }
    const deletedExchange = response.headers.get("X-Iris-Deleted-Exchange");
    const exchange = response.headers.get("X-Iris-Exchange");
    if (deletedExchange) {
      for (const entry of messages.querySelectorAll("[data-exchange]")) {
        if (entry.dataset.exchange === deletedExchange) entry.remove();
      }
    } else {
      addMessage("You", reply.heard, exchange);
    }
    addMessage("Iris", reply.text, exchange);
    if (!spoken && message.value.trim() === body) message.value = "";
    if (spoken && reply.audio && controller.voice === active) {
      show("Iris is speaking…");
      showFace("speaking", reply.expression);
      await playReply(active, reply.audio);
      if (!controller.current(request)) return;
    } else if (!spoken) {
      writtenExpression = reply.expression;
    }
    if (reply.finished) {
      disableMic();
      feedback = "Conversation ended.";
    }
  } catch (error) {
    if (!controller.current(request)) return;
    disableMic();
    feedback = error.name === "TimeoutError"
      ? "Iris took too long. Check any device action before trying again."
      : error instanceof TypeError ? "Couldn’t reach Iris. Check the connection." : error.message;
  } finally {
    if (!controller.finish(request)) return;
    const currentTrack = controller.voice?.stream?.getAudioTracks()[0];
    if (currentTrack) currentTrack.enabled = true;
    send.disabled = false;
    key.readOnly = false;
    show(feedback || (controller.voice ? "Listening…" : "Ready"));
    showFace(controller.voice ? "listening" : "idle", writtenExpression || "neutral", writtenExpression ? 3000 : 0);
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const text = message.value.trim();
  if (text && !controller.pending) void turn(text);
});

mic.addEventListener("click", async () => {
  if (controller.voice) {
    disableMic();
    show(controller.pending ? "Finishing this turn with the mic off…" : "Microphone off");
    return;
  }
  if (controller.pending || !key.reportValidity()) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
    show("Open Iris over trusted HTTPS to use the microphone.");
    return;
  }
  if (phraseLimit < 1 / 16000) {
    show("The server’s phrase time limit is too short for microphone input.");
    return;
  }
  let active;
  try {
    const audio = new AudioContext();
    active = { audio };
    controller.voice = active;
    mic.setAttribute("aria-pressed", "true");
    show("Opening microphone…");
    // Resume from this tap so the phone also permits playback later in the turn.
    await audio.resume();
    if (controller.voice !== active) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (controller.voice !== active) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    active.stream = stream;
    const track = stream.getAudioTracks()[0];
    await audio.audioWorklet.addModule("/mic.js");
    if (controller.voice !== active) return;
    track.enabled = !controller.pending;
    const capture = new SpeechCapture(audio.sampleRate, phraseLimit);
    const node = new AudioWorkletNode(audio, "iris-microphone");
    active.node = node;
    active.capture = capture;
    node.port.onmessage = ({ data }) => {
      if (controller.voice !== active || controller.pending) return;
      const frames = capture.accept(data);
      if (frames) void turn(recordingWav(frames, audio.sampleRate), active);
    };
    const input = audio.createMediaStreamSource(stream);
    input.connect(node);
    // Keep the worklet processing. It writes silence to its output.
    node.connect(audio.destination);
    track.addEventListener("ended", () => {
      if (controller.voice === active) {
        disableMic();
        show("Microphone disconnected.");
      }
    });
    show(controller.pending ? "Waiting for Iris…" : "Listening…");
    if (!controller.pending) showFace("listening");
  } catch {
    if (active && controller.voice !== active) return;
    disableMic();
    show("Couldn’t open the microphone. Check this site’s microphone permission.");
  }
});

window.addEventListener("pagehide", () => {
  controller.close();
  disableMic();
  clearTimeout(faceTimer);
});
window.addEventListener("pageshow", () => {
  controller.closed = false;
  show("Ready");
  showFace("idle");
  send.disabled = false;
  key.readOnly = false;
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && controller.voice) {
    disableMic();
    show("Microphone off");
  }
});
