"""Feedback ordering and cleanup with real assets, without opening audio devices."""

import base64
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
import speech_recognition as sr

import iris
from iris_droid.client import play_wav
from iris_droid.feedback import ClientFeedback
from iris_droid.protocol import write_wav

RECORDING = write_wav(b"\x01\x00" * 1600, 16000)
REPLY = write_wav(b"\x02\x00" * 1600, 16000)
CUES = {
    name: files("iris_sounds").joinpath(name).read_bytes()
    for name in ("chirp.wav", "bleep.wav", "longbleep.wav", "workinglongquiet.wav", "error.wav")
}


def player_for(events):
    def play(data, device=None, *, loop=False):
        assert device == 7
        name = next((name for name, cue in CUES.items() if cue == data), None)
        if name is not None:
            assert loop is (name == "workinglongquiet.wav")
            events.append(name)
        else:
            assert not loop
            events.append("reply" if data == REPLY else "echo")

    return play


@pytest.mark.parametrize("echo", [False, True])
@pytest.mark.parametrize("activation", ["enter", "continuous", "wake"])
def test_client_feedback_follows_turn_and_stops_before_reply_and_rearming(activation, echo):
    events = []
    captures = 0

    def record(*args, **kwargs):
        nonlocal captures
        captures += 1
        if kwargs.get("announce_ready"):
            kwargs["feedback"].ready()
        if captures == 2:
            raise KeyboardInterrupt()
        kwargs["feedback"].listening()
        events.append("capture")
        kwargs["feedback"].recorded()
        return RECORDING

    def turn(*args, **kwargs):
        events.append("request")
        return SimpleNamespace(
            heard="hello", text="hello", audio=base64.b64encode(REPLY).decode(), finished=False
        )

    argv = ["client", "--speaker", "7", "--activation", activation]
    if activation == "wake":
        argv.extend(["--wake-model", "."])
    if echo:
        argv.append("--echo-recording")
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("builtins.input", return_value=""),
        patch("iris_droid.client.WakeDetector"),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.play_wav", side_effect=player_for(events)),
        patch("iris_droid.client.stop_playback", side_effect=lambda: events.append("stop")),
        patch("iris_droid.client.DroidClient.turn", side_effect=turn),
    ):
        assert iris.main(argv) == 0
    assert events == (
        ["chirp.wav", "bleep.wav", "capture", "longbleep.wav"]
        + (["echo"] if echo else [])
        + ["workinglongquiet.wav", "request", "stop", "reply", "chirp.wav"]
    )


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), KeyboardInterrupt()])
def test_processing_failure_or_interrupt_stops_busy_sound(failure):
    events = []
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", side_effect=[RECORDING, KeyboardInterrupt()]),
        patch("iris_droid.client.play_wav", side_effect=player_for(events)),
        patch("iris_droid.client.stop_playback", side_effect=lambda: events.append("stop")),
        patch("iris_droid.client.DroidClient.turn", side_effect=failure) as turn,
    ):
        assert iris.main(["client", "--speaker", "7", "--activation", "continuous"]) == 0
    expected = ["workinglongquiet.wav", "stop"]
    if isinstance(failure, httpx.HTTPError):
        expected += ["error.wav"]
    assert events == expected
    turn.assert_called_once()


def test_silence_signals_error_without_starting_processing():
    events = []
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch(
            "iris_droid.client.record_turn",
            side_effect=[sr.WaitTimeoutError(), KeyboardInterrupt()],
        ),
        patch("iris_droid.client.play_wav", side_effect=player_for(events)),
        patch("iris_droid.client.DroidClient.turn") as turn,
    ):
        assert iris.main(["client", "--speaker", "7", "--activation", "continuous"]) == 0
    assert events == ["error.wav"]
    turn.assert_not_called()


def test_no_sounds_keeps_echo_and_spoken_reply():
    events = []
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", return_value=RECORDING),
        patch("iris_droid.client.play_wav", side_effect=player_for(events)),
        patch("iris_droid.client.stop_playback") as stop,
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=SimpleNamespace(
                heard="exit", text="goodbye", audio=base64.b64encode(REPLY).decode(), finished=True
            ),
        ),
    ):
        assert (
            iris.main(
                [
                    "client",
                    "--speaker",
                    "7",
                    "--activation",
                    "continuous",
                    "--no-sounds",
                    "--echo-recording",
                ]
            )
            == 0
        )
    assert events == ["echo", "reply"]
    stop.assert_not_called()


def test_text_mode_is_silent_without_requiring_no_sounds():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("builtins.input", return_value="exit"),
        patch("iris_droid.client.play_wav") as play,
        patch("iris_droid.client.stop_playback") as stop,
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=SimpleNamespace(heard="exit", text="goodbye", audio="", finished=True),
        ),
    ):
        assert iris.main(["client", "--text"]) == 0
    play.assert_not_called()
    stop.assert_not_called()


def test_optional_feedback_failure_warns_once_and_does_not_break_turn(capsys):
    player = Mock(side_effect=OSError("Speaker unavailable"))
    stop = Mock()
    with ClientFeedback(7, player, stop) as feedback:
        feedback.ready()
        feedback.listening()
        with feedback.working():
            pass
    player.assert_called_once()
    assert capsys.readouterr().out.count("continuing without cues") == 1


def test_failed_busy_start_stops_playback_and_context_still_runs(capsys):
    player = Mock(side_effect=OSError("Stream failed"))
    stop = Mock()
    with ClientFeedback(7, player, stop) as feedback:
        with feedback.working():
            assert not feedback.enabled
    stop.assert_called_once()
    assert "continuing without cues" in capsys.readouterr().out


def test_busy_loop_resamples_for_selected_speaker():
    import sounddevice as sd

    with (
        patch("sounddevice.check_output_settings", side_effect=[sd.PortAudioError("rate"), None]),
        patch("sounddevice.query_devices", return_value={"default_samplerate": 44100}),
        patch("sounddevice.play") as play,
    ):
        play_wav(CUES["workinglongquiet.wav"], 7, loop=True)
    samples, rate = play.call_args.args
    assert rate == 44100 and abs(len(samples) / rate - 3.6) < 0.001
    assert play.call_args.kwargs == {"device": 7, "blocking": False, "loop": True}


def test_recording_cues_run_with_microphone_closed():
    from iris_droid.client import record_turn

    opened = False
    events = []
    source = Mock(SAMPLE_RATE=44100, CHUNK=8192)
    microphone = Mock()

    def enter():
        nonlocal opened
        opened = True
        events.append("open")
        return source

    def close(*args):
        nonlocal opened
        opened = False
        events.append("close")

    def play(data, device=None, *, loop=False):
        assert not opened and not loop
        events.append("bleep" if data == CUES["bleep.wav"] else "recorded")

    microphone.__enter__ = Mock(side_effect=enter)
    microphone.__exit__ = Mock(side_effect=close)
    recognizer = Mock()
    recognizer.listen.return_value = sr.AudioData(b"\x01\x00" * 1600, 16000, 2)
    with (
        patch("speech_recognition.Microphone", return_value=microphone),
        patch("speech_recognition.Recognizer", return_value=recognizer),
    ):
        record_turn(1, 30, 10, None, feedback=ClientFeedback(7, play, Mock()))
    assert events == ["open", "close", "bleep", "open", "close", "recorded"]
    assert not opened


def test_wake_ready_cue_follows_calibration_with_input_closed():
    from iris_droid.client import record_turn

    opened = False
    events = []
    source = Mock(SAMPLE_RATE=44100, CHUNK=8192)
    microphone = Mock()

    def enter():
        nonlocal opened
        opened = True
        events.append("open")
        return source

    def close(*args):
        nonlocal opened
        opened = False
        events.append("close")

    def play(data, device=None, *, loop=False):
        assert not opened
        events.append(next(name for name, cue in CUES.items() if cue == data))

    def wait(source):
        assert opened
        events.append("wake")

    microphone.__enter__ = Mock(side_effect=enter)
    microphone.__exit__ = Mock(side_effect=close)
    recognizer = Mock()
    recognizer.adjust_for_ambient_noise.side_effect = lambda *a, **kw: events.append("calibrate")
    recognizer.listen.return_value = sr.AudioData(b"\x01\x00" * 1600, 16000, 2)
    with (
        patch("speech_recognition.Microphone", return_value=microphone),
        patch("speech_recognition.Recognizer", return_value=recognizer),
    ):
        record_turn(
            1,
            30,
            10,
            Mock(wait=wait),
            feedback=ClientFeedback(7, play, Mock()),
            announce_ready=True,
        )
    assert events == [
        "open",
        "calibrate",
        "close",
        "chirp.wav",
        "open",
        "wake",
        "close",
        "bleep.wav",
        "open",
        "close",
        "longbleep.wav",
    ]


def test_missing_asset_disables_feedback_without_failing_request(capsys):
    player, stop = Mock(), Mock()
    with (
        ClientFeedback(7, player, stop) as feedback,
        patch.object(feedback, "_load", side_effect=FileNotFoundError("missing cue")),
    ):
        feedback.ready()
        with feedback.working():
            pass
    player.assert_not_called()
    assert "missing cue" in capsys.readouterr().out


def test_invalid_reply_stops_busy_sound_and_signals_error():
    events = []
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", return_value=RECORDING),
        patch("iris_droid.client.play_wav", side_effect=player_for(events)),
        patch("iris_droid.client.stop_playback", side_effect=lambda: events.append("stop")),
        patch("iris_droid.client.DroidClient.turn", side_effect=ValueError("invalid reply")),
    ):
        assert iris.main(["client", "--speaker", "7", "--activation", "continuous"]) == 1
    assert events == ["workinglongquiet.wav", "stop", "error.wav"]
