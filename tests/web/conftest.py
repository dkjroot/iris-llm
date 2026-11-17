"""Real browser, real app assets, synthetic HTTP/audio; no physical microphone."""

import base64
import json
import os
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import pytest

from iris_droid.protocol import write_wav
from iris_droid.server import handler_for


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            channel=os.environ.get("IRIS_TEST_BROWSER_CHANNEL") or None,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def web_page(browser, brain_factory):
    harness = brain_factory(replies=[])
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_for(harness.brain, "browser-test-key", web_chat=True)
    )
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # A real AudioContext/MediaStream with controllable generated samples exercises
    # the app's worklet, segmentation, WAV encoding, decode and playback paths.
    page.add_init_script("""(() => {
      let sourceAudio, oscillator, gain, destination;
      window.syntheticMic = {
        async start() {
          if (!sourceAudio) {
            sourceAudio = new AudioContext();
            destination = sourceAudio.createMediaStreamDestination();
            gain = sourceAudio.createGain(); gain.gain.value = 0;
            oscillator = sourceAudio.createOscillator(); oscillator.frequency.value = 440;
            oscillator.connect(gain); gain.connect(destination); oscillator.start();
          }
          await sourceAudio.resume();
          return destination.stream;
        },
        level(value) { gain.gain.value = value; },
        utterance() {
          const now = sourceAudio.currentTime;
          gain.gain.setValueAtTime(0.25, now);
          gain.gain.setValueAtTime(0, now + 0.3);
        },
        stopped() { return destination.stream.getAudioTracks()[0].readyState === 'ended'; },
      };
      navigator.mediaDevices.getUserMedia = () => window.syntheticMic.start();
    })();""")

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != urlsplit(origin).netloc:
            route.abort()
            errors.append(f"Unexpected external request: {url.netloc}")
            return
        if url.path == "/v1/web-personalities":
            route.fulfill(json=["Assistant", "Friend", "Comedian"])
            return
        if url.path in {"/v1/text", "/v1/voice"}:
            requests.append(
                {
                    "path": url.path,
                    "headers": request.all_headers(),
                    "body": request.post_data_buffer,
                }
            )
            if request.headers.get("authorization") != "Bearer browser-test-key":
                route.fulfill(status=401, json={"error": "Authentication required"})
                return
            body = request.post_data if url.path == "/v1/text" else "Synthetic speech"
            deleted = body == "delete previous"
            reply = {
                "heard": body,
                "text": "Previous exchange deleted." if deleted else "Hello from Iris.",
                "audio": base64.b64encode(write_wav(b"\0\0" * 2400, 24000)).decode()
                if url.path == "/v1/voice"
                else "",
                "finished": False,
                "companion_mode": False,
                "expression": "amused",
            }
            route.fulfill(
                json=reply,
                headers={
                    "X-Iris-Private": "0",
                    "X-Iris-Deleted-Exchange" if deleted else "X-Iris-Exchange": "exchange-1",
                },
            )
            return
        # Worklet module loading can bypass browser request interception. Serve
        # actual assets and security headers from the production loopback handler.
        route.continue_()

    context.route("**/*", serve)
    try:
        page.goto(origin + "/chat")
        page.locator("#key").fill("browser-test-key")
        yield page, requests
    finally:
        context.close()
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    assert not worker.is_alive()
    assert not errors, json.dumps(errors)
