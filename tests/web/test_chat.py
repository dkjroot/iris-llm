import pytest

from iris_droid.protocol import read_wav
from tests.support import require_resolution

pytestmark = pytest.mark.browser


def test_text_turn_and_delete_previous_edit_the_matching_transcript(web_page):
    from playwright.sync_api import expect

    page, requests = web_page
    page.locator("#message").fill("Hello Iris")
    page.locator("#send").click()
    expect(page.locator("#messages article")).to_have_count(2)
    expect(page.locator("#status")).to_have_text("Ready")
    page.locator("#message").fill("delete previous")
    page.locator("#send").click()
    expect(page.locator("#messages article")).to_have_count(1)
    expect(page.locator("#messages")).to_contain_text("Previous exchange deleted.")
    assert [r["path"] for r in requests] == ["/v1/text", "/v1/text"]
    assert requests[0]["headers"]["x-iris-session"] == requests[1]["headers"]["x-iris-session"]


def test_menu_personality_and_saved_face_survive_the_expected_boundaries(web_page):
    from playwright.sync_api import expect

    page, requests = web_page
    page.get_by_role("button", name="Open Iris menu").click()
    page.get_by_role("button", name="Personalities", exact=True).click()
    page.get_by_role("button", name="Friend", exact=True).click()
    expect(page.get_by_role("button", name="Friend", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Open Iris menu")).to_be_focused()
    page.locator("#message").fill("Hello")
    page.locator("#send").click()
    expect(page.locator("#status")).to_have_text("Ready")
    assert requests[-1]["headers"]["x-iris-web-personality"] == "Friend"
    before = page.evaluate("async () => (await import('/face.js')).savedFace()")
    page.get_by_role("button", name="Next face").click()
    after = page.evaluate("async () => (await import('/face.js')).savedFace()")
    assert before != after
    page.reload()
    assert page.evaluate("async () => (await import('/face.js')).savedFace()") == after


def test_synthetic_microphone_turn_plays_reply_and_resumes_listening(web_page):
    from playwright.sync_api import expect

    page, requests = web_page
    page.locator("#mic").click()
    expect(page.locator("#status")).to_have_text("Listening…")
    # Schedule a 300 ms signal on the audio clock; assertions wait for real app events.
    page.evaluate("window.syntheticMic.utterance()")
    expect(page.locator("#messages article")).to_have_count(2, timeout=10000)
    expect(page.locator("#status")).to_have_text("Listening…", timeout=10000)
    assert len(requests) == 1 and requests[0]["path"] == "/v1/voice"
    pcm, rate = read_wav(requests[0]["body"], input_audio=True)
    assert rate == 16000 and len(pcm) > rate // 5 * 2
    page.locator("#mic").click()
    expect(page.locator("#mic")).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("window.syntheticMic.stopped()")


def test_authentication_failure_leaves_chat_usable(web_page):
    from playwright.sync_api import expect

    page, _ = web_page
    page.locator("#key").fill("wrong")
    page.locator("#message").fill("Hello")
    page.locator("#send").click()
    expect(page.locator("#status")).to_have_text("Authentication required")
    expect(page.locator("#send")).to_be_enabled()
    expect(page.locator("#key")).to_be_editable()
    expect(page.locator("#messages article")).to_have_count(0)


@pytest.mark.review_regression
def test_browser_keeps_newer_menu_selection_while_voice_switch_is_pending(web_page):
    from playwright.sync_api import expect

    page, _ = web_page
    held = []
    page.route("**/v1/text", lambda route: held.append(route))
    page.locator("#message").fill("switch to personality Assistant")
    with page.expect_request("**/v1/text"):
        page.locator("#send").click()
    expect(page.locator("#send")).to_be_disabled()
    page.get_by_role("button", name="Open Iris menu").click()
    page.get_by_role("button", name="Personalities", exact=True).click()
    friend = page.get_by_role("button", name="Friend", exact=True)
    friend.click()
    expect(friend).to_have_attribute("aria-pressed", "true")
    assert len(held) == 1
    held[0].fulfill(
        json={
            "heard": "switch to personality Assistant",
            "text": "Switched.",
            "audio": "",
            "finished": False,
            "companion_mode": False,
            "selected_personality": "Assistant",
        }
    )
    expect(page.locator("#status")).to_have_text("Ready")
    require_resolution(
        friend.get_attribute("aria-pressed") == "true",
        "R10: browser replaced the newer Friend selection",
    )


def test_turn_timeout_restores_controls_without_retrying_or_displaying_success(web_page):
    from playwright.sync_api import expect

    page, _ = web_page
    page.evaluate("""() => {
      const timeout = AbortSignal.timeout.bind(AbortSignal);
      AbortSignal.timeout = milliseconds => {
        if (milliseconds <= 5000) return timeout(milliseconds);
        const controller = new AbortController();
        window.expireTurn = () => controller.abort(new DOMException('Timed out', 'TimeoutError'));
        return controller.signal;
      };
    }""")
    held = []
    page.route("**/v1/text", lambda route: held.append(route))
    page.locator("#message").fill("Turn on the lamp")
    with page.expect_request("**/v1/text"):
        page.locator("#send").click()
    expect(page.locator("#send")).to_be_disabled()
    page.evaluate("window.expireTurn()")
    expect(page.locator("#status")).to_have_text(
        "Iris took too long. Check any device action before trying again."
    )
    expect(page.locator("#send")).to_be_enabled()
    expect(page.locator("#key")).to_be_editable()
    expect(page.locator("#messages article")).to_have_count(0)
    assert len(held) == 1
