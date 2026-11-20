import pytest

from iris_droid.face_display import FaceDisplay

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("screensaver", ["black", "snow", "off"])
def test_face_cycle_hides_when_the_screensaver_starts(
    browser, screensaver, monkeypatch, fake_clock
):
    from playwright.sync_api import expect

    monkeypatch.setattr("iris_droid.face_display.monotonic", fake_clock.monotonic)
    display = FaceDisplay("signal", screensaver)
    with display.speaking("neutral"):
        pass
    fake_clock.advance(299)
    with display.serve(0) as port:
        page = browser.new_page()
        try:
            page.goto(f"http://127.0.0.1:{port}/")
            cycle_button = page.locator(".iris-face-cycle")
            expect(cycle_button).to_be_visible()
            fake_clock.advance(1)
            expect(cycle_button).to_be_hidden()
            page.get_by_role("button", name="Open Iris menu").click()
            expect(page.get_by_role("button", name="Command list")).to_be_visible()
        finally:
            page.close()


def test_phone_face_tracks_audio_clock_pauses_and_cleanup(web_page):
    page, _ = web_page
    page.evaluate("""async () => {
      const face = await import('/face.js');
      window.faceTest = face;
      const original = portraits.rivet.features;
      portraits.rivet.features = (ctx, pose, motion, theme) => {
        window.lastFaceMotion = {...motion}; original(ctx, pose, motion, theme);
      };
      window.faceAudio = {state: 'running', currentTime: 0, baseLatency: 0};
      const pcm = new Float32Array(24000);
      pcm.fill(.3, 4800, 9600); // A syllable between two real pauses.
      window.stopFaceAudio = face.followFaceAudio(window.faceAudio, {
        sampleRate: 24000, length: pcm.length, numberOfChannels: 1,
        getChannelData: () => pcm,
      }, 0);
      face.updateFace({head:'rivet', activity:'speaking', expression:'neutral'});
    }""")
    page.wait_for_function("() => window.lastFaceMotion?.jaw === 0")
    page.evaluate("window.faceAudio.currentTime = .3")
    page.wait_for_function("() => window.lastFaceMotion?.jaw === 9")
    page.evaluate("window.faceAudio.currentTime = .6")
    page.wait_for_function("() => window.lastFaceMotion?.jaw === 0")
    page.evaluate("window.faceAudio.currentTime = .3")
    page.wait_for_function("() => window.lastFaceMotion?.jaw === 9")
    page.evaluate("window.stopFaceAudio()")
    page.wait_for_function("() => window.lastFaceMotion?.jaw === 0")


def test_all_phone_faces_and_emotions_render_with_reduced_motion(web_page):
    page, _ = web_page
    page.emulate_media(reduced_motion="reduce")
    result = page.evaluate("""async () => {
      const face = await import('/face.js');
      const frames = [];
      for (const head of Object.keys(portraits)) {
        const original = portraits[head].features;
        portraits[head].features = (ctx, pose, motion, theme) => {
          window.lastRendered = {head, pose: {...pose}, motion: {...motion}};
          original(ctx, pose, motion, theme);
        };
        for (const expression of Object.keys(expressions)) {
          face.updateFace({head, expression, activity:'listening'});
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const canvas = document.querySelector('canvas');
          const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let hash = 2166136261;
          for (const pixel of pixels) hash = Math.imul(hash ^ pixel, 16777619);
          frames.push({...window.lastRendered, cue: expression, hash,
            matchesPose: JSON.stringify(window.lastRendered.pose) ===
              JSON.stringify(expressions[expression])});
        }
      }
      return frames;
    }""")
    assert len(result) == 48
    for frame in result:
        assert frame["motion"]["jaw"] == frame["motion"]["blink"] == 0
        assert frame["pose"]["lid"] > 0
        assert frame["matchesPose"]
    for head in {frame["head"] for frame in result}:
        assert len({frame["hash"] for frame in result if frame["head"] == head}) == 12
