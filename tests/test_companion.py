"""Companion controls, idle generation and pacing without models or audio devices."""

import base64
import json
from http.server import HTTPServer
from threading import Event, Thread
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
import speech_recognition as sr

import iris
from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_droid.client import DroidClient, record_turn
from iris_droid.companion import COMPANION_PROMPT, ICE_BREAKERS, CompanionTimer, ConsoleInput
from iris_droid.protocol import TurnReply, companion_command, read_wav, write_wav
from iris_droid.server import Brain, handler_for
from iris_settings import Settings

TOKEN = "companion-test-connection-key"
RECORDING = write_wav(b"\x01\x00" * 1600, 16000)


def make_brain(**options):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Robot thought number {len(requests)}.",
                        }
                    }
                ]
            },
        )

    transport = httpx.Client(transport=httpx.MockTransport(handle))
    brain = Brain(
        Settings(**options),
        transport,
        Mock(recognize=Mock(return_value="companion mode on")),
        Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32), 24000))),
    )
    return brain, transport, requests


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("Companion mode on!", True),
        ("  COMPANION   mode OFF. ", False),
        ("don't turn companion mode on", None),
        ("companion mode off please", None),
    ],
)
def test_only_complete_control_phrases_match(phrase, expected):
    assert companion_command(phrase) is expected


@pytest.mark.parametrize("voice", [False, True])
def test_commands_bypass_model_preserve_history_and_affect_only_this_session(voice):
    brain, transport, requests = make_brain()
    with transport:
        brain.turn("pi", b"hello", text_only=True)
        before = brain.sessions["pi"].history.copy()
        brain.turn("other", b"hello", text_only=True)
        on = brain.turn("pi", RECORDING if voice else b"companion mode on", text_only=not voice)
        assert on.companion_mode and bool(on.audio) == voice
        assert len(requests) == 2 and brain.sessions["pi"].history == before
        brain.turn("pi", b"hello again", text_only=True)
        assert COMPANION_PROMPT in requests[-1]["messages"][0]["content"]
        brain.turn("other", b"hello again", text_only=True)
        assert COMPANION_PROMPT not in requests[-1]["messages"][0]["content"]
        off = brain.turn("pi", b"companion mode off", text_only=True)
        assert not off.companion_mode
        brain.turn("pi", b"hello again", text_only=True)
        assert COMPANION_PROMPT not in requests[-1]["messages"][0]["content"]


def test_idle_is_not_recognized_or_stored_as_human_speech_and_has_no_tools():
    brain, transport, requests = make_brain(history_turns=3)
    with transport:
        brain.turn("pi", b"companion mode on", text_only=True)
        brain.turn("pi", b"I had a strange day at work", text_only=True)
        brain.stt.recognize.reset_mock()
        for _ in range(8):
            reply = brain.turn("pi", b"idle", companion_idle=True)
            assert reply.companion_mode and reply.heard == "" and reply.audio
            assert read_wav(base64.b64decode(reply.audio))[1] == 24000
        assert "tools" not in requests[-1]
        history = brain.sessions["pi"].history
        assert [m.role for m in history] == ["user", "assistant"] * 3
        assert all("not human speech" in m.content for m in history if m.role == "user")
        assert len(history) == 6
        assert "strange day at work" in str(requests[1])
        seeds = [next(seed for seed in ICE_BREAKERS if seed in str(r)) for r in requests[1:]]
        assert all(a != b for a, b in zip(seeds, seeds[1:], strict=False))
        brain.stt.recognize.assert_not_called()


def test_idle_requires_enabled_existing_session_and_clear_preserves_mode():
    brain, transport, requests = make_brain()
    with transport:
        assert not brain.turn("missing", b"idle", companion_idle=True).companion_mode
        assert not requests and not brain.sessions
        brain.turn("pi", b"companion mode on", text_only=True)
        brain.turn("pi", b"hello", text_only=True)
        assert brain.turn("pi", b"clear context", text_only=True).companion_mode
        assert not brain.sessions["pi"].history
        assert not brain.turn("pi", b"stop listening", text_only=True).companion_mode
        assert not brain.turn("pi", b"idle", companion_idle=True).companion_mode
        assert len(requests) == 1
        brain.tts.synthesize.assert_not_called()


def test_synthesis_failure_rolls_back_mode_and_idle_history_and_seed():
    brain, transport, _ = make_brain()
    with transport:
        brain.tts.synthesize.side_effect = RuntimeError("speaker service failed")
        with pytest.raises(RuntimeError):
            brain.turn("pi", RECORDING)
        assert not brain.sessions["pi"].companion_mode
        brain.turn("pi", b"companion mode on", text_only=True)
        chat = brain.sessions["pi"]
        with pytest.raises(RuntimeError):
            brain.turn("pi", b"idle", companion_idle=True)
        assert chat.history == [] and chat.last_companion_seed is None
        assert chat.companion_mode


@pytest.mark.parametrize("content", [None, " "])
def test_bad_opener_does_not_commit_history(content):
    with httpx.Client() as transport:
        backend = Mock(
            complete=Mock(return_value=Mock(role="assistant", tool_calls=None, content=content))
        )
        chat = ChatEngine(Settings(), transport, backend=backend)
        chat.companion_mode = True
        with pytest.raises(ChatError):
            chat.get_companion_opener()
        assert chat.history == [] and chat.last_companion_seed is None


def test_opener_cannot_execute_model_requested_tools():
    with httpx.Client() as transport:
        backend = Mock(complete=Mock(return_value=Mock(role="assistant", tool_calls=[Mock()])))
        chat = ChatEngine(Settings(), transport, backend=backend)
        chat.companion_mode = True
        with patch.object(chat.tools, "execute") as execute, pytest.raises(ChatError):
            chat.get_companion_opener()
        execute.assert_not_called()
        assert chat.history == []


def test_timer_randomizes_backs_off_caps_and_resets_after_human_reply():
    now = [100.0]
    random_delay = Mock(side_effect=[90, 240, 90, 240, 90, 90])
    timer = CompanionTimer(clock=lambda: now[0], uniform=random_delay)
    assert not timer.enabled and timer.remaining() == 0
    timer.update(True)
    assert timer.remaining() == 90
    now[0] += 90
    assert timer.remaining() == 0
    for expected in [480, 360, 1920, 720]:
        timer.update(True, proactive=True)
        assert timer.remaining() == expected
    timer.update(True)
    assert timer.unanswered == 0 and timer.remaining() == 90
    timer.update(False)
    assert timer.remaining() == 0
    assert random_delay.call_args_list == [((90, 240),)] * 6


@pytest.mark.parametrize(
    "minimum, maximum", [(4, 90), (90, 89), (90, 3601), (float("nan"), 90), (90, float("inf"))]
)
def test_invalid_timer_options_fail_before_network_or_devices(minimum, maximum):
    with patch("httpx.Client") as transport, patch("iris_droid.client.record_turn") as record:
        assert (
            iris.main(
                [
                    "client",
                    "--companion-idle-min",
                    str(minimum),
                    "--companion-idle-max",
                    str(maximum),
                ]
            )
            == 1
        )
    transport.assert_not_called()
    record.assert_not_called()


@pytest.mark.parametrize("elapsed", [0, 100])
def test_recording_waits_for_companion_deadline_instead_of_normal_timeout(elapsed):
    now = [0]
    timer = CompanionTimer(clock=lambda: now[0], uniform=lambda lo, hi: 90)
    timer.update(True)
    now[0] = elapsed
    recognizer = Mock(listen=Mock(side_effect=sr.WaitTimeoutError()))
    with (
        patch("iris_droid.client.open_microphone"),
        patch("speech_recognition.Recognizer", return_value=recognizer),
        pytest.raises(sr.WaitTimeoutError),
    ):
        record_turn(1, 30, 10, None, energy_threshold=100, companion_timer=timer, feedback=Mock())
    if elapsed == 0:
        assert recognizer.listen.call_args.kwargs == {"timeout": 90, "phrase_time_limit": 30}
    else:
        recognizer.listen.assert_not_called()


@pytest.mark.parametrize("failure", ["unrecognized", "offline"])
def test_failed_voice_turns_preserve_companion_deadline_and_unanswered_backoff(failure):
    now = [0.0]
    timer = CompanionTimer(clock=lambda: now[0], uniform=lambda lo, hi: 90)
    request = httpx.Request("POST", "http://iris/turn")
    error = (
        httpx.HTTPStatusError(
            "No speech recognized", request=request, response=httpx.Response(422, request=request)
        )
        if failure == "unrecognized"
        else httpx.ConnectError("offline", request=request)
    )
    captures = 0

    def record(*args, **kwargs):
        nonlocal captures
        captures += 1
        if captures == 2:
            now[0] = 90
            raise sr.WaitTimeoutError()
        if captures == 3:
            assert timer.unanswered == 1 and timer.remaining() == 180
            now[0] = 120
        if captures == 4:
            assert timer.unanswered == 1 and timer.remaining() == 150
            now[0] = 200
        if captures == 5:
            assert timer.unanswered == 1 and timer.remaining() == 70
            now[0] = 280
            raise sr.WaitTimeoutError()
        if captures == 6:
            assert timer.unanswered == 2 and timer.remaining() == 360
        if captures == 7:
            assert timer.unanswered == 0 and timer.remaining() == 90
        return RECORDING

    def play(*args, **kwargs):
        now[0] += 20  # The fresh human-turn interval must start after playback.

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.CompanionTimer", return_value=timer),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.play_wav", side_effect=play),
        patch(
            "iris_droid.client.DroidClient.turn",
            side_effect=[
                TurnReply(heard="companion mode on", text="On", companion_mode=True),
                TurnReply(heard="", text="First opener", companion_mode=True),
                error,
                error,
                TurnReply(heard="", text="Second opener", companion_mode=True),
                TurnReply(
                    heard="I'm making supper",
                    text="What's cooking?",
                    audio=base64.b64encode(RECORDING).decode(),
                    companion_mode=True,
                ),
                TurnReply(heard="exit", text="Bye", finished=True),
            ],
        ) as turn,
    ):
        assert iris.main(["client", "--no-sounds", "--activation", "continuous"]) == 0
    assert turn.call_count == 7
    assert turn.call_args_list[4].kwargs == {"text_only": False, "companion_idle": True}


@pytest.mark.parametrize("activation", ["enter", "continuous", "wake"])
def test_voice_client_silence_generates_opener_then_off_restores_activation(activation):
    replies = iter(
        [
            TurnReply(heard="companion mode on", text="On", companion_mode=True),
            TurnReply(
                heard="",
                text="Funny thought",
                audio=base64.b64encode(RECORDING).decode(),
                companion_mode=True,
            ),
            TurnReply(heard="companion mode off", text="Off"),
            TurnReply(heard="exit", text="Bye", finished=True),
        ]
    )
    captures = []
    timer = CompanionTimer(uniform=lambda lo, hi: 90)

    def record(*args, **kwargs):
        captures.append((args[3], kwargs))
        if len(captures) == 2:
            assert kwargs["companion_timer"] is timer
            raise sr.WaitTimeoutError()
        if len(captures) == 3:
            assert timer.remaining() > 170  # Full backed-off interval after playback.
        return RECORDING

    def play(*args, **kwargs):
        # Simulate slow playback; timing must be restarted after this returns.
        timer._deadline = 0

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.CompanionTimer", return_value=timer),
        patch("iris_droid.client.WakeDetector"),
        patch("builtins.input", return_value="") as console,
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.play_wav", side_effect=play) as playback,
        patch(
            "iris_droid.client.DroidClient.turn", side_effect=lambda *a, **kw: next(replies)
        ) as turn,
    ):
        argv = ["client", "--no-sounds", "--activation", activation]
        if activation == "wake":
            argv += ["--wake-model", "."]
        assert iris.main(argv) == 0
    assert turn.call_args_list[1].kwargs == {"text_only": False, "companion_idle": True}
    assert captures[1][0] is None and captures[2][0] is None
    assert "companion_timer" not in captures[3][1]
    assert captures[3][0] is None  # Wake conversation stays open after mode off.
    assert console.call_count == (2 if activation == "enter" else 0)
    assert playback.call_count == 1


def test_text_client_timeout_requests_idle_without_fabricated_human_message():
    replies = [
        TurnReply(heard="companion mode on", text="On", companion_mode=True),
        TurnReply(heard="", text="Funny thought", companion_mode=True),
        TurnReply(heard="exit", text="Bye", finished=True),
    ]
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch(
            "iris_droid.client.ConsoleInput.read", side_effect=["companion mode on", None, "exit"]
        ),
        patch("iris_droid.client.DroidClient.turn", side_effect=replies) as turn,
        patch("iris_droid.client.record_turn") as record,
    ):
        assert iris.main(["client", "--text"]) == 0
    record.assert_not_called()
    assert turn.call_args_list[1].kwargs == {"text_only": True, "companion_idle": True}


def test_failed_idle_request_backs_off_instead_of_retrying_immediately():
    timer = CompanionTimer(uniform=lambda lo, hi: 90)
    reads = 0

    def read(*args):
        nonlocal reads
        reads += 1
        if reads == 1:
            return "companion mode on"
        if reads == 2:
            return None
        assert timer.unanswered == 1 and timer.remaining() > 170
        return "exit"

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.CompanionTimer", return_value=timer),
        patch("iris_droid.client.ConsoleInput.read", side_effect=read),
        patch(
            "iris_droid.client.DroidClient.turn",
            side_effect=[
                TurnReply(heard="companion mode on", text="On", companion_mode=True),
                httpx.ConnectError("offline"),
                TurnReply(heard="exit", text="Bye", finished=True),
            ],
        ) as turn,
    ):
        assert iris.main(["client", "--text"]) == 0
    assert turn.call_count == 3


def test_console_timeout_keeps_one_reader_and_does_not_lose_the_next_line():
    entered, release = Event(), Event()

    def read(prompt):
        entered.set()
        assert release.wait(2)
        return "companion mode off"

    console = ConsoleInput()
    with patch("builtins.input", side_effect=read) as user_input:
        try:
            assert console.read("You: ", 0.01) is None
            assert entered.wait(1)
            assert console.read("You: ", 0.01) is None
            release.set()
            assert console.read("You: ", 1) == "companion mode off"
        finally:
            release.set()
    user_input.assert_called_once()


def test_real_http_idle_roundtrip_returns_speech_without_recognition():
    brain, transport, requests = make_brain()
    with transport, HTTPServer(("127.0.0.1", 0), handler_for(brain, TOKEN)) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with httpx.Client(trust_env=False, timeout=5) as http:
                client = DroidClient(f"http://127.0.0.1:{server.server_port}", TOKEN, http)
                assert not client.turn(b"idle", companion_idle=True).companion_mode
                assert client.turn(b"companion mode on", text_only=True).companion_mode
                reply = client.turn(b"idle", companion_idle=True)
                assert reply.heard == "" and reply.audio and reply.companion_mode
                assert len(requests) == 1
                brain.stt.recognize.assert_not_called()
                assert not client.turn(b"companion mode off", text_only=True).companion_mode
                assert not client.turn(b"idle", companion_idle=True).audio
        finally:
            server.shutdown()
            worker.join(timeout=5)
