"""Process boundaries, scheduler ordering and redacted timing diagnostics."""

import json
import logging
import sys
import time
from datetime import UTC, datetime
from threading import Event, Thread
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from iris_home.devices import Device, DeviceError
from iris_home.protocol import ScheduleRequest
from iris_home.schedule import Scheduler
from iris_tts_engines.chatterbox import Chatterbox
from iris_tts_engines.pocket import Pocket
from iris_tts_engines.worker import open_worker
from iris_turn import TurnExpired, diagnostic_turn, stage, turn_budget

# These are real tiny subprocesses with no speech dependencies or network access.
PRELUDE = """import json, sys, base64, struct, time
print(json.dumps({"status":"ready"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    rid = request["request_id"]
"""
AUDIO = """    print(json.dumps({"status":"audio", "request_id":rid, "rate":24000,
        "samples":base64.b64encode(struct.pack("<f", 0.5)).decode()}), flush=True)
"""
DONE = '    print(json.dumps({"status":"done", "request_id":rid}), flush=True)\n'


def worker_file(tmp_path, body):
    path = tmp_path / "worker.py"
    path.write_text(PRELUDE + body)
    return [sys.executable, "-I", "-u", str(path)]


def test_later_request_recovers_after_exit_without_replaying_failed_text(tmp_path):
    log = tmp_path / "calls.txt"
    body = f'    with open({str(log)!r}, "a") as f: f.write(request["text"] + "\\n")\n'
    body += '    if request["text"] == "fail": sys.exit(1)\n' + AUDIO + DONE
    with open_worker(worker_file(tmp_path, body), Chatterbox, "Synthetic", {}) as worker:
        with pytest.raises(RuntimeError, match="stopped"):
            worker.synthesize("fail")
        assert worker.health()["state"] == "failed"
        assert worker.synthesize("next")[0].tolist() == [0.5]
        assert worker.health()["restarts"] == 1
    assert log.read_text().splitlines() == ["fail", "next"]


def test_wrong_request_identity_retires_pipe_and_restart_budget_is_finite(tmp_path):
    body = "    rid += 100\n" + AUDIO + DONE
    with open_worker(worker_file(tmp_path, body), Chatterbox, "Synthetic", {}) as worker:
        for _ in range(3):
            with pytest.raises(RuntimeError, match="unrelated"):
                worker.synthesize("hello")
        with pytest.raises(RuntimeError, match="recovery limit"):
            worker.synthesize("hello")
        assert worker.health()["restarts"] == 2


def test_missing_terminal_frame_uses_total_turn_deadline(tmp_path):
    with open_worker(
        worker_file(tmp_path, AUDIO + "    time.sleep(60)\n"), Chatterbox, "Synthetic", {}
    ) as worker:
        with pytest.raises(TurnExpired), turn_budget(0.05):
            worker.synthesize("hello")
        assert worker.failed
    assert worker.process.poll() is not None


def test_abandoned_stream_is_retired_without_draining_then_restarts(tmp_path):
    body = AUDIO + "    time.sleep(60)\n"
    with open_worker(worker_file(tmp_path, body), Pocket, "Synthetic", {}) as worker:
        stream = worker.stream("first")
        assert next(stream)
        started = time.monotonic()
        stream.close()
        assert time.monotonic() - started < 1
        assert worker.failed
        stream = worker.stream("second")
        assert next(stream)
        stream.close()
        assert worker.health()["restarts"] == 1


def test_stalled_stdin_cannot_hold_turn_deadline(tmp_path):
    path = tmp_path / "no_reader.py"
    path.write_text('import time\nprint(\'{"status":"ready"}\', flush=True)\ntime.sleep(60)\n')
    with open_worker(
        [sys.executable, "-I", "-u", str(path)], Chatterbox, "Synthetic", {}
    ) as worker:
        # Fill beyond the pipe capacity, without exceeding the request frame cap.
        with pytest.raises((TurnExpired, RuntimeError)), turn_budget(0.05):
            worker.synthesize("x" * 60000)
        assert worker.failed
    assert worker.process.poll() is not None


def test_overlapping_worker_request_is_rejected_without_corrupting_first(tmp_path):
    with open_worker(worker_file(tmp_path, AUDIO + DONE), Pocket, "Synthetic", {}) as worker:
        stream = worker.stream("first")
        assert next(stream)
        with pytest.raises(RuntimeError, match="already synthesizing"):
            worker.synthesize("second")
        assert list(stream) == []
        assert not worker.failed


class Switch:
    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.calls = []
        self.block = False
        self.fail = False

    def set_state(self, state):
        self.calls.append(state)
        self.entered.set()
        if self.block:
            assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("private adapter credential")


def make_scheduler(tmp_path, switches):
    scheduler = Scheduler(
        tmp_path / "home.sqlite3",
        ZoneInfo("UTC"),
        {k: Device(v, {}) for k, v in switches.items()},
        clock=lambda: datetime(2026, 11, 7, 17, tzinfo=UTC),
    )
    for device in switches:
        scheduler.save(
            device,
            ScheduleRequest(
                device=device, revision=0, rules=[{"days": ["sat"], "time": "18:00", "state": "on"}]
            ),
        )
    return scheduler


def test_schedule_reads_and_edits_do_not_wait_for_device_io(tmp_path):
    switch = Switch()
    switch.block = True
    scheduler = make_scheduler(tmp_path, {"lamp": switch})
    thread = Thread(target=scheduler._next_delay, args=(datetime(2026, 11, 7, 19, tzinfo=UTC),))
    thread.start()
    try:
        assert switch.entered.wait(1)
        assert scheduler.read("lamp")["revision"] == 1
        scheduler.save("lamp", ScheduleRequest(device="lamp", revision=1, rules=[]))
        cursor = scheduler.db.execute("SELECT cursor FROM schedules").fetchone()[0]
    finally:
        switch.release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert scheduler.read("lamp")["revision"] == 2
    assert scheduler.db.execute("SELECT cursor FROM schedules").fetchone()[0] == cursor
    scheduler.db.close()


def test_manual_change_cannot_be_overwritten_by_stale_planned_work(tmp_path):
    switch = Switch()
    scheduler = make_scheduler(tmp_path, {"lamp": switch})
    scheduler.clock = lambda: datetime(2026, 11, 7, 19, tzinfo=UTC)
    scheduler.switch("lamp", "off")
    scheduler._next_delay(scheduler.clock())
    assert switch.calls == ["off"]
    scheduler.db.close()


def test_bad_schedule_and_adapter_do_not_kill_other_devices(tmp_path, caplog):
    broken, good, corrupt = Switch(), Switch(), Switch()
    broken.fail = True
    scheduler = make_scheduler(tmp_path, {"bad": broken, "good": good, "corrupt": corrupt})
    with scheduler.db:
        scheduler.db.execute("UPDATE schedules SET rules='not-json' WHERE device='corrupt'")
    assert scheduler._next_delay(datetime(2026, 11, 7, 19, tzinfo=UTC)) == 30
    assert good.calls == ["on"]
    with pytest.raises(DeviceError, match="saved schedule is invalid"):
        scheduler.read("corrupt")
    assert scheduler.health()["errors"] == {
        "bad": "device_unconfirmed",
        "corrupt": "invalid_schedule",
    }
    assert "private adapter credential" not in caplog.text
    broken.fail = False
    scheduler.save("corrupt", ScheduleRequest(device="corrupt", revision=1, rules=[]))
    scheduler._next_delay(datetime(2026, 11, 7, 19, tzinfo=UTC))
    assert scheduler.health()["errors"] == {}
    scheduler.db.close()


def test_diagnostics_correlate_stages_without_exception_or_conversation_text(caplog):
    with caplog.at_level(logging.INFO, logger="iris.performance"):
        with diagnostic_turn():
            with stage("recognition"):
                pass
            with pytest.raises(ValueError), stage("memory_interpretation"):
                raise ValueError("secret conversation and credentials")
    events = [json.loads(r.message.removeprefix("iris_stage ")) for r in caplog.records]
    assert len(events) == 2
    assert events[0]["turn"] == events[1]["turn"]
    assert len(events[0]["turn"]) == 32
    assert events[1]["outcome"] == "failed"
    assert "secret conversation" not in caplog.text
    assert all(e["duration_ms"] >= 0 for e in events)
    with pytest.raises(ValueError, match="Unknown diagnostic stage"):
        with stage("private prompt"):
            pass


def test_worker_environment_does_not_inherit_service_credentials(monkeypatch):
    from iris_tts_engines.pocket import worker_environment

    monkeypatch.setenv("IRIS_API_KEY", "secret")
    monkeypatch.setenv("HOME_SERVICE_TOKEN", "secret")
    monkeypatch.setenv("HF_TOKEN", "secret")
    monkeypatch.setenv("PATH", "/test/path")
    env = worker_environment()
    assert env["PATH"] == "/test/path"
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert not {"IRIS_API_KEY", "HOME_SERVICE_TOKEN", "HF_TOKEN"} & env.keys()


@pytest.mark.parametrize("owner", ["alice", "bob"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("dated", [False, True])
@pytest.mark.parametrize("active_old", [False, True])
def test_bounded_vector_query_matches_original_candidates_exactly(
    tmp_path, monkeypatch, owner, explicit, dated, active_old
):
    from contextlib import contextmanager

    import numpy as np

    from iris_memory import Exchange, SQLiteMemory
    from iris_memory.semantic import SemanticMemory

    class Encoder:
        model_id = "phase4"
        dimensions = 3

        def encode(self, texts):
            return np.asarray([[1, 0, 0] for _ in texts], dtype=np.float32)

    archive = SQLiteMemory(tmp_path / "archive.sqlite3")
    conversations = {}
    for person in ("alice", "bob"):
        conversation = archive.start_conversation(person)
        conversations[person] = conversation
        for number in range(12):
            archive.append(
                person, conversation, Exchange(f"{person}{number}", "human", "Garden plan", "Reply")
            )
        archive.start_conversation(person)  # Block unsolicited retrieval of the old discussion.
    semantic = SemanticMemory(archive, Encoder())
    for person in ("alice", "bob"):
        while semantic.pending(person):
            semantic.index_batch(person)
    with archive.connect() as db:
        db.execute("UPDATE exchanges SET created_at='2000-01-01T00:00:00+00:00' WHERE sequence%2=0")
        db.execute("UPDATE chunks SET superseded=1 WHERE id%3=0")
        db.execute("UPDATE embedding_vectors SET vector=x'' WHERE chunk%5=0")
    monkeypatch.setattr("iris_memory.semantic.SEMANTIC_CANDIDATE_LIMIT", 6)
    original_connect = archive.connect
    seen = []

    class Compare:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, args):
            # Flatten only the payload-fetching subquery to reconstruct the old
            # candidate SQL. The filters, bindings and LIMIT remain identical.
            prefix, rest = sql.split("FROM (SELECT c.id ", 1)
            body, _ = rest.split(") chosen ", 1)
            reference = prefix + body
            expected = list(self.db.execute(reference, args[:-1]))
            actual = list(self.db.execute(sql, args))
            assert [tuple(r) for r in actual] == [tuple(r) for r in expected]
            assert len(actual) <= 6
            seen.append(True)
            return iter(actual)

    @contextmanager
    def compare():
        with original_connect() as db:
            yield Compare(db)

    with patch.object(archive, "connect", compare):
        semantic.search(
            owner,
            "garden",
            explicit=explicit,
            active=conversations[owner] if active_old else None,
            end=datetime(2001, 1, 1, tzinfo=UTC) if dated else None,
        )
    assert seen == [True]


def test_deferring_summary_changes_current_turn_evidence_so_serial_order_is_required(tmp_path):
    from unittest.mock import Mock

    from iris_chat_engines.messages import Message
    from iris_memory import Exchange, SQLiteMemory
    from iris_memory.context import MemoryContext
    from iris_memory.index import MemoryIndex
    from iris_settings import Settings

    archive = SQLiteMemory(tmp_path / "archive.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append(
        "alice",
        conversation,
        Exchange("source", "human", "The robot needs a blue hat", "A blue hat suits it"),
    )
    index = MemoryIndex(archive)
    backend = Mock()
    backend.complete.return_value = Message(
        role="assistant",
        content=json.dumps(
            {
                "notes": [
                    {
                        "kind": "decision",
                        "text": "The robot needs a blue hat",
                        "sources": ["source"],
                        "scope": "fictional",
                        "status": "confirmed",
                    }
                ]
            }
        ),
    )
    context = MemoryContext(
        Settings(memory_summary_interval=1, memory_hierarchy_enabled=False), index, backend
    )
    assert index.notes("alice", conversation) == []
    assert context.checkpoint("alice", conversation)
    assert index.notes("alice", conversation)[0]["text"] == "The robot needs a blue hat"
    # Running this checkpoint after generation would remove that evidence from
    # the current reply. No concurrency setting should quietly make that change.


@pytest.mark.parametrize("engine", ["pocket", "chatterbox"])
def test_real_worker_protocol_preserves_utf8_and_echoes_identity(monkeypatch, engine):
    from io import BytesIO, StringIO, TextIOWrapper
    from pathlib import Path
    from unittest.mock import Mock

    import numpy as np

    from iris_tts_engines import chatterbox_worker, pocket_worker

    text = "Here comes the punchline — ready? 🥁🐔"
    request = {"protocol": 1, "request_id": 17, "text": text}
    payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    # The parent writes UTF-8 bytes even when Windows selects an ANSI code page for stdin.
    stdin = TextIOWrapper(BytesIO(payload), encoding="cp1252", errors="surrogateescape")
    monkeypatch.setattr(sys, "stdin", stdin)
    output = StringIO()
    model = Mock()
    waveform = Mock()
    waveform.numpy.return_value = np.array([0.25, 0.5], dtype=np.float32)
    if engine == "pocket":
        model.sample_rate = 24000
        model.generate_audio_stream.return_value = iter([waveform])
        pocket_worker.serve(model, Path("unused"), output)
        model.generate_audio_stream.assert_called_once_with(
            model.get_state_for_audio_prompt.return_value, text
        )
    else:
        model.sr = 24000
        model.generate.return_value = waveform
        chatterbox_worker.serve(model, output)
        model.generate.assert_called_once_with(text)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [m["status"] for m in messages] == ["ready", "audio", "done"]
    assert all(m["request_id"] == 17 for m in messages[1:])


@pytest.mark.parametrize("instance", ["", "a" * 32, "invalid-private-sentinel"])
def test_authenticated_health_has_no_conversation_or_config_content(monkeypatch, instance):
    monkeypatch.setenv("IRIS_INSTANCE_ID", instance)
    from http.server import HTTPServer

    import httpx

    from iris_droid.server import handler_for
    from iris_settings import Settings
    from tests.support import ScriptedBackend, make_harness

    harness = make_harness(Settings(), ScriptedBackend())
    try:
        with HTTPServer(("127.0.0.1", 0), handler_for(harness.brain, "health-secret")) as server:
            thread = Thread(target=server.serve_forever)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/v1/health"
                with httpx.Client(trust_env=False) as client:
                    assert client.get(url).status_code == 401
                    reply = client.get(url, headers={"Authorization": "Bearer health-secret"})
                    assert reply.status_code == 200
                    assert reply.json() == {
                        "status": "ready",
                        "queue": {"active": False, "waiting": 0},
                        "speech": {"state": "in_process"},
                        **({"instance": instance} if instance == "a" * 32 else {}),
                    }
                    assert "health-secret" not in reply.text
            finally:
                server.shutdown()
                thread.join(2)
    finally:
        harness.transport.close()


def test_shutdown_interrupts_pipe_wait_before_speech_deadline(tmp_path):
    from threading import Timer

    from iris_turn import CURRENT_STOP

    with open_worker(
        worker_file(tmp_path, "    time.sleep(60)\n"), Chatterbox, "Synthetic", {}
    ) as worker:
        stop = Event()
        token = CURRENT_STOP.set(stop)
        timer = Timer(0.05, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with pytest.raises(TurnExpired, match="shutting down"):
                worker.synthesize("hello")
        finally:
            CURRENT_STOP.reset(token)
            timer.join()
        assert time.monotonic() - started < 1
        assert worker.failed


def test_scheduler_shutdown_does_not_close_database_beneath_blocked_adapter(tmp_path):
    switch = Switch()
    scheduler = make_scheduler(tmp_path, {"lamp": switch})
    switch.block = True
    scheduler.clock = lambda: datetime(2026, 11, 7, 19, tzinfo=UTC)
    scheduler.__enter__()
    assert switch.entered.wait(1)
    # Shorten only the join in this deterministic test, then allow the worker
    # to return and perform its own deferred database close.
    original_join = scheduler.worker.join
    with patch.object(scheduler.worker, "join", lambda timeout: original_join(0.01)):
        scheduler.__exit__()
    assert scheduler.worker.is_alive()
    assert scheduler.db.execute("SELECT count(*) FROM schedules").fetchone()[0] == 1
    switch.release.set()
    original_join(2)
    assert not scheduler.worker.is_alive()
    assert scheduler._closed


def test_synthesis_budget_does_not_restart_for_terminal_frame(tmp_path, monkeypatch):
    monkeypatch.setattr("iris_tts_engines.worker.SYNTHESIS_TIMEOUT", 0.08)
    body = "    time.sleep(0.04)\n" + AUDIO + "    time.sleep(0.06)\n" + DONE
    with open_worker(worker_file(tmp_path, body), Chatterbox, "Synthetic", {}) as worker:
        with pytest.raises((RuntimeError, TurnExpired)), turn_budget(600):
            worker.synthesize("hello")
        assert worker.failed


def test_manual_command_invalidates_an_already_planned_schedule_action(tmp_path):
    first, second = Switch(), Switch()
    first.block = True
    scheduler = make_scheduler(tmp_path, {"first": first, "second": second})
    scheduler.clock = lambda: datetime(2026, 11, 7, 19, tzinfo=UTC)
    thread = Thread(target=scheduler._next_delay, args=(scheduler.clock(),))
    thread.start()
    try:
        assert first.entered.wait(1)
        scheduler.switch("second", "off")
    finally:
        first.release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert second.calls == ["off"]
    scheduler.db.close()


def test_download_environment_keeps_explicit_proxy_but_not_iris_keys(monkeypatch):
    from iris_tts_engines.worker import isolated_environment

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("HF_TOKEN", "model-download-secret")
    monkeypatch.setenv("IRIS_API_KEY", "iris-secret")
    assert "HTTPS_PROXY" not in isolated_environment()
    assert isolated_environment(download=True)["HTTPS_PROXY"] == "http://proxy.invalid"
    assert isolated_environment(download=True)["HF_TOKEN"] == "model-download-secret"
    assert "IRIS_API_KEY" not in isolated_environment(download=True)


def test_scheduler_reports_clock_wait_and_readiness(tmp_path):
    ready = Event()
    with Scheduler(
        tmp_path / "clock.sqlite3", ZoneInfo("UTC"), {}, clock_ready=ready.is_set
    ) as scheduler:
        assert scheduler.health()["state"] == "waiting_for_clock"
        ready.set()
        with scheduler.condition:
            scheduler.condition.notify()
        deadline = time.monotonic() + 1
        while scheduler.health()["state"] != "ready" and time.monotonic() < deadline:
            with scheduler.condition:
                scheduler.condition.notify()
            time.sleep(0.01)
        assert scheduler.health()["state"] == "ready"
