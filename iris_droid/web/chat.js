import { SpeechCapture, recordingWav } from "/speech.js";

const form = document.querySelector("#chat");
const key = document.querySelector("#key");
const message = document.querySelector("#message");
const send = document.querySelector("#send");
const mic = document.querySelector("#mic");
const status = document.querySelector("#status");
const messages = document.querySelector("#messages");
const connection = document.querySelector("#connection");
const phraseLimit = Number(document.body.dataset.phraseLimit);
let session = newSession();
let voice = null;
let pending = false;
let privateMode = false;

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
  status.textContent = privateMode ? `Private mode · ${text}` : text;
}

function addMessage(speaker, text) {
  const entry = document.createElement("article");
  const name = document.createElement("strong");
  const body = document.createElement("p");
  entry.dataset.speaker = speaker;
  name.textContent = speaker;
  body.textContent = text;
  entry.append(name, body);
  messages.append(entry);
  messages.scrollTop = messages.scrollHeight;
}

function disableMic() {
  const active = voice;
  voice = null;
  mic.setAttribute("aria-pressed", "false");
  if (!active) return;
  active.finishPlayback?.();
  active.stream?.getTracks().forEach(track => track.stop());
  active.node?.disconnect();
  void active.audio.close().catch(() => {});
}

async function playReply(active, wav) {
  const bytes = Uint8Array.from(atob(wav), value => value.charCodeAt(0));
  const buffer = await active.audio.decodeAudioData(bytes.buffer);
  if (voice !== active) return;
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
  pending = true;
  send.disabled = true;
  key.readOnly = true;
  const track = voice?.stream?.getAudioTracks()[0];
  if (track) track.enabled = false;
  voice?.capture?.reset();
  show("Waiting for Iris…");
  let feedback = "";
  try {
    const response = await fetch(spoken ? "/v1/voice" : "/v1/text", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key.value}`,
        "Content-Type": spoken ? "audio/wav" : "text/plain",
        "X-Iris-Session": session,
      },
      body,
      redirect: "error",
    });
    const reply = await response.json();
    if (!response.ok) {
      if (response.status === 401) connection.open = true;
      if (response.status === 422 && spoken) {
        feedback = "I didn’t catch that. Try again.";
        return;
      }
      throw new Error(reply.error || "Iris couldn’t answer.");
    }
    privateMode = response.headers.get("X-Iris-Private") === "1";
    connection.open = false;
    addMessage("You", reply.heard);
    addMessage("Iris", reply.text);
    if (!spoken && message.value.trim() === body) message.value = "";
    if (reply.finished) session = newSession();
    if (spoken && reply.audio && voice === active) {
      show("Iris is speaking…");
      await playReply(active, reply.audio);
    }
    if (reply.finished) {
      disableMic();
      feedback = "Conversation ended.";
    }
  } catch (error) {
    disableMic();
    feedback = error instanceof TypeError ? "Couldn’t reach Iris. Check the connection." : error.message;
  } finally {
    const currentTrack = voice?.stream?.getAudioTracks()[0];
    if (currentTrack) currentTrack.enabled = true;
    pending = false;
    send.disabled = false;
    key.readOnly = false;
    show(feedback || (voice ? "Listening…" : "Ready"));
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const text = message.value.trim();
  if (text && !pending) void turn(text);
});

mic.addEventListener("click", async () => {
  if (voice) {
    disableMic();
    show(pending ? "Finishing this turn with the mic off…" : "Microphone off");
    return;
  }
  if (pending || !key.reportValidity()) return;
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
    voice = active;
    mic.setAttribute("aria-pressed", "true");
    show("Opening microphone…");
    // Resume from this tap so the phone also permits playback later in the turn.
    await audio.resume();
    if (voice !== active) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    if (voice !== active) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    active.stream = stream;
    const track = stream.getAudioTracks()[0];
    await audio.audioWorklet.addModule("/mic.js");
    if (voice !== active) return;
    track.enabled = !pending;
    const capture = new SpeechCapture(audio.sampleRate, phraseLimit);
    const node = new AudioWorkletNode(audio, "iris-microphone");
    active.node = node;
    active.capture = capture;
    node.port.onmessage = ({ data }) => {
      if (voice !== active || pending) return;
      const frames = capture.accept(data);
      if (frames) void turn(recordingWav(frames, audio.sampleRate), active);
    };
    const input = audio.createMediaStreamSource(stream);
    input.connect(node);
    // Keep the worklet processing. It writes silence to its output.
    node.connect(audio.destination);
    track.addEventListener("ended", () => {
      if (voice === active) {
        disableMic();
        show("Microphone disconnected.");
      }
    });
    show(pending ? "Waiting for Iris…" : "Listening…");
  } catch {
    if (active && voice !== active) return;
    disableMic();
    show("Couldn’t open the microphone. Check this site’s microphone permission.");
  }
});

window.addEventListener("pagehide", disableMic);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && voice) {
    disableMic();
    show("Microphone off");
  }
});
