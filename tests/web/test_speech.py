"""Pure JavaScript assertions run in the same module environment as the app."""

import pytest

pytestmark = pytest.mark.browser


def test_segmentation_rejects_silence_and_ends_after_a_pause(web_page):
    page, _ = web_page
    result = page.evaluate("""async () => {
      const {SpeechCapture} = await import('/speech.js');
      const capture = new SpeechCapture(16000, 2);
      const quiet = new Float32Array(160);
      const speech = new Float32Array(160).fill(0.25);
      let unexpected = false;
      for (let i = 0; i < 1000; ++i) unexpected ||= capture.accept(quiet) !== null;
      const retained = capture.length;
      for (let i = 0; i < 20; ++i) unexpected ||= capture.accept(speech) !== null;
      let result;
      for (let i = 0; i < 80 && !result; ++i) result = capture.accept(quiet);
      return {unexpected, retained, samples: result?.reduce((n, f) => n + f.length, 0),
              hasSpeech: result?.some(f => f.some(s => s !== 0)), reset: capture.length};
    }""")
    assert not result["unexpected"]
    assert result["retained"] <= 16000 * 0.24
    assert 16000 * 0.8 <= result["samples"] <= 16000 * 2
    assert result["hasSpeech"] and result["reset"] == 0


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_wav_output_is_mono_16khz_pcm_with_clipping(web_page, rate):
    page, _ = web_page
    result = page.evaluate(
        """async rate => {
      const {recordingWav} = await import('/speech.js');
      const samples = new Float32Array(rate);
      samples.fill(2, 0, rate / 2); samples.fill(-2, rate / 2);
      const frames = [samples.subarray(0, 123), samples.subarray(123)];
      const view = new DataView(recordingWav(frames, rate));
      return {bytes: view.byteLength, riff: view.getUint32(0), channels: view.getUint16(22, true),
              rate: view.getUint32(24, true), bits: view.getUint16(34, true),
              data: view.getUint32(40, true), first: view.getInt16(44, true),
              last: view.getInt16(view.byteLength - 2, true)};
    }""",
        rate,
    )
    assert result == {
        "bytes": 32044,
        "riff": 0x52494646,
        "channels": 1,
        "rate": 16000,
        "bits": 16,
        "data": 32000,
        "first": 32767,
        "last": -32767,
    }


def test_phrase_limit_caps_and_resets_capture(web_page):
    page, _ = web_page
    result = page.evaluate("""async () => {
      const {SpeechCapture} = await import('/speech.js');
      const capture = new SpeechCapture(16000, 0.25);
      let frames;
      for (let i = 0; i < 20 && !frames; ++i)
        frames = capture.accept(new Float32Array(512).fill(0.2));
      return {samples: frames.reduce((n, f) => n + f.length, 0), started: capture.started};
    }""")
    assert result == {"samples": 4000, "started": False}
