import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import iris
from iris_ptt.hold_capture import record_while_held
from iris_ptt.push_space_to_talk import PTT


def test_windows_held_state_uses_current_key_state_not_recent_press_bit() -> None:
    keyboard = PTT.__new__(PTT)
    keyboard._key_state = Mock(side_effect=[1, 0x8000, 0])
    assert not keyboard.is_held()
    assert keyboard.is_held()
    assert not keyboard.is_held()


@pytest.mark.parametrize(
    "pending,held,expected", [(False, True, False), (True, False, False), (True, True, True)]
)
def test_start_needs_console_input_and_a_key_still_held(
    pending: bool,
    held: bool,
    expected: bool,
) -> None:
    keyboard = PTT.__new__(PTT)
    keyboard._key_state = Mock(return_value=0x8000 if held else 0)
    console = SimpleNamespace(
        kbhit=Mock(side_effect=[True, False] if pending else [False]), getwch=Mock(return_value=" ")
    )
    with (
        patch("iris_ptt.push_space_to_talk.sys.platform", "win32"),
        patch.dict(sys.modules, {"msvcrt": console}),
    ):
        assert keyboard.start_requested() is expected


def test_pauses_do_not_end_a_held_recording_and_leadin_is_bounded() -> None:
    source = SimpleNamespace(
        CHUNK=100,
        SAMPLE_RATE=1000,
        SAMPLE_WIDTH=1,
        stream=Mock(
            read=Mock(
                side_effect=[
                    b"a" * 100,
                    b"b" * 100,
                    b"c" * 100,
                    b"d" * 100,
                    b"speech" * 10,
                    b"\x00" * 100,
                    b"ending" * 10,
                ]
            )
        ),
    )
    audio = record_while_held(
        source,
        Mock(side_effect=[False] * 4 + [True]),
        Mock(side_effect=[False, True, True, True, False]),
        30,
    )
    assert (
        audio.frame_data
        == b"c" * 100 + b"d" * 100 + b"speech" * 10 + b"\x00" * 100 + b"ending" * 10
    )
    assert audio.sample_rate == 1000


def test_time_limit_is_exact_and_does_not_require_key_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = SimpleNamespace(
        CHUNK=4,
        SAMPLE_RATE=10,
        SAMPLE_WIDTH=1,
        stream=Mock(read=Mock(side_effect=lambda count: b"x" * count)),
    )
    held = Mock(side_effect=[False, True, True, True, True])
    audio = record_while_held(source, lambda: True, held, 1)
    assert audio.frame_data == b"x" * 10
    assert [call.args[0] for call in source.stream.read.call_args_list] == [4, 4, 2]
    assert "time limit reached" in capsys.readouterr().out


def test_key_held_from_previous_turn_must_be_released_before_rearming() -> None:
    source = SimpleNamespace(
        CHUNK=4,
        SAMPLE_RATE=10,
        SAMPLE_WIDTH=1,
        stream=Mock(read=Mock(side_effect=[b"old1", b"old2", b"new!"])),
    )
    audio = record_while_held(
        source, lambda: True, Mock(side_effect=[True, True, False, True, False]), 30
    )
    assert audio.frame_data == b"new!"


def test_empty_microphone_stream_fails_instead_of_spinning() -> None:
    source = SimpleNamespace(
        CHUNK=4, SAMPLE_RATE=10, SAMPLE_WIDTH=1, stream=Mock(read=Mock(return_value=b""))
    )
    with pytest.raises(OSError, match="stopped returning audio"):
        record_while_held(source, lambda: False, lambda: False, 30)


def test_hold_mode_opens_before_recording_and_skips_silence_detection() -> None:
    mic = Mock()
    source = Mock()
    mic.__enter__ = Mock(return_value=source)
    mic.__exit__ = Mock(return_value=False)
    recorder = Mock()
    stt = Mock()
    feedback = Mock()
    events = []
    recorder.record.side_effect = lambda *args: events.append("recorded") or Mock()
    mic.__exit__.side_effect = lambda *args: events.append("closed")
    stt.recognize.side_effect = lambda audio: events.append("transcribed") or "whole sentence"
    with patch("speech_recognition.Microphone", return_value=mic), patch("iris.stop_audio"):
        voice = iris.VoiceInput(iris.Settings(activation="space"), stt, recorder, feedback)
        voice.recognizer = Mock()
        assert voice.listen() == "whole sentence"
    recorder.record.assert_called_once_with(source, 30)
    assert events == ["recorded", "closed", "transcribed"]
    voice.recognizer.listen.assert_not_called()
    voice.recognizer.adjust_for_ambient_noise.assert_not_called()
    assert all(call.args != ("play_bleep",) for call in feedback.play.call_args_list)


def test_cancelled_hold_closes_microphone_and_stops_feedback() -> None:
    mic = Mock()
    mic.__enter__ = Mock(return_value=Mock())
    mic.__exit__ = Mock(return_value=False)
    recorder = Mock(record=Mock(side_effect=KeyboardInterrupt))
    with patch("speech_recognition.Microphone", return_value=mic), patch("iris.stop_audio") as stop:
        voice = iris.VoiceInput(iris.Settings(activation="space"), Mock(), recorder, Mock())
        with pytest.raises(KeyboardInterrupt):
            voice.listen()
    mic.__exit__.assert_called_once()
    stop.assert_called_once()
