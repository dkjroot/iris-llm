"""Regression tests for Phase 1 correctness fixes and Phase 2 latency optimizations."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
from speech_recognition import AudioData

from iris_app.replies import STREAM_PIPELINE, STREAM_REPLY
from iris_app.runtime import Brain
from iris_chat_engines.backends import ChatCompletionsBackend, chat_headers
from iris_chat_engines.budget import estimate, estimate_bytes
from iris_chat_engines.compatible import ChatEngine
from iris_chat_engines.messages import ChatError, Message
from iris_domain.companion import COMPANION_PROMPT
from iris_domain.expression import FACE_PROMPT
from iris_domain.personality import PersonalityContext
from iris_domain.volume import VolumeCommand
from iris_droid.protocol import write_wav
from iris_droid.vad import FRAME_BYTES, RATE, listen_for_speech
from iris_droid.volume import AlsaVolume
from iris_memory import Exchange, SQLiteMemory
from iris_memory.details import Details
from iris_memory.hierarchy import FactChange, Hierarchy, Interpretation, TopicChange
from iris_memory.index import MemoryIndex
from iris_memory.search import SearchArguments, search_memory
from iris_settings import Settings
from iris_tools import ToolRegistry, ToolServices
from iris_tts_engines.text import plain_speech_text, split_speech_sentences
from iris_tts_engines.worker import Response, SpeechWorker


def test_default_8k_budget_leaves_comfortable_headroom_with_all_prompts_and_tools():
    settings = Settings(
        memory_enabled=True,
        weather_enabled=True,
        notifications_enabled=True,
        home_service_url="http://127.0.0.1:8766",
        home_service_token="a" * 32,
    )
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))) as client:
        tools = ToolRegistry(settings, client)
        system = (
            settings.system_prompt
            + "\n"
            + settings.conversation_style_prompt
            + FACE_PROMPT
            + COMPANION_PROMPT
            + tools.instructions()
        )
        tokens = estimate([Message(role="system", content=system)], tools.definitions())
    usable = settings.model_context_tokens - settings.max_tokens - settings.context_margin_tokens
    assert tokens < usable * 0.55
    assert estimate_bytes(100) == 50


def test_chat_history_survives_temporary_fit_truncation():
    settings = Settings(
        model_context_tokens=2048,
        max_tokens=256,
        context_margin_tokens=128,
        tools_enabled=False,
        history_turns=12,
    )
    seen_lengths: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_lengths.append(len(body["messages"]))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        chat = ChatEngine(settings, client)
        chat.get_response("First message " + "a" * 800)
        chat.get_response("Second message " + "b" * 800)
        assert len(chat.history) == 4
        # Temporarily force fit() to drop earlier turns for this oversized prompt.
        chat.get_response("Huge message " + "c" * 2400)
        assert len(chat.history) == 6
        assert chat.history[0].content.startswith("First message")


def test_duplicate_tool_call_ids_rejected_before_before_tools_or_execution():
    duplicate_reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "dup-1",
                            "type": "function",
                            "function": {"name": "get_local_time", "arguments": "{}"},
                        },
                        {
                            "id": "dup-1",
                            "type": "function",
                            "function": {"name": "get_local_time", "arguments": "{}"},
                        },
                    ],
                }
            }
        ]
    }
    before_tools = Mock()
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=duplicate_reply))
    ) as client:
        chat = ChatEngine(Settings(tools_enabled=True), client)
        with (
            patch.object(chat.tools, "execute") as execute,
            pytest.raises(ChatError, match="duplicate tool-call IDs"),
        ):
            chat.generate_reply("What time is it?", before_tools=before_tools)
        before_tools.assert_not_called()
        execute.assert_not_called()


def test_chat_headers_strips_api_key_whitespace():
    settings = Settings(api_key="  secret-key-with-newline\n ")
    assert chat_headers(settings) == {"Authorization": "Bearer secret-key-with-newline"}


def test_hierarchy_skips_unresolved_fact_conflict_without_rolling_back_source_exchange(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conv = archive.start_conversation("alice")
    archive.append("alice", conv, Exchange("e1", "human", "My favourite tea is Assam", "Nice!"))
    hierarchy = Hierarchy(archive, Settings(memory_enabled=True), None)
    # Seed an active fact directly.
    with archive.connect() as db:
        db.execute(
            "INSERT INTO memory_facts(id,owner,fact_key,value,kind,scope,lifetime,importance,"
            "status,source,evidence) "
            "VALUES('f1','alice','tea','Assam','preference','real','stable','normal',"
            "'confirmed','e1','My favourite tea is Assam')"
        )
    # Duplicate case-insensitive topic labels in one interpretation are rejected at validation.
    dup_interp = Interpretation(
        facts=[],
        topics=[
            TopicChange(
                target=None,
                label="Tea tasting",
                scope="real",
                status="open",
                engaged=True,
                evidence="Actually I prefer Darjeeling",
            ),
            TopicChange(
                target=None,
                label="tea tasting",
                scope="real",
                status="open",
                engaged=True,
                evidence="Actually I prefer Darjeeling",
            ),
        ],
    )
    with pytest.raises(ValueError, match="Invalid topic evidence"):
        Hierarchy._validate("Actually I prefer Darjeeling", dup_interp, [], [])

    # Now simulate a prepared interpretation that proposes a conflicting 'tea' fact
    # without supersedes (e.g. outside top-12) alongside a valid topic.
    interp = Interpretation(
        facts=[
            FactChange(
                key="tea",
                value="Darjeeling",
                kind="preference",
                scope="real",
                lifetime="stable",
                importance="normal",
                status="confirmed",
                supersedes=None,
                evidence="Actually I prefer Darjeeling",
            )
        ],
        topics=[
            TopicChange(
                target=None,
                label="Tea tasting",
                scope="real",
                status="open",
                engaged=True,
                evidence="Actually I prefer Darjeeling",
            )
        ],
    )
    _, epoch = archive.policy("alice")
    from iris_memory.hierarchy import Prepared

    prepared = Prepared(
        interpretation=interp,
        revision=1,
        epoch=epoch,
        facts=(),
        topics=(),
    )
    # Committing must NOT roll back the immutable exchange e2.
    exchange = Exchange("e2", "human", "Actually I prefer Darjeeling", "Good choice.")
    archive.append("alice", conv, exchange, hierarchy=prepared)
    saved_ids = [row["id"] for row in archive.export("alice")["exchanges"]]
    assert saved_ids == ["e1", "e2"]
    # Existing fact preserved; valid topic committed once.
    assert [f["value"] for f in hierarchy.facts("alice")] == ["Assam"]
    assert len(hierarchy.topics("alice", "tea")) == 1


def test_delete_previous_does_not_resurrect_forgotten_explicit_detail(tmp_path):
    from datetime import UTC, datetime, timedelta

    from iris_domain.memory import DetailCommand

    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    details = Details(archive)
    conv = archive.start_conversation("alice")
    expires = datetime.now(UTC) + timedelta(minutes=5)
    pending = details.snapshot(
        "alice", DetailCommand(action="remember", label="tea", text="Assam"), expires
    )
    details.apply("alice", pending)
    # Supersede the detail via a fact created by exchange e1.
    archive.append("alice", conv, Exchange("e1", "human", "I switched to Earl Grey", "Noted"))
    with archive.connect() as db:
        db.execute(
            "INSERT INTO detail_supersessions(owner,label,source) VALUES('alice','tea','e1')"
        )
    # Explicitly forget the detail later.
    forget_pending = details.snapshot("alice", DetailCommand(action="forget", label="tea"), expires)
    details.apply("alice", forget_pending)
    # Now delete exchange e1: it must NOT resurrect the forgotten detail.
    _, epoch = archive.policy("alice")
    deleted = archive.delete_previous("alice", expected_epoch=epoch)
    assert deleted == "e1"
    assert details.items("alice") == []


def test_memory_search_rejects_empty_stopword_query_without_time_window(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conv = archive.start_conversation("alice")
    archive.append("alice", conv, Exchange("e1", "human", "Secret garden plan", "Lovely!"))
    index = MemoryIndex(archive)
    _, epoch = archive.policy("alice")
    result = search_memory(
        index, "alice", "Europe/London", epoch, lambda _: None, SearchArguments(query="the and of")
    )
    assert result["sources"] == []


def test_background_memory_worker_keeps_hot_path_free_and_respects_policy_epoch(tmp_path):
    calls: list[str] = []
    release_bg = threading.Event()
    bg_started = threading.Event()

    def complete(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        if system.startswith("Extract memory metadata"):
            calls.append("hierarchy")
            bg_started.set()
            release_bg.wait(timeout=5)
            content = json.dumps(
                {
                    "facts": [
                        {
                            "key": "city",
                            "value": "Bristol",
                            "kind": "identity",
                            "scope": "real",
                            "lifetime": "stable",
                            "importance": "normal",
                            "status": "confirmed",
                            "supersedes": None,
                            "evidence": "I live in Bristol",
                        }
                    ],
                    "topics": [],
                }
            )
        elif system.startswith("Summarise these original"):
            calls.append("summary")
            content = '{"notes": []}'
        else:
            calls.append("chat")
            content = "Nice city!"
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )

    settings = Settings(
        memory_enabled=True,
        memory_database=tmp_path / "memory.sqlite3",
        tools_enabled=False,
    )
    with httpx.Client(transport=httpx.MockTransport(complete)) as client:
        brain = Brain(settings, client, Mock(), Mock())
        try:
            reply = brain.turn("pi", b"I live in Bristol", text_only=True)
            assert reply.text == "Nice city!"
            # Hot path made ONLY the chat call before returning!
            assert calls[0] == "chat"
            # Switch to private mode while the background hierarchy job is in flight.
            assert bg_started.wait(timeout=5)
            brain.turn("pi", b"private mode on", text_only=True)
            release_bg.set()
            assert brain.memory_worker is not None
            brain.memory_worker.flush()
            # Because policy_epoch advanced, the stale hierarchy commit was discarded!
            assert brain.memory_context is not None
            assert brain.memory_context.hierarchy is not None
            assert brain.memory_context.hierarchy.facts("default") == []
        finally:
            release_bg.set()
            brain.lifetime.close()


def test_speech_worker_error_does_not_kill_healthy_worker_and_cancels_dont_exhaust_cap():
    from queue import Queue

    responses: Queue[str | None] = Queue()

    def make_process():
        proc = Mock()
        proc.poll.return_value = None
        proc.kill = Mock()
        proc.wait = Mock()
        return proc

    worker = SpeechWorker(make_process(), responses, "Fake")

    def fake_send(text: str) -> None:
        worker._number += 1
        worker._active = worker._number
        worker._terminal = False
        responses.put(Response(status="error", request_id=worker._active).model_dump_json())

    worker.send = fake_send  # type: ignore[method-assign]
    restarts = 0

    def restart():
        nonlocal restarts
        restarts += 1
        worker.process = make_process()
        worker.failed = False

    worker._restart = restart
    with worker.request("hello"):
        resp = worker.receive(5.0)
        assert resp.status == "error"
    # Terminal status="error" must NOT retire/kill a healthy worker!
    assert worker.failed is False
    assert restarts == 0

    # Three intentional cancellations within 60s must NOT hit the 2/min crash restart cap.
    for _ in range(3):
        worker.mark_cancelled()
        worker.retire()
        with worker.request("retry"):
            resp = worker.receive(5.0)
            assert resp.status == "error"
    assert restarts == 3


def test_speech_text_and_sentence_splitting():
    assert plain_speech_text("...") == ""
    assert plain_speech_text("!? — ...") == ""
    assert split_speech_sentences(
        "Dr. Smith arrived at 3.5 pm. He brought freshly brewed tea! Are you ready to start?"
    ) == [
        "Dr. Smith arrived at 3.5 pm.",
        "He brought freshly brewed tea!",
        "Are you ready to start?",
    ]


def test_kokoro_stream_yields_pcm_per_sentence_and_wraps_invalid_audio():
    from iris_tts_engines.kokoro_offline import TTS

    calls: list[str] = []

    class FakeBackend:
        def create(self, text: str, **kwargs):
            calls.append(text)
            return np.ones(480, dtype=np.float32) * 0.25, 24000

    tts = TTS.__new__(TTS)
    tts.voice = "af_heart"
    tts.speed = 1.0
    tts.language = "en-us"
    tts.sample_rate = 24000
    tts.backend = FakeBackend()

    chunks = list(tts.stream("First sentence. Second sentence!"))
    assert calls == ["First sentence.", "Second sentence!"]
    assert len(chunks) == 2
    assert all(len(c) == 480 * 2 for c in chunks)

    # Non-finite audio raises RuntimeError (503), not ValueError (400).
    class BadBackend:
        def create(self, text: str, **kwargs):
            return np.array([float("nan")], dtype=np.float32), 24000

    tts.backend = BadBackend()
    with pytest.raises(RuntimeError):
        tts.synthesize("hello")


def test_whisper_passes_configured_vad_filter_and_beam_size():
    import sys

    from iris_stt_engines.whisper_offline import STT

    backend = Mock()
    backend.transcribe.return_value = (iter([SimpleNamespace(text="hello")]), object())
    constructor = Mock(return_value=backend)
    settings = Settings(whisper_vad_filter=False, whisper_beam_size=1)
    with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
        stt = STT(settings)
        assert stt.recognize(AudioData(bytes(32000), 16000, 2)) == "hello"
    assert backend.transcribe.call_args.kwargs == {
        "language": "en",
        "vad_filter": False,
        "beam_size": 1,
    }


def test_llm_sse_streaming_pipelines_into_tts_before_llm_finishes():
    first_audio_arrived = threading.Event()
    allow_second_sentence = threading.Event()

    class SlowSSEStream(httpx.SyncByteStream):
        def __iter__(self):
            chunks = [
                "[[expression:amused]]\n",
                "Hello there! ",
            ]
            for piece in chunks:
                yield (
                    "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n"
                ).encode()
            # Block before emitting the second sentence until TTS has ALREADY yielded chunk 1!
            assert allow_second_sentence.wait(timeout=5)
            yield (
                "data: "
                + json.dumps({"choices": [{"delta": {"content": "How are you today?"}}]})
                + "\n\n"
            ).encode()
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=SlowSSEStream(),
        )

    class FakeStreamingTTS:
        sample_rate = 24000

        def synthesize(self, text: str):
            return np.ones(100, dtype=np.float32), 24000

        def stream(self, text: str):
            yield b"\x01\x00" * len(text)

    received_chunks: list[bytes] = []
    received_reply = None

    def sink(reply, chunks, rate):
        nonlocal received_reply
        assert rate == 24000
        it = iter(chunks)
        first = next(it)
        received_chunks.append(first)
        first_audio_arrived.set()
        allow_second_sentence.set()
        for rest in it:
            received_chunks.append(rest)
        received_reply = reply

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        brain = Brain(
            Settings(tools_enabled=False),
            client,
            Mock(recognize=Mock(return_value="Hi Iris")),
            FakeStreamingTTS(),
        )
        try:
            reply_token = STREAM_REPLY.set(sink)
            pipe_token = STREAM_PIPELINE.set(True)
            try:
                reply = brain.turn(
                    "pi", write_wav(b"\x01\x00" * 1600, 16000), text_only=False, face=True
                )
            finally:
                STREAM_PIPELINE.reset(pipe_token)
                STREAM_REPLY.reset(reply_token)
            assert first_audio_arrived.is_set()
            assert len(received_chunks) == 2
            assert reply.text == "Hello there! How are you today?"
            assert reply.expression == "amused"
            assert received_reply is reply
        finally:
            allow_second_sentence.set()
            brain.lifetime.close()


def test_complete_stream_falls_back_to_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Plain JSON reply."}}]},
        )

    seen: list[str] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        backend = ChatCompletionsBackend(Settings(tools_enabled=False), client)
        reply = backend.complete_stream(
            [Message(role="user", content="hi")],
            [],
            on_text=seen.append,
        )
    assert reply.content == "Plain JSON reply."
    assert seen == ["Plain JSON reply."]


def test_parallel_read_only_tool_batch_runs_concurrently():
    started = threading.Barrier(2, timeout=3)
    turn_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal turn_calls
        turn_calls += 1
        if turn_calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "get_local_time", "arguments": "{}"},
                                    },
                                    {
                                        "id": "c2",
                                        "type": "function",
                                        "function": {"name": "get_local_time", "arguments": "{}"},
                                    },
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "Done."}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        chat = ChatEngine(Settings(tools_enabled=True), client)
        original = chat.tools.local_time

        def synchronized_time():
            started.wait()
            return original()

        chat.tools.local_time = synchronized_time  # type: ignore[method-assign]
        t0 = time.monotonic()
        assert chat.get_response("check twice") == "Done."
        assert time.monotonic() - t0 < 2.5


def test_personality_curly_apostrophe_matches_ascii_voice_input():
    with pytest.raises(ValueError, match="unique"):
        PersonalityContext(
            names=["Dave’s Coach", "Dave's Coach"],
            active="Dave’s Coach",
            prompt="Be encouraging.",
        )
    ctx = PersonalityContext(
        names=["Assistant", "Dave’s Coach"],
        active="Assistant",
        prompt="Be helpful.",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200))) as client:
        brain = Brain(Settings(tools_enabled=False), client, Mock(), Mock())
        try:
            reply = brain.turn(
                "pi", b"switch to personality Dave's Coach", text_only=True, personality=ctx
            )
            assert reply.selected_personality == "Dave’s Coach"
        finally:
            brain.lifetime.close()


def test_tool_services_shares_weather_geocode_cache_across_sessions():
    geocode_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal geocode_calls
        if "geocoding-api.open-meteo.com" in str(request.url):
            geocode_calls += 1
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Stroud",
                            "latitude": 51.74,
                            "longitude": -2.21,
                            "country_code": "GB",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-11-20T12:00",
                    "temperature_2m": 12.0,
                    "apparent_temperature": 11.0,
                    "weather_code": 1,
                    "wind_speed_10m": 5.0,
                },
                "daily": {
                    "time": ["2026-11-20"],
                    "weather_code": [1],
                    "temperature_2m_max": [14.0],
                    "temperature_2m_min": [6.0],
                    "precipitation_probability_max": [10.0],
                    "precipitation_sum": [0.0],
                    "wind_speed_10m_max": [8.0],
                },
            },
        )

    settings = Settings(tools_enabled=True, weather_enabled=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        services = ToolServices(settings, client)
        r1 = ToolRegistry(settings, client, services=services)
        r2 = ToolRegistry(settings, client, services=services)
        assert "current" in r1.local_weather(1)
        assert "current" in r2.local_weather(1)
    assert geocode_calls == 1


def test_vad_trims_trailing_silence_when_opted_in():
    probs = [0.9] * 5 + [0.1] * 20
    idx = 0

    class FakeDetector:
        def reset(self) -> None:
            pass

        def probability(self, pcm: bytes) -> float:
            nonlocal idx
            val = probs[min(idx, len(probs) - 1)]
            idx += 1
            return val

    frames = [bytes([i % 256]) * FRAME_BYTES for i in range(len(probs))]
    pos = 0

    class FakeStream:
        def read(self, n: int) -> bytes:
            nonlocal pos
            out = frames[pos]
            pos += 1
            return out

    source = SimpleNamespace(SAMPLE_RATE=RATE, CHUNK=512, stream=FakeStream())
    pcm = listen_for_speech(
        source,
        FakeDetector(),
        timeout=5,
        limit=30,
        pause=0.64,
        trim_silence=True,
    )
    # 5 speech frames + 6 retained trailing silence frames = 11 frames (instead of 25).
    assert len(pcm) == 11 * FRAME_BYTES


def test_alsa_volume_parses_sset_output_without_extra_sget():
    mixer = (
        "Simple mixer control 'Master',0\n"
        "  Front Left: Playback 45 [45%] [on]\n"
        "  Front Right: Playback 45 [45%] [on]\n"
    )
    with patch(
        "iris_droid.volume.subprocess.run",
        side_effect=[Mock(stdout=mixer), Mock(stdout=mixer.replace("45", "60"))],
    ) as run:
        state = AlsaVolume().execute(VolumeCommand(action="set", value=60))
    assert state.levels == [60, 60]
    assert run.call_count == 2  # Preflight sget + sset only; no redundant trailing sget.
