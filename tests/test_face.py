import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import HTTPServer
from threading import Thread
from unittest.mock import Mock

import httpx
import numpy as np
import pytest

from iris_droid import client
from iris_droid.expression import FACE_HEADER, ModelReply, read_model_reply
from iris_droid.face_console import read_state, request_personality
from iris_droid.face_display import ASSETS, FaceDisplay
from iris_droid.personalities import WEB_PERSONALITY_HEADER, load_personalities
from iris_droid.protocol import write_wav
from iris_droid.server import Brain, handler_for
from iris_settings import Settings

TOKEN = "test-only-droid-key-for-the-face"
PCM = b"\x02\x00" * 1600
RECORDING = write_wav(PCM, 16000)
REPLY = "I thought you might say that."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[[expression:Amused]]\nHello.", ModelReply("Hello.", "amused")),
        (" [[expression:angry]] Hello.", ModelReply("Hello.", "neutral")),
        ("[[expression:curious\nHello.", ModelReply("Hello.", "neutral")),
        ("Hello.", ModelReply("Hello.", "neutral")),
        ("What is [[expression:amused]]?", ModelReply("What is [[expression:amused]]?", "neutral")),
    ],
)
def test_read_expression_only_from_the_reply_prefix(text, expected):
    assert read_model_reply(text, face=True) == expected
    assert read_model_reply(text, face=False) == ModelReply(text)


@contextmanager
def running_brain(model, tts, *, stream=False, **web) -> Iterator[tuple[Brain, str]]:
    with httpx.Client(transport=httpx.MockTransport(model)) as transport:
        brain = Brain(
            Settings(tts_engine="pocket" if stream else "kokoro"),
            transport,
            Mock(recognize=Mock(return_value="Tell me something.")),
            tts,
        )
        with HTTPServer(("127.0.0.1", 0), handler_for(brain, TOKEN, **web)) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                yield brain, f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                worker.join()


def model_response(text):
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": text}}]}
    )


def test_face_is_negotiated_per_turn_and_cue_is_removed_before_history():
    prompts = []

    def model(request):
        system = json.loads(request.content)["messages"][0]["content"]
        prompts.append(system)
        return model_response(
            f"[[expression:amused]]\n{REPLY}" if "[[expression:" in system else REPLY
        )

    with (
        running_brain(model, Mock()) as (brain, url),
        httpx.Client(trust_env=False, timeout=5) as http,
    ):
        droid = client.DroidClient(url, TOKEN, http, face=True)
        reply = droid.turn(b"Hello", text_only=True)
        assert (reply.text, reply.expression) == (REPLY, "amused")
        legacy = http.post(
            f"{url}/v1/text",
            headers={
                **{name: value for name, value in droid.headers.items() if name != FACE_HEADER},
                "Content-Type": "text/plain",
            },
            content=b"Hello again",
        )
        assert legacy.status_code == 200 and "expression" not in legacy.json()
        assert "[[expression:" in prompts[0] and "[[expression:" not in prompts[1]
        assert [
            m.content for m in next(iter(brain.sessions.values())).history if m.role == "assistant"
        ] == [REPLY, REPLY]
        assert droid.turn(b"companion mode on", text_only=True).companion_mode
        opener = droid.turn(b"idle", text_only=True, companion_idle=True)
        assert (opener.text, opener.expression) == (REPLY, "amused")
        assert "[[expression:" in prompts[2]


@pytest.mark.parametrize("stream", [False, True])
def test_voice_client_displays_the_pc_expression_during_audio(monkeypatch, tmp_path, stream):
    display_url = ""
    serve = FaceDisplay.serve
    tts = Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32), 24000)))

    def read_state():
        return httpx.get(f"{display_url}/state", trust_env=False, timeout=5).json()

    @contextmanager
    def on_test_port(display, _port):
        nonlocal display_url
        with serve(display, 0) as port:
            display_url = f"http://127.0.0.1:{port}"
            yield port

    def model(request):
        assert "[[expression:" in json.loads(request.content)["messages"][0]["content"]
        assert read_state()["activity"] == "thinking"
        return model_response(f"[[expression:amused]]\n{REPLY}")

    def generate_audio(text):
        assert text == REPLY
        yield PCM

    def play_audio(*args, **kwargs):
        assert read_state() == {
            "head": "signal",
            "screensaver": "black",
            "activity": "speaking",
            "expression": "amused",
        }
        if stream:
            assert list(args[0]) == [PCM]

    captures = 0

    def record(*args, **kwargs):
        nonlocal captures
        assert read_state()["activity"] == "idle"
        captures += 1
        if captures > 1:
            raise KeyboardInterrupt
        kwargs["on_listening"]()
        assert read_state()["activity"] == "listening"
        return RECORDING

    tts.stream = generate_audio
    tts.sample_rate = 24000
    monkeypatch.setenv("IRIS_DROID_TOKEN", TOKEN)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(FaceDisplay, "serve", on_test_port)
    monkeypatch.setattr(client, "record_turn", record)
    player = Mock(side_effect=play_audio)
    monkeypatch.setattr(client, "play_stream" if stream else "play_wav", player)
    with running_brain(model, tts, stream=stream) as (_, url):
        assert (
            client.main(
                [
                    "--server",
                    url,
                    "--activation",
                    "continuous",
                    "--no-sounds",
                    "--face",
                    "signal",
                    "--face-idle",
                    "black",
                ]
            )
            == 0
        )
    player.assert_called_once()
    assert captures == 2
    if not stream:
        tts.synthesize.assert_called_once_with(REPLY)


def test_face_display_assets_and_idle_state():
    display = FaceDisplay("humanoid-android")
    with display.serve(0) as port, httpx.Client(trust_env=False, timeout=5) as http:
        url = f"http://127.0.0.1:{port}"
        for path in ASSETS:
            response = http.get(url + path)
            assert response.status_code == 200
            assert "'self'" in response.headers["content-security-policy"]
            assert response.content
        assert http.get(f"{url}/.env").status_code == 404
        assert http.get(f"{url}/state", headers={"Host": f"example.org:{port}"}).status_code == 400
        assert http.get(f"{url}/state").json() == {
            "head": "humanoid-android",
            "screensaver": "snow",
            "activity": "idle",
            "expression": "neutral",
        }
        display.show("listening", "amused", seconds=0)
        assert http.get(f"{url}/state").json()["activity"] == "idle"


def test_display_selection_updates_the_live_droid_and_tracks_voice_switches(tmp_path):
    catalog = tmp_path / "personalities.json"
    catalog.write_text(json.dumps({"Regular": "Always be polite.", "Café": "Talk about coffee."}))

    def model(request):
        assert "Talk about coffee." in json.loads(request.content)["messages"][0]["content"]
        return model_response(REPLY)

    with (
        running_brain(model, Mock()) as (_, url),
        httpx.Client(trust_env=False, timeout=5) as http,
    ):
        droid = client.DroidClient(url, TOKEN, http, personalities_file=catalog)
        display = FaceDisplay("signal")
        display.personalities = tuple(droid.personality_headers)
        display.active_personality = lambda: droid.personality
        display.select_personality = droid.select_personality
        with display.serve(0) as port:
            assert port is not None
            assert read_state(port).personalities == ("Regular", "Café")
            response = http.post(
                f"http://127.0.0.1:{port}/personality",
                headers={"Origin": "https://another.example"},
                json="Café",
            )
            assert response.status_code == 403
            assert not request_personality(port, "Unknown")
            assert droid.personality == "Regular"
            assert request_personality(port, "Café")
            assert read_state(port).personality == "Café"
            assert droid.turn(b"Hello", text_only=True).text == REPLY
            assert droid.turn(b"switch to personality Regular", text_only=True).selected_personality
            assert read_state(port).personality == "Regular"


def test_web_personality_catalog_exposes_names_and_applies_the_selected_prompt(tmp_path):
    catalog = tmp_path / "personalities.json"
    catalog.write_text(json.dumps({"Regular": "Be polite.", "Café": "Talk about coffee."}))

    def model(request):
        assert "Talk about coffee." in json.loads(request.content)["messages"][0]["content"]
        return model_response(f"[[expression:amused]]\n{REPLY}")

    with (
        running_brain(
            model, Mock(), web_chat=True, web_personalities=load_personalities(catalog)
        ) as (_, url),
        httpx.Client(trust_env=False, timeout=5) as http,
    ):
        assert http.get(f"{url}/v1/web-personalities").status_code == 401
        droid = client.DroidClient(url, TOKEN, http, face=True)
        response = http.get(f"{url}/v1/web-personalities", headers=droid.headers)
        assert response.json() == ["Regular", "Café"]
        droid.headers[WEB_PERSONALITY_HEADER] = "Caf%C3%A9"
        reply = droid.turn(b"Hello", text_only=True)
        assert (reply.text, reply.expression) == (REPLY, "amused")
        assert droid.turn(b"list personalities", text_only=True).text.endswith("I'm using Café.")


def test_web_chat_starts_with_assistant_and_lists_default_personalities():
    def model(request):
        system = json.loads(request.content)["messages"][0]["content"]
        assert "Be a helpful assistant." in system
        return model_response(REPLY)

    with (
        running_brain(model, Mock(), web_chat=True) as (_, url),
        httpx.Client(trust_env=False, timeout=5) as http,
    ):
        assert http.get(f"{url}/v1/web-personalities").json() == [
            "Assistant",
            "Friend",
            "Comedian",
        ]
        droid = client.DroidClient(url, TOKEN, http)
        droid.headers[WEB_PERSONALITY_HEADER] = ""
        assert droid.turn(b"Hello", text_only=True).text == REPLY
        reply = droid.turn(b"list personalities", text_only=True)
        assert reply.text.endswith("I'm using Assistant.")
