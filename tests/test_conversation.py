import importlib
from unittest.mock import patch

import httpx
import pytest

import iris


def test_import_does_not_construct_models_devices_or_clients() -> None:
    with patch("httpx.Client") as client, patch("speech_recognition.Microphone") as mic:
        importlib.reload(iris)
        client.assert_not_called()
        mic.assert_not_called()


@pytest.mark.parametrize("command", ["server", "client"])
def test_cli_routes_to_the_same_droid_components(command: str) -> None:
    with patch(f"iris_droid.{command}.main", return_value=0) as run:
        assert iris.main([command, "--help"]) == 0
    run.assert_called_once_with(["--help"])


def test_no_arguments_shows_help_without_opening_devices(capsys) -> None:
    with patch("speech_recognition.Microphone") as microphone, patch("httpx.Client") as client:
        assert iris.main([]) == 0
    microphone.assert_not_called()
    client.assert_not_called()
    output = capsys.readouterr().out
    assert "server" in output and "client" in output


def test_server_check_shows_model_ids_for_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "koboldcpp/gemma-model"}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert iris.check_setup(iris.Settings(), client) == 0
    assert "koboldcpp/gemma-model" in capsys.readouterr().out


@pytest.mark.parametrize("body", [{"data": []}, {"unexpected": "format"}, {"data": [{"id": None}]}])
def test_server_check_rejects_empty_or_invalid_model_lists(body: dict[str, object]) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        assert iris.check_setup(iris.Settings(), client) == 1
