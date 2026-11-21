"""Regression tests covering Phase 3 hardening and Phase 4 architecture improvements."""

from __future__ import annotations

import http.client
import json
from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pytest

from iris_app.runtime import Brain
from iris_app.sessions import SessionStore
from iris_chat_engines.backends import BackendCapabilities
from iris_chat_engines.compatible import ChatEngine
from iris_chat_engines.messages import Message
from iris_domain.audio import AudioBuffer
from iris_domain.replies import TurnReply
from iris_domain.volume import VolumeResult, VolumeState
from iris_droid.client_settings import ClientSettings, client_parser, settings_parser
from iris_droid.console_menu import ConsoleMenu
from iris_droid.display_power import IdleDisplayPower
from iris_droid.face_console import DisplayState
from iris_droid.face_display import FaceDisplay
from iris_droid.turn_state import ClientTurnState
from iris_home.devices import DRIVER_REGISTRY, ChipTool, Device, _decoded_report
from iris_home.protocol import ScheduleRequest
from iris_home.schedule import Scheduler
from iris_home.service import Config, HomeAPI, HomeServer
from iris_memory.protocols import DerivedView, VectorStore
from iris_memory.repository import SQLiteMemory
from iris_memory.semantic import SemanticMemory
from iris_memory.text import (
    RECALL_WORDS,
    STOP_WORDS,
    is_recall_request,
    query_terms,
    topic_terms,
    topical_terms,
)
from iris_settings import Settings
from iris_stt_engines import open_stt
from iris_tools import NoArguments, Skill, ToolRegistry

HOME_TOKEN = "0123456789abcdef0123456789abcdef"


class _FakeSwitch:
    def __init__(self, *, blocking_state: bool = False) -> None:
        self.blocking_state = blocking_state
        self.entered = Event()
        self.release = Event()
        self.last_state = "off"

    def state(self) -> str:
        if self.blocking_state:
            self.entered.set()
            assert self.release.wait(timeout=5)
        return self.last_state

    def set_state(self, state: str) -> None:
        self.last_state = state


def test_home_server_handles_concurrent_requests_and_ipv6_loopback_config(tmp_path: Path) -> None:
    cfg = Config(
        listen="::1",
        database=tmp_path / "home.db",
        matter_storage=tmp_path,
        devices={"lamp": {"node": 1, "switch_endpoint": 1}},
    )
    assert cfg.listen == "::1"
    devices = cfg.make_devices()
    assert "lamp" in devices
    assert devices["lamp"].sensors == {}
    assert "matter" in DRIVER_REGISTRY

    slow_switch = _FakeSwitch(blocking_state=True)
    test_devices = {"lamp": Device(slow_switch, {})}
    with (
        Scheduler(tmp_path / "sched.db", ZoneInfo("UTC"), test_devices) as scheduler,
        HomeServer(("127.0.0.1", 0), HomeAPI(test_devices, scheduler), HOME_TOKEN, None) as server,
    ):
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            headers = {"Authorization": f"Bearer {HOME_TOKEN}"}
            slow_result: list[int] = []

            def run_slow() -> None:
                with httpx.Client(trust_env=False) as client:
                    resp = client.post(
                        f"{url}/v1/status",
                        headers=headers,
                        json={"device": "lamp"},
                        timeout=5.0,
                    )
                    slow_result.append(resp.status_code)

            t = Thread(target=run_slow, daemon=True)
            t.start()
            assert slow_switch.entered.wait(timeout=3)
            with httpx.Client(trust_env=False) as client:
                fast = client.post(f"{url}/v1/list", headers=headers, json={}, timeout=2.0)
            assert fast.status_code == 200
            slow_switch.release.set()
            t.join(timeout=3)
            assert slow_result == [200]
        finally:
            server.shutdown()
            worker.join(timeout=3)


def test_home_scheduler_normalizes_utc_cursors_and_recovers_corrupt_rows(tmp_path: Path) -> None:
    switch = _FakeSwitch()
    devices = {"lamp": Device(switch, {})}
    now = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)
    db_path = tmp_path / "sched.db"
    with Scheduler(db_path, ZoneInfo("UTC"), devices, clock=lambda: now) as scheduler:
        # Insert an equivalent +02:00 cursor that is ahead of 10:30Z (13:00+02:00 == 11:00Z).
        with scheduler.db:
            scheduler.db.execute(
                "INSERT INTO schedules VALUES (?, ?, ?, ?)",
                ("lamp", "[]", 1, "2026-06-01T13:00:00+02:00"),
            )
        scheduler.switch("lamp", "on")
        row = scheduler.db.execute("SELECT cursor FROM schedules WHERE device='lamp'").fetchone()
        assert row[0] == "2026-06-01T13:00:00+02:00"

        # Now store an earlier +02:00 cursor (12:00+02:00 == 10:00Z < 10:30Z) -> normalizes to UTC.
        with scheduler.db:
            scheduler.db.execute(
                "UPDATE schedules SET cursor=? WHERE device='lamp'",
                ("2026-06-01T12:00:00+02:00",),
            )
        scheduler.switch("lamp", "off")
        row = scheduler.db.execute("SELECT cursor FROM schedules WHERE device='lamp'").fetchone()
        assert row[0] == now.isoformat()

        # Corrupt rules/cursor can be recovered via save(revision=0).
        with scheduler.db:
            scheduler.db.execute(
                "UPDATE schedules SET rules=?, revision=?, cursor=? WHERE device='lamp'",
                ("{not-json", 4, "2026-06-01T00:00:00"),
            )
        saved = scheduler.save("lamp", ScheduleRequest(device="lamp", revision=0, rules=[]))
        assert saved["revision"] == 5
        assert saved["rules"] == []


def test_chip_tool_filters_decoded_report_and_closes_interactive_process(tmp_path: Path) -> None:
    filtered = _decoded_report("CHIP:DMG: OnOff: FALSE\nCHIP:TOO:   OnOff: TRUE\nDone\n")
    assert filtered.strip() == "CHIP:TOO:   OnOff: TRUE"

    tool = ChipTool("chip-tool", tmp_path, interactive=True)
    fake_proc = Mock()
    fake_proc.poll.return_value = None
    fake_proc.stdin = Mock()
    fake_proc.stdout = iter(["CHIP:TOO:   OnOff: TRUE\n"])
    fake_proc.wait.return_value = 0
    tool._process = fake_proc
    tool.close()
    fake_proc.stdin.close.assert_called_once()
    fake_proc.terminate.assert_called_once()
    assert tool._process is None


def test_device_supports_optional_sensors() -> None:
    class _TempSensor:
        def read(self) -> dict[str, object]:
            return {"metric": "temperature", "value": 21.5, "unit": "C"}

    device = Device(_FakeSwitch(), {}, sensors={"ambient": _TempSensor()})
    assert device.sensors["ambient"].read()["value"] == 21.5


def test_idle_display_power_wake_handles_oserror() -> None:
    power = IdleDisplayPower(Mock())
    power.asleep = True
    power._set_power = Mock(side_effect=OSError("drm offline"))
    power.wake()
    assert power.asleep is False
    assert power._unavailable is True


def test_face_display_sse_streams_json_state() -> None:
    display = FaceDisplay("rivet", "snow")
    with display.serve(0) as port:
        assert port is not None
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        try:
            conn.request("GET", "/events", headers={"Host": f"127.0.0.1:{port}"})
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "text/event-stream; charset=utf-8"
            line = resp.fp.readline().decode("utf-8")
            assert line.startswith("data: ")
            payload = json.loads(line.removeprefix("data: "))
            assert payload["head"] == "rivet"
            assert payload["activity"] == "idle"
        finally:
            conn.close()


def test_console_menu_awaiting_busy_prevents_double_tap() -> None:
    pygame = Mock()
    pygame.font.Font.return_value = Mock(
        size=lambda t: (len(t) * 8, 16), get_linesize=lambda: 20, render=lambda *_: Mock()
    )
    pygame.draw = Mock()
    screen = Mock()
    screen.get_size.return_value = (800, 480)
    future: Future[bool] = Future()
    future.set_result(True)
    menu = ConsoleMenu(pygame, screen, select=lambda _: Future(), execute=lambda _: future)
    menu.command_request = future
    ready_state = DisplayState("rivet", "idle", "neutral", "snow", commands_ready=True)
    menu.draw(ready_state, 10.0)
    assert menu._awaiting_busy is True
    assert menu.command_ready is False
    # After the 2-second grace window passes, ready resumes if server still reports ready.
    menu.draw(ready_state, 12.1)
    assert menu._awaiting_busy is False
    assert menu.command_ready is True


def test_client_settings_and_turn_state() -> None:
    parser = client_parser(settings_parser())
    defaults = parser.parse_args([])
    opts = ClientSettings.from_namespace(defaults)
    assert opts.server == "http://127.0.0.1:8765"

    bad_vad = parser.parse_args(["--vad-threshold", "1.5"])
    with pytest.raises(ValueError, match="vad-threshold"):
        ClientSettings.from_namespace(bad_vad)

    bad_server = parser.parse_args(["--server", "ftp://127.0.0.1:8765"])
    with pytest.raises(ValueError):
        ClientSettings.from_namespace(bad_server)

    bad_limit = parser.parse_args(["--phrase-time-limit", "500"])
    with pytest.raises(ValueError, match="Recording limit"):
        ClientSettings.from_namespace(bad_limit)

    state = ClientTurnState()
    assert state.phase == "sleeping"
    assert state.should_activate_wake(was_wake_active=False) is True
    state.begin_listening("touch")
    assert state.should_activate_wake(was_wake_active=False) is False
    assert state.should_activate_wake(was_wake_active=True) is True
    state.begin_processing("voice")
    assert state.phase == "processing"
    state.begin_speaking()
    assert state.phase == "speaking"
    state.finish_turn(wake_active=True)
    assert state.phase == "listening"


def test_audio_buffer_and_open_stt_closes_engine() -> None:
    buf = AudioBuffer(b"\x00\x01", 16000)
    assert buf.channels == 1
    assert buf.sample_width == 2

    fake_stt = Mock()
    settings = Settings()
    with (
        patch("iris_stt_engines.make_stt", return_value=fake_stt),
        pytest.raises(RuntimeError, match="boom"),
    ):
        with open_stt(settings) as engine:
            assert engine is fake_stt
            raise RuntimeError("boom")
    fake_stt.close.assert_called_once()


def test_tool_registry_register_and_skill_instructions() -> None:
    settings = Settings(tools_enabled=True, weather_enabled=False, notifications_enabled=False)
    with httpx.Client(trust_env=False) as client:
        registry = ToolRegistry(settings, client)
        assert "Open-Meteo" not in registry.instructions()
        registry.register(
            Skill(
                "custom_timer",
                "Set a named kitchen timer.",
                NoArguments,
                lambda _: {"ok": True},
                instructions="Always state remaining minutes briefly.",
            )
        )
        assert "custom_timer" in registry.skills
        assert "Always state remaining minutes briefly." in registry.instructions()


def test_session_store_lru_eviction_and_owner_scoped_policies() -> None:
    created = 0
    with httpx.Client(trust_env=False) as client:

        def make_chat() -> ChatEngine:
            nonlocal created
            created += 1
            backend = Mock()
            backend.capabilities = BackendCapabilities()
            return ChatEngine(Settings(), client, backend=backend)

        store = SessionStore(make_chat, max_sessions=2, default_owner="alice")
        c1 = store.session("s1")
        _c2 = store.session("s2")
        assert store.session("s1") is c1  # s1 becomes MRU
        c3 = store.session("s3")  # evicts s2
        assert created == 3
        assert store.get("s2") is None
        assert store.get("s1") is c1
        assert store.get("s3") is c3

        snap = store.snapshot()
        assert store.pop("s3") is c3
        assert store.get("s3") is None
        store.restore(snap)
        assert store.get("s3") is c3

        assert store.set_policy(True, 4) is True
        assert store.policy() == (True, 4)
        assert store.policy("bob") == (False, 0)
        assert store.set_policy(True, 2, owner="bob") is True
        assert store.policy() == (True, 4)


def test_brain_volume_result_and_extraction_model_wiring(tmp_path: Path) -> None:
    seen_models: list[str] = []

    def fake_backend_factory(cfg: Settings, _client: httpx.Client) -> Mock:
        seen_models.append(cfg.model)
        backend = Mock()
        backend.capabilities = BackendCapabilities()
        backend.complete.return_value = Message(role="assistant", content="ok")
        return backend

    with pytest.raises(ValueError, match="must not be blank"):
        Settings(memory_extraction_model="   ")

    settings = Settings(
        model="main-llm",
        memory_enabled=True,
        memory_database=tmp_path / "memory.db",
        memory_extraction_model=" small-extractor ",
        memory_semantic_enabled=False,
    )
    assert settings.memory_extraction_model == "small-extractor"

    tts = Mock()
    tts.synthesize.return_value = (np.zeros(8, dtype=np.float32), 24000)
    with httpx.Client(trust_env=False) as client:
        brain = Brain(settings, client, Mock(), tts, backend_factory=fake_backend_factory)
        try:
            assert seen_models == ["small-extractor"]
            chat = brain._session("sess-1")
            assert seen_models == ["small-extractor", "main-llm"]
            chat.companion_mode = True
            reply: TurnReply = brain.volume_result(
                "sess-1",
                VolumeResult(state=VolumeState(levels=[45], muted=False)),
                text_only=True,
            )
            assert reply.companion_mode is True
            assert reply.command is not None
            assert reply.command.title == "Speaker volume"
            assert reply.command.status == "success"
            assert "45 percent" in reply.text
        finally:
            brain.lifetime.close()


def test_semantic_memory_satisfies_protocols_and_text_helpers(tmp_path: Path) -> None:
    class _FakeEncoder:
        model_id = "test-minilm"
        dimensions = 4

        def encode(self, texts: list[str]) -> np.ndarray:
            arr = np.ones((len(texts), 4), dtype=np.float32) * 0.5
            return arr

    archive = SQLiteMemory(tmp_path / "archive.db")
    semantic = SemanticMemory(archive, _FakeEncoder(), minimum=0.3)
    assert isinstance(semantic, DerivedView)
    assert isinstance(semantic, VectorStore)
    assert "remember" in STOP_WORDS
    assert "discussed" in RECALL_WORDS
    assert query_terms("Testing hybrid photovoltaic batteries") == [
        "testing",
        "hybrid",
        "photovoltaic",
        "batteries",
    ]
    assert topical_terms("Did we discuss photovoltaic batteries yesterday?") == [
        "photovoltaic",
        "batteries",
    ]
    assert "let" not in topic_terms("Let us discuss batteries")
    assert is_recall_request("Do you remember what we discussed yesterday?") is True
    assert is_recall_request("Remember to buy milk.") is False
