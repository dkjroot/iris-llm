import importlib
from unittest.mock import Mock, patch

import httpx
import pytest

import iris
from iris_chat_engines.compatible import ChatError


def test_printed_and_spoken_reply_are_the_same(capsys: pytest.CaptureFixture[str]) -> None:
    chat = Mock()
    chat.get_response.return_value = "I'm fine & well"
    tts = Mock()
    listen = Mock(side_effect=[None, "", "hello", " EXIT "])
    stop = Mock()
    iris.conversation(chat, listen, tts, stop=stop)
    chat.get_response.assert_called_once_with("hello")
    assert "Iris: I'm fine & well" in capsys.readouterr().out
    assert tts.speak.call_args_list[1].args == ("I'm fine & well",)
    stop.assert_called_once()


def test_failed_turn_stops_feedback_and_does_not_speak_a_failure() -> None:
    chat = Mock()
    chat.get_response.side_effect = [ChatError("server unavailable"), "recovered"]
    tts = Mock()
    stop = Mock()
    iris.conversation(chat, Mock(side_effect=["first", "second", "exit"]), tts, stop=stop)
    assert stop.call_count == 2
    assert [call.args[0] for call in tts.speak.call_args_list] == [
        "Nice to see you again, what's up?",
        "recovered",
        "Shutting down",
    ]


def test_import_does_not_construct_models_devices_or_clients() -> None:
    with patch("httpx.Client") as client, patch("speech_recognition.Microphone") as mic:
        importlib.reload(iris)
        client.assert_not_called()
        mic.assert_not_called()


def test_activation_failure_does_not_open_microphone() -> None:
    activation = Mock()
    activation.wait_to_talk.return_value = False
    with patch("speech_recognition.Microphone") as mic:
        voice = iris.VoiceInput(iris.Settings(), Mock(), activation, Mock())
        assert voice.listen() is None
        mic.assert_not_called()


def test_microphone_is_closed_before_transcription_and_feedback_always_stops() -> None:
    events: list[str] = []
    mic = Mock()
    mic.__enter__ = Mock(return_value=Mock())
    mic.__exit__ = Mock(side_effect=lambda *args: events.append("closed"))
    stt = Mock()
    stt.recognize.side_effect = lambda audio: events.append("transcribed") or "hello"
    with patch("speech_recognition.Microphone", return_value=mic), patch("iris.stop_audio") as stop:
        voice = iris.VoiceInput(iris.Settings(), stt, Mock(), Mock())
        voice.recognizer = Mock()
        assert voice.listen() == "hello"
        assert events == ["closed", "transcribed"]
        assert voice.recognizer.listen.call_args.kwargs == {"timeout": 10, "phrase_time_limit": 30}
        stop.assert_called_once()


def test_text_mode_does_not_construct_speech_providers() -> None:
    with (
        patch("iris.load_settings", return_value=iris.Settings()),
        patch("iris.ChatEngine") as engine,
        patch("builtins.input", side_effect=["hello", "exit"]),
        patch("iris.make_stt") as stt,
        patch("iris.make_tts") as tts,
    ):
        engine.return_value.get_response.return_value = "hi"
        assert iris.main(["--text"]) == 0
        stt.assert_not_called()
        tts.assert_not_called()


def test_missing_optional_sound_asset_falls_back_to_silence() -> None:
    with (
        patch("iris_sounds.beeps_and_bleeps.Sounds", side_effect=FileNotFoundError("cue")),
        patch("sounddevice.play") as play,
    ):
        feedback = iris.Feedback(iris.Settings())
        feedback.play("play_working_sound")
        play.assert_not_called()


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
