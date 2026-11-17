# How Iris fits together

Iris has three main jobs: understand an input, work out a response, and deliver
that response to the person who asked. Keeping those jobs separate matters when
we add a model, a device or a new way of talking to her. A change to a microphone
shouldn't require understanding how the conversation archive works.

The code is arranged around these boundaries:

| Part | What belongs there |
| --- | --- |
| `iris_domain` | Shared values: messages, session state, commands, personalities, replies and audio capabilities. These modules don't open devices, databases or network connections. |
| `iris_app` | The order of operations for a turn, exact command routing, memory preparation, reply preparation and ownership of running work. |
| `iris_chat_engines` | The model conversation loop and provider adapters. A backend implements `complete(messages, tools)`. |
| `iris_memory` | The SQLite archive, retrieval, derived summaries, facts and memory policies. |
| `iris_tools` | Validated tool definitions, execution policy and the services those tools call. |
| `iris_stt_engines` and `iris_tts_engines` | Speech recognition and synthesis adapters. Isolated synthesis backends share `iris_tts_engines.worker`. |
| `iris_droid` | HTTP and device adapters, the Pi client, display, mixer and browser assets. |

`iris_app.runtime.Brain` composes the application services. Its existing
`turn(session, body, ...)` method is still available, including through the
`iris_droid.server` import. New application code can call
`brain.coordinator.execute(TurnInput(...))` directly. It doesn't need an HTTP
request, a microphone or a display.

## What happens during a turn

The coordinator admits one turn at a time. The HTTP adapter has a separate FIFO
queue for completed uploads, so a slow upload doesn't occupy the conversation.
A direct application caller also goes through the coordinator's lock and deadline.

1. Iris refreshes the owner's privacy policy and reads or transcribes the input.
2. The command parser recognises exact controls. The router applies their existing
   precedence and acceptance rules before ordinary model conversation begins.
3. For a conversation turn, Iris restores the current archive revision and takes
   a checkpoint of the reversible session state.
4. The memory orchestrator prepares recall choices, labelled details, facts and
   retrieved context. These are derived views of the saved conversations.
5. The model loop gets a response, including any permitted tool calls. External
   actions have their own attempt policy and receipts.
6. The reply service prepares audio or hands audio chunks to the caller's stream
   sink. The HTTP adapter owns the wire format and socket writes.
7. Once the required acceptance gate has passed, Iris saves the completed
   exchange and returns its completion metadata.

Streaming deliberately overlaps synthesis and delivery. Hearing the beginning of
an answer isn't proof that its archive commit succeeded. Short memory controls
prepare their complete confirmation before applying the change. The details of
that distinction are in [the runtime contracts](runtime-contracts.md).

`TurnExecution` exposes prepared, accepted, completed, discarded and failed
states. Ordinary requests begin accepted; cancellable voice requests remain
provisional until their acceptance gate passes. An execution object can be
supplied by an application caller, and failures also carry it as
`error.iris_execution`. Admission failures can happen before an execution starts.

## What rollback can and cannot do

`SessionState` lists the reversible conversation fields. Checkpoint and restore
copy those values deeply, including nested messages and pending choices. They
never copy a backend, transport, lock or tool service. Adding a session field to
that dataclass includes it in both operations automatically.

Discarding an unaccepted voice turn also restores the session order and any
session displaced by the cache limit. A newer privacy-policy epoch supersedes
that restoration. Archive changes still use their own transactions and policy
checks; the in-memory checkpoint isn't a database transaction.

A switch that has already turned on cannot be turned back off by restoring chat
history. `TurnEffects` therefore lives outside the session checkpoint. Its
receipts record the tool name and whether an attempt returned or had an uncertain
outcome. “Returned” means the service returned a non-error result, not that a
notification was read or that physical hardware reached the requested state.

Receipts are available to the application caller even if later model generation
or synthesis fails. They aren't a new durable archive, and they contain no tool
arguments or conversation text. Attempt keys are cleared when the turn exits.
There is no cross-request exactly-once promise: check an uncertain external action
before asking Iris to repeat it.

## Adding a model, command or tool

A model adapter implements `ChatBackend.complete`. `Brain` accepts a
`backend_factory` for tests and alternative adapters. Simple fake backends don't
need any other methods. Optional `BackendCapabilities` declares tool support and
whether completion reasons are available. The HTTP adapter retains the provider's
finish reason on the returned message as private metadata, so it isn't sent back
as part of model history. A length-limited reply keeps its existing behaviour;
recording the reason doesn't silently change how much context Iris retains.
The conservative token estimator remains the fallback.

Exact controls and model-selected tools are different entry points. A control
extension registers a phrase and a handler with `brain.commands.register`.
Built-in controls keep their precedence and can't be replaced by an extension.
The handler receives a `CommandRequest` after any required voice acceptance.
Keep recognition exact, and add a phrase case, behaviour test and menu/help entry
when introducing a control people will use.

A tool registers a `Skill` with its argument schema and callable. Mark external
actions with `effect=True`; read-only tools keep the default. Equivalent validated
arguments identify a repeated effect within a turn, regardless of the model's
call ID. Set `retry_after_error=False` when even an edited retry is prohibited;
notifications use this policy. Different successful requests remain distinct.
No policy here retries an action automatically.

Tool registries are session-specific, but application tool services live across
sessions. Notification cooldowns consequently survive clear-context and privacy
resets. The home service lazily opens one application-owned TLS client and reuses
it. Standalone registries keep per-call home connections unless an owner explicitly
requests the persistent service lifetime.

The memory orchestrator currently uses the concrete SQLite repository and its
index/hierarchy services. That is intentional: policy transactions and provenance
are real dependencies. A small, unused “generic store” interface would conceal
those requirements rather than make another database interchangeable.

## Clients and asynchronous work

The Python client's `ClientController` owns selection revisions, private-mode
state and admission of client turns. It rejects overlapping turns and requests
after close. `ClientOptions` is the typed startup boundary; argparse continues to
handle existing command-line arguments and saved configuration keys.

The browser's `ChatController` owns session identity, pending request identity,
personality revisions and the active audio identity. `chat.js` handles the DOM,
fetch, storage and browser audio. Replies can only update the controller that
started their request. A later personality choice wins over an earlier reply.
Leaving the page aborts the fetch and retires its identity; an old result cannot
change the page after it is restored from the browser's history.

Audio remains half-duplex: capture pauses while a turn is processed and played.
The adapter retains identity checks after microphone permission, worklet setup
and audio decoding, so a stopped microphone cannot reappear after an await.

## Shutdown and resource ownership

The composition root transfers its HTTP and synthesis resources to
`ApplicationLifetime`. A request holds a lease while it can use those resources.
Shutdown stops new leases, signals active work to stop at its next cooperative
boundary, and waits up to five seconds for the leases to drain. Cleanup runs once,
after the last lease exits. A turn already holding a resource never races its
owner closing that resource underneath it.

An in-process native inference call cannot safely be killed by a Python thread.
If it outlasts the drain wait, its lease retains the resources and performs cleanup
when it returns. This is a bounded shutdown wait, not a promise that every native
library can be interrupted. A service manager can still terminate the process.

Isolated speech workers have bounded graceful-exit, kill/reap and reader-join
waits. They no longer rely on an implicit unbounded `Popen` context exit. Client
cancellation listeners have bounded joins; a listener that won't stop cannot
cause acceptance of the pending turn. Playback completion waits use the audio's
duration plus a margin. Low-level device open, close and native inference calls
still depend on their platform libraries.

## Compatibility and checks

Existing imports for replies, WAV helpers, messages, personalities and shared
speech-worker types remain as explicit compatibility exports. New code should
import the owner named above. Adding an import alias doesn't make the old module's
private implementation details a supported extension API.

The tests include the original command corpus, direct coordinator and controller
contracts, effect failures, deferred cleanup, HTTP compatibility, browser events
and an installed-wheel check outside the checkout. See
[testing](testing.md) for the commands. Synthetic audio and fake services make
these checks repeatable; they don't replace listening on the Pi or checking an
actual model and home device.

For repository result types, read snapshots, migration ownership and the complete
table dictionary, see [memory storage and recovery](memory-storage.md).
