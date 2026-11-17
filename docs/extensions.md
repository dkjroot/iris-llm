# Adding something to Iris

There are three different places to add behaviour. A backend talks to a model;
a control responds to an exact user command; a tool lets the model request a
validated operation. Pick the boundary that matches the job. Model output and
recalled history must never become an exact user control.

The examples below run in a development checkout. They use the same fake harness
as the tests, so they won't contact a real model or open an audio device. Put the
corresponding production registration in your composition code when creating
`Brain` or its session tools; Iris does not scan directories for arbitrary plugins.

## A conversation backend

A backend implements `complete(messages, tools)` and returns a `Message`.
`Brain` accepts a `backend_factory(settings, client)` when it is constructed.
Use the supplied HTTP client's lifetime rather than creating a new client for
every request. Declare optional `BackendCapabilities` if the provider has tool
support or completion reasons. Keep provider-only metadata out of messages sent
back to the model.

This test starts at the application boundary and deliberately fails if Iris makes
an unexpected extra model call:

```python
import httpx
from iris_app.runtime import Brain
from iris_chat_engines.messages import Message
from iris_settings import Settings
from tests.support import FakeSTT, FakeTTS, ScriptedBackend

backend = ScriptedBackend([Message(role="assistant", content="Hello from the test.")])
with httpx.Client(transport=httpx.MockTransport(backend.handle)) as client:
    brain = Brain(
        Settings(),
        client,
        FakeSTT(),
        FakeTTS(),
        backend_factory=lambda settings, transport: backend,
    )
    reply = brain.turn("extension-test", b"Hello", text_only=True)
    assert reply.text == "Hello from the test."
    assert len(backend.calls) == 1
```

For a real adapter, test tool calls, unsupported tools, empty replies, invalid
responses, deadlines and provider failures. A length-limited completion must
remain visible as such; do not silently discard old archive sources to make it fit.

## An exact control

Register a unique phrase with `brain.commands.register(phrase, handler)`. The
handler receives a `CommandRequest` containing the session and client
capabilities, after any required voice acceptance. Built-in controls keep their
precedence and cannot be overridden. Use the reply service to prepare speech,
and carry through the current session's companion state.

```python
from iris_app.commands import CommandRequest
from iris_settings import Settings
from tests.support import ScriptedBackend, make_harness

harness = make_harness(Settings(), ScriptedBackend())
brain = harness.brain


def ping(request: CommandRequest):
    session = brain.sessions.get(request.session)
    return brain.replies.prepare(
        request.heard,
        "The extension is here.",
        text_only=request.text_only,
        companion=session is not None and session.companion_mode,
    )


brain.commands.register("Extension ping", ping)
try:
    reply = brain.turn("extension-test", b"Extension ping", text_only=True)
    assert reply.text == "The extension is here."
finally:
    harness.transport.close()
```

A real control also needs tests for provisional voice cancellation, repeated
requests and disabled capabilities. Add its phrase and any capability limitation
to `iris_droid/web/commands.json`, then update the appropriate user guide. The
Pi and browser load that same list. Recall choices and voice cancellation are
stateful controls, so do not turn them into unconditional router commands.

## A model-selected tool

A `Skill` combines a name, description, argument model and callable. Strict
arguments reject accidental extra fields before the callable runs. Register it
on the session's `ToolRegistry.skills` dictionary:

```python
import json
import httpx
from iris_settings import Settings
from iris_tools import NoArguments, Skill, ToolRegistry

with httpx.Client() as client:
    registry = ToolRegistry(Settings(), client)
    registry.skills["get_example_status"] = Skill(
        "get_example_status",
        "Read the example's current status.",
        NoArguments,
        lambda arguments: {"status": "ready"},
    )
    result = json.loads(registry.execute("get_example_status", "{}"))
    assert result == {"status": "ready"}
```

For a tool that changes something outside Iris, set `effect=True`. Equivalent
validated arguments then identify a repeated attempt within the same turn. Set
`retry_after_error=False` when even a changed retry is inappropriate, as with
notifications. Neither setting provides cross-request exactly-once delivery.
A lost response can leave the physical outcome uncertain; do not retry blindly.

Shared service objects belong to `ToolServices` and the application lifetime,
not to a conversation checkpoint. Test success, validation failure, timeout,
ambiguous completion, same-turn repeats and later speech failure. Use fake
switches or notification transports so a test can't act on your house or phone.

See [runtime contracts](runtime-contracts.md) for acceptance and effect receipts,
and [architecture](architecture.md) for shutdown ownership and memory boundaries.

## A speech backend

A recogniser implements `ISTT.recognize(AudioData)` and `is_offline()`. A
synthesiser implements `synthesize(text)`, returning a mono float32 NumPy array
and its sample rate. Streaming adds `sample_rate` and a generator of little-endian
16-bit mono PCM bytes. That generator is a separate capability: returning a
generator from `synthesize` is not the same interface.

This small adapter deliberately says one fixed sentence and emits a quiet tone.
It checks the application boundary, recogniser input and actual WAV output
without loading weights or opening a microphone:

```python
import base64
import httpx
import numpy as np
from iris_app.runtime import Brain
from iris_audio import read_wav, write_wav
from iris_chat_engines.messages import Message
from iris_settings import Settings
from tests.support import ScriptedBackend


class Recogniser:
    def recognize(self, audio):
        assert audio.sample_rate == 16000
        return "A speech adapter test."

    def is_offline(self):
        return True


class Voice:
    def synthesize(self, text):
        assert text == "The adapter works."
        return np.full(2400, 0.1, dtype=np.float32), 24000


backend = ScriptedBackend([Message(role="assistant", content="The adapter works.")])
with httpx.Client(transport=httpx.MockTransport(backend.handle)) as client:
    brain = Brain(
        Settings(),
        client,
        Recogniser(),
        Voice(),
        backend_factory=lambda settings, transport: backend,
    )
    try:
        reply = brain.turn("speech-example", write_wav(bytes(3200), 16000))
        pcm, rate = read_wav(base64.b64decode(reply.audio))
        assert reply.heard == "A speech adapter test."
        assert reply.text == "The adapter works."
        assert rate == 24000 and len(pcm) == 4800 and any(pcm)
    finally:
        brain.lifetime.close()
```

A production implementation also needs empty-input, malformed-output, model
failure and shutdown tests. If a library cannot be interrupted, isolate it behind
the [bounded speech worker protocol](../iris_tts_engines/worker.py). Test worker
exit, oversized frames and timeout with synthetic processes. Keep real inference
and audio-quality checks separately recorded for each prepared model/platform.
Register engine selection in the STT/TTS factories and settings/wizard together;
update install profiles, preparation help and this guide. Adding a new selectable
engine is a deliberate interface change, not something this example does.

## A device adapter

`Device` holds a `Switch` and any supported `Meter` implementations. A switch
reads `on`/`off` with `state()` and accepts `set_state(state)`. Raise `DeviceError`
when an outcome cannot be confirmed; do not invent success after a timeout.
The scheduler owns command ordering and schedule revisions. Here the genuine
service API and scheduler use a fake physical switch and a temporary database:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from iris_home.devices import Device
from iris_home.protocol import DeviceRequest, SwitchRequest
from iris_home.schedule import Scheduler
from iris_home.service import HomeAPI
from tests.support import FakeSwitch

lamp = FakeSwitch()
devices = {"example lamp": Device(lamp, {})}
with TemporaryDirectory() as directory:
    with Scheduler(Path(directory) / "schedule.sqlite3", ZoneInfo("UTC"), devices) as scheduler:
        api = HomeAPI(devices, scheduler)
        result = api.execute(SwitchRequest(device="example lamp", state="on"), "switch")
        assert result["accepted"] is True
        assert api.execute(DeviceRequest(device="example lamp"), "status")["state"] == "on"
        assert lamp.commands == ["on"]
```

For another protocol, implement its transport, validate configuration in the
home service, and construct the device in `Config.make_devices`. Don't teach
the conversation loop about sockets or vendor command lines. Extend the shared
protocol only when a new capability needs it. Add offline/timeout, meter units,
ambiguous action, concurrent schedule edit and restart tests; use a harmless
real device for final commissioning/read-back. Update the Home menu examples
when a user-visible capability changes. The fake above checks integration
ordering; it cannot establish that an electrical device actually switched.
