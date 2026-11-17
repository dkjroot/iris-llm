"""Failure boundaries for admission, deadlines and final stream outcomes."""

import base64
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event, Thread, current_thread
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from iris_chat_engines.messages import FunctionCall, Message, ToolCall
from iris_droid.admission import BoundedHTTPServer, TurnQueue
from iris_droid.client import DroidClient
from iris_droid.protocol import COMPLETION_HEADER, VOICE_STREAM_TYPE, write_wav
from iris_droid.server import handler_for
from iris_frames import ndjson_lines
from iris_tools import NoArguments, Skill
from iris_turn import TurnExpired, check_deadline, remaining_timeout, turn_budget

TOKEN = "phase-one-synthetic-token-long-enough"
WAV = write_wav(b"\1\0" * 1600, 16000)


@pytest.fixture
def running_server(brain_factory):
    with ExitStack() as resources:

        def start(**options):
            harness = brain_factory(**options)
            server = BoundedHTTPServer(("127.0.0.1", 0), handler_for(harness.brain, TOKEN))
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            worker.start()

            def close():
                server.shutdown()
                worker.join(timeout=5)
                server.server_close()
                assert not worker.is_alive()

            resources.callback(close)
            return harness, server, f"http://127.0.0.1:{server.server_port}"

        yield start


def headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Iris-Session": str(uuid4()),
        "Content-Type": "text/plain",
    }


def test_slow_upload_does_not_occupy_brain_and_has_total_deadline(running_server):
    harness, server, url = running_server(upload_timeout=0.3)
    with socket.create_connection(server.server_address, timeout=3) as slow:
        fields = headers()
        request = "POST /v1/text HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items())
        slow.sendall((request + "Content-Length: 5\r\n\r\nx").encode())
        with httpx.Client(trust_env=False, timeout=3) as client:
            reply = client.post(url + "/v1/text", headers=headers(), content=b"Hello")
        assert reply.status_code == 200
        assert b"408" in slow.recv(4096).split(b"\r\n", 1)[0]
    assert len(harness.model.calls) == 1


def test_admission_rejects_overload_but_decision_route_remains_available(running_server):
    harness, _, url = running_server(max_pending_turns=1)
    started, release = Event(), Event()
    original = harness.model.complete

    def blocked(messages, tools):
        started.set()
        assert release.wait(5)
        return original(messages, tools)

    harness.model.complete = blocked
    with httpx.Client(trust_env=False, timeout=5) as client, ThreadPoolExecutor() as pool:
        active = pool.submit(client.post, url + "/v1/text", headers=headers(), content=b"Hello")
        try:
            assert started.wait(3)
            busy = client.post(url + "/v1/text", headers=headers(), content=b"Later")
            assert busy.status_code == 503
            decision = client.post(
                url + "/v1/voice/decision",
                headers={**headers(), "X-Iris-Turn": str(uuid4())},
                content=b"cancel",
            )
            assert decision.status_code == 409  # No such turn, but handled immediately.
        finally:
            release.set()
        assert active.result().status_code == 200


def test_queue_timeout_does_not_run_later_or_block_next_turn(running_server):
    harness, _, url = running_server(
        queue_timeout=0.05,
        replies=[
            Message(role="assistant", content="First"),
            Message(role="assistant", content="Next"),
        ],
    )
    started, release = Event(), Event()
    original = harness.model.complete

    def blocked(messages, tools):
        started.set()
        assert release.wait(5)
        return original(messages, tools)

    harness.model.complete = blocked
    with httpx.Client(trust_env=False, timeout=5) as client, ThreadPoolExecutor() as pool:
        active = pool.submit(client.post, url + "/v1/text", headers=headers(), content=b"First")
        try:
            assert started.wait(3)
            assert (
                client.post(url + "/v1/text", headers=headers(), content=b"Expired").status_code
                == 503
            )
        finally:
            release.set()
        assert active.result().status_code == 200
        assert client.post(url + "/v1/text", headers=headers(), content=b"Next").status_code == 200
    assert len(harness.model.calls) == 2


def test_queue_runs_complete_requests_in_fifo_order_without_overlapping():
    queue = TurnQueue()
    waiting = {name: Event() for name in ("first", "second")}
    order = []
    original_wait = queue.condition.wait

    def wait(timeout=None):
        waiting[current_thread().name].set()
        return original_wait(timeout)

    def run():
        with queue.enter(5):
            order.append(current_thread().name)

    workers = [Thread(target=run, name=name) for name in waiting]
    with patch.object(queue.condition, "wait", wait):
        with queue.enter(5):
            for worker in workers:
                worker.start()
                assert waiting[worker.name].wait(3)
            assert order == []
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive()
    assert order == ["first", "second"]


def test_connection_thread_cap_rejects_before_starting_another_handler(running_server):
    _, server, _ = running_server()
    for _ in range(64):
        assert server._connections.acquire(blocking=False)
    try:
        with socket.create_connection(server.server_address, timeout=3) as connection:
            assert b"503" in connection.recv(4096).split(b"\r\n", 1)[0]
    finally:
        for _ in range(64):
            server._connections.release()


@pytest.mark.parametrize(
    "mode", ["text", "buffered", "streamed", "cancelled_buffered", "cancelled_streamed"]
)
def test_final_metadata_identifies_committed_and_deleted_exchange(running_server, mode):
    harness, _, url = running_server(memory_enabled=True, tts_engine="pocket")

    def accept(stop):
        assert stop.wait(3)
        return False

    with httpx.Client(trust_env=False, timeout=5) as http:
        client = DroidClient(
            url,
            TOKEN,
            http,
            stream_player=(lambda start, chunks: list(chunks) and None)
            if "streamed" in mode
            else None,
            cancel_listener=accept if mode.startswith("cancelled") else None,
        )
        reply = client.turn(b"Hello" if mode == "text" else WAV, text_only=mode == "text")
        saved = harness.brain.memory.export("default")["exchanges"]
        assert len(saved) == 1 and saved[0]["id"] == reply._exchange_id
        assert reply._completion is not None and not reply._completion.private
        harness.stt.text = "delete previous"
        deleted = client.turn(
            b"delete previous" if mode == "text" else WAV, text_only=mode == "text"
        )
        assert deleted._deleted_exchange_id == reply._exchange_id
        assert not harness.brain.memory.export("default")["exchanges"]


def test_private_control_prepares_audio_then_commits_and_returns_final_policy(running_server):
    harness, _, url = running_server(memory_enabled=True, tts_engine="pocket", replies=[])
    harness.stt.text = "private mode on"
    with httpx.Client(trust_env=False, timeout=5) as http:
        client = DroidClient(
            url, TOKEN, http, stream_player=lambda *_: pytest.fail("Short control streamed")
        )
        result = client.turn(WAV)
        assert result.audio and client.private_mode and harness.brain.private_mode
        harness.stt.text = "private mode off"
        client.turn(WAV)
        assert not client.private_mode and not harness.brain.private_mode


def test_streamed_archive_failure_cannot_be_reported_as_completed(running_server):
    harness, _, url = running_server(memory_enabled=True, tts_engine="pocket")
    played = []
    with httpx.Client(trust_env=False, timeout=5) as http:
        client = DroidClient(
            url, TOKEN, http, stream_player=lambda start, chunks: played.extend(chunks)
        )
        with patch.object(harness.brain.memory, "append", side_effect=RuntimeError("injected")):
            with pytest.raises(httpx.RemoteProtocolError):
                client.turn(WAV)
    assert played  # Delivered audio cannot be undone; no committed completion follows it.
    assert not harness.brain.memory.export("default")["exchanges"]


def test_nested_stage_budget_does_not_reset_deadline(fake_clock):
    with patch("iris_turn.monotonic", fake_clock.monotonic), turn_budget(10):
        fake_clock.advance(8)
        with turn_budget(100):
            assert remaining_timeout(20) == 2
            fake_clock.advance(2)
            with pytest.raises(TurnExpired):
                check_deadline()
    check_deadline()  # No leaked context after exit.


@pytest.mark.parametrize("stage", ["recognition", "model", "synthesis"])
def test_expired_stage_never_archives_or_dispatches_a_later_effect(
    brain_factory, fake_clock, stage
):
    harness = brain_factory(memory_enabled=True, turn_timeout=1)
    target, operation = {
        "recognition": (harness.stt, "recognize"),
        "model": (harness.model, "complete"),
        "synthesis": (harness.tts, "synthesize"),
    }[stage]
    original = getattr(target, operation)

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        fake_clock.advance(2)
        return result

    with patch("iris_turn.monotonic", fake_clock.monotonic), patch.object(target, operation, late):
        with pytest.raises(TurnExpired):
            harness.brain.turn("session", WAV)
    assert not harness.brain.memory.export("default")["exchanges"]
    if stage != "synthesis":
        assert not harness.tts.calls


def test_deadline_between_tools_prevents_the_second_effect(brain_factory, fake_clock):
    calls = [
        ToolCall(id=str(i), type="function", function=FunctionCall(name="switch", arguments="{}"))
        for i in range(2)
    ]
    harness = brain_factory(replies=[Message(role="assistant", tool_calls=calls)], turn_timeout=1)
    effects = []

    def switch(_):
        effects.append("on")
        fake_clock.advance(2)
        return {"accepted": True}

    harness.brain._session("session").tools.skills["switch"] = Skill(
        "switch", "Synthetic switch", NoArguments, switch
    )
    with patch("iris_turn.monotonic", fake_clock.monotonic), pytest.raises(TurnExpired):
        harness.brain.turn("session", b"Turn it on", text_only=True)
    assert effects == ["on"]  # An earlier effect is not rolled back or replayed.


@pytest.mark.parametrize("hierarchy", [False, True])
def test_expired_memory_inference_does_not_commit_derived_or_original_data(
    brain_factory, fake_clock, hierarchy
):
    from iris_memory import Exchange

    harness = brain_factory(
        memory_enabled=True,
        memory_hierarchy_enabled=hierarchy,
        memory_summary_interval=1,
        turn_timeout=1,
        replies=[
            Message(
                role="assistant",
                content='{"facts":[],"topics":[]}' if hierarchy else '{"notes":[]}',
            )
        ],
    )
    archive = harness.brain.memory
    conversation = archive.active_conversation("default")
    archive.append("default", conversation, Exchange("seed", "human", "Original source", "Noted"))
    original = harness.model.complete

    def late(*args):
        result = original(*args)
        fake_clock.advance(2)
        return result

    with (
        patch("iris_turn.monotonic", fake_clock.monotonic),
        patch.object(harness.model, "complete", late),
    ):
        with pytest.raises(TurnExpired):
            harness.brain.turn("session", b"Hello", text_only=True)
    exported = archive.export("default")
    assert len(exported["exchanges"]) == 1
    assert not exported["summaries"] and not exported["memory_facts"]


def stream_frames():
    timing = {
        "engine": "pocket",
        "words": 1,
        "started_ns": 1,
        "synthesis_ns": 1,
        "audio_seconds": 0.1,
    }
    return [
        {
            "event": "start",
            "reply": {"heard": "Hello", "text": "Hello"},
            "rate": 24000,
            "started_ns": 1,
            "first_audio_ns": 1,
        },
        {"event": "audio", "pcm": base64.b64encode(b"\1\0" * 8).decode()},
        {
            "event": "done",
            "timing": timing,
            "completion": {"outcome": "committed", "private": True, "exchange_id": "saved"},
        },
    ]


@pytest.mark.parametrize(
    "broken",
    [
        "missing_done",
        "duplicate_start",
        "duplicate_done",
        "after_done",
        "invalid_base64",
        "odd_pcm",
        "missing_completion",
        "missing_outcome",
        "wrong_outcome",
    ],
)
def test_invalid_or_incomplete_stream_never_returns_success(broken):
    frames = stream_frames()
    if broken == "missing_done":
        frames.pop()
    elif broken == "duplicate_start":
        frames.insert(1, frames[0])
    elif broken == "duplicate_done":
        frames.append(frames[-1])
    elif broken == "after_done":
        frames.append(frames[1])
    elif broken == "invalid_base64":
        frames[1]["pcm"] = "!invalid"
    elif broken == "odd_pcm":
        frames[1]["pcm"] = "AQ=="
    elif broken == "missing_outcome":
        del frames[-1]["completion"]["outcome"]
    elif broken == "wrong_outcome":
        frames[-1]["completion"]["outcome"] = "failed"
    else:
        del frames[-1]["completion"]
    body = "".join(json.dumps(frame) + "\n" for frame in frames).encode()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body,
                headers={"Content-Type": VOICE_STREAM_TYPE, COMPLETION_HEADER: "1"},
            )
        )
    ) as http:
        client = DroidClient(
            "http://iris.invalid", TOKEN, http, stream_player=lambda start, chunks: list(chunks)
        )
        with pytest.raises(httpx.RemoteProtocolError):
            client.turn(WAV)


@pytest.mark.parametrize("negotiated", [False, True])
def test_completion_negotiation_keeps_old_servers_usable(negotiated):
    frames = stream_frames()
    if not negotiated:
        del frames[-1]["completion"]
    response_headers = {"Content-Type": VOICE_STREAM_TYPE}
    if negotiated:
        response_headers[COMPLETION_HEADER] = "1"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content="".join(json.dumps(frame) + "\n" for frame in frames),
                headers=response_headers,
            )
        )
    ) as http:
        client = DroidClient(
            "http://iris.invalid", TOKEN, http, stream_player=lambda start, chunks: list(chunks)
        )
        reply = client.turn(WAV)
        assert reply._streamed
        assert client.private_mode is negotiated
        assert reply._exchange_id == ("saved" if negotiated else None)


def test_incremental_frames_preserve_utf8_across_every_byte_boundary():
    body = '{"text":"café 🤖"}\r\n'.encode()
    assert list(ndjson_lines((bytes([b]) for b in body), max_frame=100)) == ['{"text":"café 🤖"}']


@pytest.mark.parametrize(
    "chunks,options",
    [
        ([b"1234", b"5"], {"max_frame": 4}),
        ([b"12345\n"], {"max_frame": 4}),
        ([b"abc"], {"max_frame": 4}),
        ([b"a\na\n"], {"max_frame": 4, "max_total": 3}),
        ([b"a\na\n"], {"max_frame": 4, "max_frames": 1}),
        ([b"\xff\n"], {"max_frame": 4}),
    ],
)
def test_bad_frame_is_rejected_before_json_decoding(chunks, options):
    with pytest.raises(ValueError):
        list(ndjson_lines(chunks, **options))
