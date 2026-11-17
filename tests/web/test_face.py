import pytest

from iris_droid.face_display import FaceDisplay

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("screensaver", ["black", "snow"])
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
