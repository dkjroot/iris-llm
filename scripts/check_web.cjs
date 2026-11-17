"use strict";

// Fast pure-JavaScript checks; browser/device lifecycle tests remain in tests/web.
const assert = require("node:assert/strict");
const { readFileSync, readdirSync } = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const web = path.join(__dirname, "..", "iris_droid", "web");

async function moduleFrom(name) {
  return import(`data:text/javascript;base64,${readFileSync(path.join(web, name)).toString("base64")}`);
}

(async () => {
  const names = readdirSync(web).filter((name) => name.endsWith(".js"));
  for (const name of names) {
    const result = spawnSync(process.execPath, ["--input-type=module", "--check"], {
      input: readFileSync(path.join(web, name)), encoding: "utf8",
    });
    assert.equal(result.status, 0, `${name}: ${result.stderr}`);
  }
  const { speechEnvelope, envelopeAt, outputTime } = await moduleFrom("speech-envelope.js");
  const speech = new Float32Array(1440);
  speech.fill(.2, 480, 960);
  const envelope = speechEnvelope({length: 1440, sampleRate: 24000, numberOfChannels: 1,
    getChannelData: () => speech});
  assert.equal(envelopeAt(envelope, -.01), 0);
  assert.equal(envelopeAt(envelope, .01), 0);
  assert.equal(envelopeAt(envelope, .03), 1);
  assert.equal(envelopeAt(envelope, .05), 0);
  assert.equal(envelopeAt(envelope, 2), 0);
  assert.equal(outputTime({currentTime: 1, outputLatency: .1}, 0), .9);
  assert.equal(outputTime({currentTime: 1, getOutputTimestamp: () =>
    ({contextTime: .8, performanceTime: 1000})}, 1050), .8500000000000001);
  const { recordingWav, SpeechCapture } = await moduleFrom("speech.js");
  const wav = new DataView(recordingWav([new Float32Array(4800).fill(0.5)], 48000));
  assert.equal(wav.getUint32(24, true), 16000);
  assert.equal(wav.getUint32(40, true), 3200);
  assert.equal(wav.byteLength, 3244);
  assert.equal(wav.getInt16(44, true), 16384);
  const capture = new SpeechCapture(16000, 1);
  assert.equal(capture.accept(new Float32Array(2000).fill(0.2)), null);
  assert.ok(capture.accept(new Float32Array(12000)));
  const { ChatController } = await moduleFrom("chat-controller.js");
  const controller = new ChatController(() => "synthetic-session");
  const request = controller.begin();
  assert.equal(controller.begin(), null);
  controller.selectPersonality("newer-choice");
  controller.acknowledge(request, { selected_personality: "old-choice" }, false);
  assert.equal(controller.personality, "newer-choice");
  controller.close();
  assert.equal(request.abort.signal.aborted, true);
  assert.equal(controller.acknowledge(request, {}, false), false);
  console.log(`Checked ${names.length} JavaScript files, WAV/capture and stale-request contracts.`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
