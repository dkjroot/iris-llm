import pytest

from tests.support import KnownReviewDefect, require_resolution

pytestmark = pytest.mark.artifact


def test_installed_entrypoints_and_resources_work_without_the_checkout(installed_artifact):
    assert set(installed_artifact["entrypoints"]) == {
        "iris",
        "iris-configure",
        "iris-droid",
        "iris-droid-server",
        "iris-home",
    }
    assert all(
        value for name, value in installed_artifact["modules"].items() if name != "configure_client"
    )
    assert installed_artifact["assets"]["iris_sounds/cancelled.wav"] > 44


@pytest.mark.review_regression
@pytest.mark.xfail(
    raises=KnownReviewDefect, strict=True, reason="R11: configure_client is missing from the wheel"
)
def test_client_wizard_is_available_in_the_installed_wheel(installed_artifact):
    require_resolution(
        installed_artifact["modules"]["configure_client"],
        "R11: the installed distribution has no client wizard",
    )
