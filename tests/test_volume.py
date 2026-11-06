import base64
import json
import subprocess
import threading
from http.server import HTTPServer
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from iris_droid.client import DroidClient
from iris_droid.protocol import TurnReply, read_wav, write_wav
from iris_droid.server import Brain, handler_for
from iris_droid.volume import (
    AlsaVolume,
    VolumeCommand,
    VolumeResult,
    VolumeState,
    execute_volume,
    make_volume_backend,
    volume_command,
)
from iris_settings import Settings

TOKEN = "volume-test-token-long-enough"
MIXER = """Simple mixer control 'Master',0
  Capabilities: pvolume pswitch
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 100
  Front Left: Playback 40 [40%] [-20.00dB] [on]
  Front Right: Playback 50 [50%] [-15.00dB] [on]
  Capture channels: Front Left
  Front Left: Capture 90 [90%] [on]
"""


@pytest.mark.parametrize(
    "phrase,action,value",
    [
        ("Set volume to 50%.", "set", 50),
        ("set the volume to fifty percent", "set", 50),
        ("Please set speaker volume to twenty-five percent!", "set", 25),
        ("set volume to zero", "set", 0),
        ("set volume to one hundred percent", "set", 100),
        ("volume 30", "set", 30),
        ("volume up", "change", 10),
        ("turn it down", "change", -10),
        ("turn the volume down by five percent", "change", -5),
        ("increase volume by 20%", "change", 20),
        ("lower the volume by ten", "change", -10),
        ("mute the speaker", "mute", None),
        ("unmute", "unmute", None),
        ("What's the volume?", "get", None),
    ],
)
def test_commands(phrase, action, value):
    assert volume_command(phrase) == VolumeCommand(action=action, value=value)


@pytest.mark.parametrize(
    "phrase",
    [
        "I said set volume to 50%",
        "don't mute",
        "what does volume up mean",
        "set volume to -5%",
        "set volume to 101%",
        "set volume to 50%2",
        "set volume to 5.5%",
        "set volume to 50%; reboot",
        "increase volume by zero",
    ],
)
def test_non_commands_never_change_the_mixer(phrase):
    assert volume_command(phrase) is None


@pytest.mark.parametrize(
    "data",
    [
        {"action": "set", "value": True},
        {"action": "set", "value": 101},
        {"action": "set"},
        {"action": "change", "value": 0},
        {"action": "change", "value": -101},
        {"action": "mute", "value": 10},
        {"action": "shell", "value": 10},
        {"action": "get", "card": "x"},
    ],
)
def test_wire_commands_are_validated(data):
    with pytest.raises(ValidationError):
        VolumeCommand.model_validate(data)


@pytest.mark.parametrize(
    "action,value,argument",
    [
        ("set", 50, "50%"),
        ("change", 10, "10%+"),
        ("change", -10, "10%-"),
        ("mute", None, "mute"),
        ("unmute", None, "unmute"),
    ],
)
def test_alsa_uses_mapped_playback_volume_and_reads_back_actual_levels(action, value, argument):
    changed = MIXER.replace("[40%]", "[49%]").replace("[50%]", "[49%]")
    with patch(
        "iris_droid.volume.subprocess.run",
        side_effect=[
            Mock(stdout=MIXER),
            Mock(stdout=""),
            Mock(stdout=changed),
        ],
    ) as run:
        state = AlsaVolume("Device", "PCM").execute(VolumeCommand(action=action, value=value))
    assert state.levels == [49, 49] and state.muted is False
    assert run.call_args_list[1].args[0] == [
        "amixer",
        "-M",
        "-c",
        "Device",
        "--",
        "sset",
        "PCM",
        "playback",
        argument,
    ]
    assert run.call_args.kwargs["timeout"] == 5
    assert run.call_args.kwargs["env"]["LC_ALL"] == "C"
    assert "shell" not in run.call_args.kwargs


def test_get_reports_channel_range_and_mute():
    with patch("iris_droid.volume.subprocess.run", return_value=Mock(stdout=MIXER)) as run:
        state = AlsaVolume().execute(VolumeCommand(action="get"))
    assert run.call_count == 1
    assert state.description() == "Speaker volume is 40 to 50 percent."
    state.muted = True
    assert state.description().endswith("The speaker is muted.")


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("amixer", 5),
        subprocess.CalledProcessError(1, "amixer"),
    ],
)
def test_alsa_failures_return_no_success(failure):
    with patch("iris_droid.volume.subprocess.run", side_effect=failure):
        assert execute_volume(AlsaVolume(), VolumeCommand(action="get")).error == "unavailable"


@pytest.mark.parametrize(
    "output,action",
    [
        ("Front Left: Capture 60 [60%] [on]", "set"),
        ("Front Left: Playback 20", "set"),
        ("Front Left: Playback 20 [20%]", "mute"),
    ],
)
def test_unsupported_controls_are_not_changed(output, action):
    command = VolumeCommand(action=action, value=50 if action == "set" else None)
    with patch("iris_droid.volume.subprocess.run", return_value=Mock(stdout=output)) as run:
        assert execute_volume(AlsaVolume(), command).error == "unavailable"
    assert run.call_count == 1


def make_brain():
    def reject_chat(request):
        pytest.fail("A volume command must bypass the chat model")

    transport = httpx.Client(transport=httpx.MockTransport(reject_chat))
    brain = Brain(
        Settings(tools_enabled=False),
        transport,
        Mock(recognize=Mock(return_value="set volume to fifty percent")),
        Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32), 24000))),
    )
    return brain, transport


def test_server_routes_voice_without_chat_tts_or_history_and_supports_old_clients():
    brain, transport = make_brain()
    with transport:
        reply = brain.turn("pi", write_wav(b"\x00\x00" * 1600, 16000), volume_supported=True)
        assert reply.volume == VolumeCommand(action="set", value=50)
        assert not reply.audio and not reply.text and not brain.sessions
        brain.tts.synthesize.assert_not_called()
        old = brain.turn("old", b"volume up", text_only=True)
        assert old.volume is None and "update the client" in old.text
        assert "volume" not in json.loads(old.model_dump_json(exclude_none=True))


@pytest.mark.parametrize("mode", ["enabled", "disabled", "failure", "private"])
def test_real_http_volume_roundtrip_speaks_actual_result_without_chat(mode):
    brain, transport = make_brain()
    backend = Mock(execute=Mock(return_value=VolumeState(levels=[49])))
    if mode == "failure":
        backend.execute.side_effect = OSError("missing mixer")
    if mode == "private":
        brain._set_private(True)
    with transport, HTTPServer(("127.0.0.1", 0), handler_for(brain, TOKEN)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(trust_env=False) as http:
                client = DroidClient(
                    f"http://127.0.0.1:{server.server_port}",
                    TOKEN,
                    http,
                    volume_backend=None if mode == "disabled" else backend,
                )
                reply = client.turn(write_wav(b"\x00\x00" * 1600, 16000))
                assert reply.heard == "set volume to fifty percent"
                if mode == "disabled":
                    assert "disabled" in reply.text
                elif mode == "failure":
                    assert "couldn't check or change" in reply.text
                else:
                    assert reply.text == "Speaker volume is 49 percent."
                assert client.private_mode == (mode == "private")
                read_wav(base64.b64decode(reply.audio))
                if mode == "disabled":
                    backend.execute.assert_not_called()
                else:
                    backend.execute.assert_called_once_with(VolumeCommand(action="set", value=50))
                assert not brain.sessions
                response = http.post(
                    f"{client.url}/v1/volume-result",
                    json={"state": {"levels": [101]}},
                    headers=client.headers,
                )
                assert response.status_code == 400
                response = http.post(f"{client.url}/v1/volume-result", json={"error": "disabled"})
                assert response.status_code == 401
        finally:
            server.shutdown()
            thread.join()


def test_client_never_retries_change_when_acknowledgement_fails():
    requests = []
    backend = Mock(execute=Mock(return_value=VolumeState(levels=[50])))

    def respond(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=TurnReply(
                    heard="volume up",
                    text="",
                    volume=VolumeCommand(action="change", value=10),
                ).model_dump(),
            )
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = DroidClient("http://localhost", TOKEN, http, volume_backend=backend)
        with pytest.raises(httpx.HTTPStatusError):
            client.turn(b"volume up", text_only=True)
    backend.execute.assert_called_once()
    assert requests == ["/v1/text", "/v1/volume-result"]


def test_client_rejects_action_that_does_not_match_the_transcript():
    backend = Mock()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "heard": "tell me a story",
                    "text": "",
                    "volume": {"action": "mute"},
                },
            )
        )
    ) as http:
        client = DroidClient("http://localhost", TOKEN, http, volume_backend=backend)
        with pytest.raises(ValueError, match="Unexpected speaker"):
            client.turn(b"tell me a story", text_only=True)
    backend.execute.assert_not_called()


def test_backend_factory_and_result_validation():
    assert make_volume_backend("none") is None
    assert isinstance(make_volume_backend("alsa", card="1", control="PCM"), AlsaVolume)
    assert execute_volume(None, VolumeCommand(action="get")).error == "disabled"
    with pytest.raises(ValueError):
        make_volume_backend("unknown")
    with pytest.raises(ValidationError):
        VolumeResult(state=VolumeState(levels=[50]), error="disabled")
