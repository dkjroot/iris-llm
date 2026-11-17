"""Application boundaries exercised without HTTP handlers or real hardware."""

import ast
import json
from dataclasses import fields
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch

import httpx
import pytest

from iris_app.commands import parse_commands
from iris_app.decisions import VoiceDecision
from iris_app.lifetime import ApplicationLifetime
from iris_app.turn import TurnEffects, TurnInput
from iris_audio import write_wav
from iris_domain.messages import FunctionCall, Message, ToolCall
from iris_domain.replies import TurnCancelled, TurnReply
from iris_domain.session import SessionState
from iris_domain.turn import effect_scope
from iris_droid.client_settings import ClientOptions, parse_client_args
from iris_tools import NoArguments, Skill
from iris_turn import TurnExpired, check_deadline


def test_checkpoint_copies_every_state_field_without_copying_services(brain_factory):
    chat = brain_factory().brain._session("one")
    chat.history = [Message(role="user", content="original")]
    chat.memory_sources = {"source"}
    saved = chat.checkpoint()
    backend, tools = chat.backend, chat.tools
    chat.history[0].content = "changed"
    chat.memory_sources.add("later")
    chat.last_tool_names = ("external_action",)
    chat.companion_mode = True
    chat.restore(saved)
    assert all(getattr(chat, f.name) == getattr(saved, f.name) for f in fields(SessionState))
    assert chat.backend is backend and chat.tools is tools
    chat.history[0].content = "changed again"
    assert saved.history[0].content == "original"
    assert saved.memory_sources == {"source"}


def test_provisional_application_turn_restores_evicted_session_without_http(brain_factory):
    app = brain_factory().brain
    for i in range(16):
        app._session(str(i))
    before = list(app.sessions.items())
    decision = VoiceDecision(lambda event: None)

    def cancel():
        decision.decide(False)
        decision.check()

    decision.require_acceptance = cancel
    with pytest.raises(TurnCancelled):
        app.coordinator.execute(TurnInput("new", b"hello", text_only=True), decision=decision)
    assert list(app.sessions.items()) == before


def test_exact_command_extension_has_no_model_or_memory_work(brain_factory):
    harness = brain_factory(replies=[])
    harness.brain.commands.register(
        "test status", lambda command: TurnReply(heard=command.heard, text="Ready.")
    )
    result = harness.brain.coordinator.execute(TurnInput("a", b"TEST STATUS!", text_only=True))
    assert result.text == "Ready."
    assert not harness.model.calls and not harness.brain.sessions
    with pytest.raises(ValueError):
        harness.brain.commands.register("clear context", lambda command: result)


def test_effect_receipt_survives_reply_failure_and_session_rollback(brain_factory):
    call = Message(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="call", type="function", function=FunctionCall(name="switch", arguments="{}")
            )
        ],
    )
    harness = brain_factory(replies=[call, Message(role="assistant", content="Done.")])
    chat = harness.brain._session("a")
    attempts = []
    chat.tools.skills["switch"] = Skill(
        "switch",
        "Test effect",
        NoArguments,
        lambda args: attempts.append(1) or {"ok": True},
        effect=True,
    )
    harness.tts.failure = RuntimeError("speaker unavailable")
    effects = TurnEffects()
    with pytest.raises(RuntimeError, match="speaker unavailable"):
        harness.brain.coordinator.execute(
            TurnInput("a", write_wav(b"\1\0" * 1600, 16000)), effects=effects
        )
    assert attempts == [1]
    assert not chat.history
    assert [(r.tool, r.outcome) for r in effects.receipts] == [("switch", "returned")]
    assert not effects.attempted


def test_uncertain_effect_is_not_repeated_with_a_new_tool_call_id(brain_factory):
    tools = brain_factory().brain._session("a").tools
    attempts = []
    tools.skills["switch"] = Skill(
        "switch",
        "Test effect",
        NoArguments,
        lambda args: attempts.append(1) or {"error": "Acknowledgement lost"},
        effect=True,
    )
    with effect_scope() as effects:
        first = tools.execute("switch", "{}")
        second = tools.execute("switch", " { } ")
        assert "Acknowledgement lost" in first and "already attempted" in second
        assert effects.receipts[0].outcome == "uncertain"
    assert attempts == [1]
    assert not effects.attempted
    with effect_scope():
        tools.execute("switch", "{}")
    assert attempts == [1, 1]


def test_notification_cooldown_is_shared_across_reset_sessions(brain_factory):
    app = brain_factory(
        notifications_enabled=True, pushover_api_token="secret", pushover_user_key="owner"
    ).brain
    sender = app._session("a").tools.notifications
    transport = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": 1}))
    )
    sender.client = transport
    try:
        first = app._session("a").tools.execute("send_notification", '{"message":"hello"}')
        app._reset_sessions()
        second = app._session("b").tools.execute("send_notification", '{"message":"again"}')
        assert json.loads(first)["status"] == "accepted"
        assert "cooldown" in json.loads(second)["error"]
        assert app._session("b").tools.notifications is sender
    finally:
        transport.close()


def test_shutdown_defers_cleanup_and_stops_work_after_native_returns():
    lifetime = ApplicationLifetime()
    entered, release, expired = Event(), Event(), Event()
    cleanup = Mock()
    lifetime.own(cleanup)

    def native():
        with lifetime.lease():
            entered.set()
            assert release.wait(2)
            try:
                check_deadline()
            except TurnExpired:
                expired.set()

    worker = Thread(target=native)
    worker.start()
    try:
        assert entered.wait(1)
        assert not lifetime.close(timeout=0.01)
        cleanup.assert_not_called()
        with pytest.raises(TurnExpired), lifetime.lease():
            pytest.fail("Shutdown admitted another turn")
    finally:
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive() and expired.is_set()
    assert lifetime.close(timeout=0)
    cleanup.assert_called_once()


def test_default_server_startup_without_config_path(monkeypatch):
    from iris_droid import server
    from iris_settings import Settings

    monkeypatch.setenv("IRIS_DROID_TOKEN", "long-test-connection-key")
    with (
        patch.object(server, "load_settings", return_value=Settings()),
        patch.object(server, "open_tts", side_effect=RuntimeError("reached synthesis setup")),
    ):
        with pytest.raises(RuntimeError, match="reached synthesis setup"):
            server.main([])


def test_client_options_preserve_parser_defaults():
    namespace = parse_client_args([])
    options = ClientOptions.from_namespace(namespace)
    assert vars(options) == vars(namespace)


def test_domain_and_model_do_not_import_transport_or_repository_adapters():
    for directory in ("iris_domain", "iris_chat_engines"):
        for path in Path(directory).glob("*.py"):
            tree = ast.parse(path.read_text())
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            assert not any(
                name and name.startswith(("iris_droid", "iris_memory")) for name in imports
            ), path


def test_router_uses_the_original_grammar_corpus():
    cases = json.loads(Path("tests/fixtures/command_contract.json").read_text())
    for case in cases:
        parsed = parse_commands(case["phrase"], companion=True)
        expected = case["result"]
        kind = expected.split(":")[0] if expected else None
        assert parsed.first == {"delete_previous": "delete"}.get(kind, kind), case


def test_application_accepts_a_backend_without_any_model_http(brain_factory):
    from iris_app.runtime import Brain
    from tests.support import ScriptedBackend

    harness = brain_factory()
    model = ScriptedBackend([Message(role="assistant", content="Injected reply.")])
    app = Brain(
        harness.brain.settings,
        harness.transport,
        harness.stt,
        harness.tts,
        backend_factory=lambda settings, client: model,
    )
    try:
        reply = app.turn("a", b"hello", text_only=True)
        assert reply.text == "Injected reply."
        assert len(model.calls) == 1 and not harness.model.calls
    finally:
        app.lifetime.close()


def test_backend_can_declare_no_tool_support_and_retains_finish_metadata():
    from iris_chat_engines.backends import BackendCapabilities, ChatCompletionsBackend
    from iris_chat_engines.compatible import ChatEngine
    from iris_settings import Settings
    from tests.support import ScriptedBackend

    model = ScriptedBackend([Message(role="assistant", content="Plain reply.")])
    model.capabilities = BackendCapabilities(tools=False)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected HTTP"))
    ) as client:
        chat = ChatEngine(Settings(), client, backend=model)
        assert chat.get_response("hello") == "Plain reply."
        assert model.calls[0][1] == []
        assert "get_local_time" not in model.calls[0][0][0].content

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Partial reply"},
                            "finish_reason": "length",
                        }
                    ]
                },
            )
        )
    ) as client:
        reply = ChatCompletionsBackend(Settings(), client).complete([], [])
        assert reply._finish_reason == "length"
        assert "finish_reason" not in reply.model_dump()


def test_failed_notification_cannot_retry_with_edited_arguments(brain_factory):
    tools = brain_factory().brain._session("a").tools
    calls = []
    from iris_tools.notifications import NotificationArguments

    tools.skills["notify"] = Skill(
        "notify",
        "Test notification",
        NotificationArguments,
        lambda args: calls.append(args.message) or {"error": "outcome unknown"},
        effect=True,
        retry_after_error=False,
    )
    with effect_scope():
        tools.execute("notify", '{"message":"first"}')
        result = tools.execute("notify", '{"message":"edited retry"}')
        assert "already attempted" in result
    assert calls == ["first"]


def test_different_legitimate_effects_are_not_deduplicated(brain_factory):
    from iris_tools.notifications import NotificationArguments

    tools = brain_factory().brain._session("a").tools
    calls = []
    tools.skills["effect"] = Skill(
        "effect",
        "Test effect",
        NotificationArguments,
        lambda args: calls.append(args.message) or {"status": "accepted"},
        effect=True,
    )
    with effect_scope():
        tools.execute("effect", '{"message":"first"}')
        tools.execute("effect", '{"message":"second"}')
    assert calls == ["first", "second"]


def test_application_home_transport_is_reused_and_closed_after_drain(brain_factory):
    app = brain_factory(home_service_url="http://127.0.0.1:8768", home_service_token="a" * 32).brain
    home = app.tool_services.home
    with patch("iris_tools.home.httpx.Client") as factory:
        client = factory.return_value
        client.post.return_value = httpx.Response(200, json={"devices": []})
        home.execute("list", NoArguments())
        app._reset_sessions()
        home.execute("list", NoArguments())
        factory.assert_called_once()
        assert client.post.call_count == 2
        client.close.assert_not_called()
        assert app.lifetime.close()
        assert app.lifetime.close()
        client.close.assert_called_once()


def test_turn_failure_exposes_receipts_without_replacing_exception_type(brain_factory):
    from iris_domain.turn import TurnExecution

    harness = brain_factory(replies=[RuntimeError("synthetic failure")])
    execution = TurnExecution()
    with pytest.raises(RuntimeError) as failure:
        harness.brain.coordinator.execute(
            TurnInput("a", b"hi", text_only=True), execution=execution
        )
    assert failure.value.iris_execution is execution
    assert execution.phase == "failed" and not execution.effects.receipts


def test_client_controller_rejects_overlap_and_close():
    from iris_droid.controller import ClientController

    controller = ClientController({"Friend": "prompt"})
    with controller.turn():
        with pytest.raises(ValueError), controller.turn():
            pytest.fail("Overlapping turn admitted")
    with controller.turn():
        pass
    controller.close()
    with pytest.raises(ValueError), controller.turn():
        pytest.fail("Closed client admitted a turn")
    assert not controller.select("Friend")


def test_stalled_playback_aborts_with_a_finite_wait():
    from iris_droid.client import play_wav

    stream = Mock(active=True)
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.play"),
        patch("sounddevice.get_stream", return_value=stream),
        patch("iris_droid.client.monotonic", side_effect=[0, 6]),
    ):
        with pytest.raises(OSError, match="did not finish"):
            play_wav(write_wav(b"\1\0" * 1600, 16000))
    stream.abort.assert_called_once()
    stream.close.assert_called_once()


def test_shutdown_wakes_a_provisional_acceptance_wait():
    lifetime = ApplicationLifetime()
    ready, exited = Event(), Event()
    decision = VoiceDecision(lambda event: ready.set() if event == "ready" else None)

    def wait_for_acceptance():
        with lifetime.lease():
            try:
                decision.require_acceptance()
            except TurnExpired:
                exited.set()

    worker = Thread(target=wait_for_acceptance)
    worker.start()
    try:
        assert ready.wait(1)
        assert lifetime.close(timeout=1)
    finally:
        decision.decide(False)
        worker.join(timeout=1)
    assert not worker.is_alive() and exited.is_set() and not decision.accepted


def test_worker_startup_failure_reaps_process(tmp_path):
    import subprocess
    import sys

    from iris_tts_engines.worker import SpeechWorker, open_worker

    worker = tmp_path / "worker.py"
    worker.write_text("import sys\nfor line in sys.stdin:\n    pass\n")
    processes = []
    popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    with (
        patch("iris_tts_engines.worker.subprocess.Popen", side_effect=spawn),
        patch(
            "iris_tts_engines.worker.Thread.start", side_effect=RuntimeError("cannot start reader")
        ),
    ):
        with (
            pytest.raises(RuntimeError, match="cannot start reader"),
            open_worker([sys.executable, "-I", str(worker)], SpeechWorker, "test", {}),
        ):
            pytest.fail("Reader startup should fail")
    assert len(processes) == 1 and processes[0].poll() is not None


def test_backend_without_tools_cannot_dispatch_an_unadvertised_action():
    from iris_chat_engines.backends import BackendCapabilities
    from iris_chat_engines.compatible import ChatEngine
    from iris_domain.messages import ChatError
    from iris_settings import Settings
    from tests.support import ScriptedBackend

    model = ScriptedBackend(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="unexpected",
                        type="function",
                        function=FunctionCall(name="get_local_time", arguments="{}"),
                    )
                ],
            )
        ]
    )
    model.capabilities = BackendCapabilities(tools=False)
    with httpx.Client() as client:
        chat = ChatEngine(Settings(), client, backend=model)
        with patch.object(chat.tools, "execute") as execute:
            with pytest.raises(ChatError, match="tools are disabled"):
                chat.generate_reply("hello")
        execute.assert_not_called()
    assert not chat.history


def test_playback_uses_the_real_sounddevice_wrapper_without_duplicate_callbacks(monkeypatch):
    import numpy as np
    import sounddevice as sd

    from iris_droid.client import play_wav

    played = []
    streams = []

    class SyntheticStream:
        def __init__(self, *, callback, finished_callback, **kwargs):
            self.callback = callback
            self.finished_callback = finished_callback
            self.active = False
            self.closed = False
            streams.append(self)

        def start(self):
            output = np.empty((1600, 1), dtype="<i2")
            self.active = True
            try:
                self.callback(output, len(output), None, sd.CallbackFlags())
            except (sd.CallbackStop, sd.CallbackAbort):
                pass
            played.append(output.copy())
            self.active = False
            self.finished_callback()

        def close(self, *args):
            self.closed = True

    # Only the native stream is replaced. sd.play, get_stream and get_status are real.
    monkeypatch.setattr(sd, "_last_callback", None)
    monkeypatch.setattr(sd, "OutputStream", SyntheticStream)
    monkeypatch.setattr(sd, "check_output_settings", lambda **kwargs: None)
    play_wav(write_wav(b"\1\0" * 1600, 16000))
    assert len(streams) == 1 and streams[0].closed
    assert played[0].shape == (1600, 1) and np.all(played[0] == 1)
