# Timing and recovery without guessing

A slow reply can come from several places: waiting for another client, speech
recognition, memory interpretation, a model request, speech synthesis or playback.
Iris can now report those stages separately without turning on prompt tracing.
The timings use a local monotonic clock, which measures elapsed time without
being affected by wall-clock corrections.

## Collecting timings

Set `IRIS_PERFORMANCE_LOG=1` in the environment of the server or client before
starting it. In PowerShell, use `$env:IRIS_PERFORMANCE_LOG = "1"`; in a macOS or
Linux shell, use `export IRIS_PERFORMANCE_LOG=1`. For a service, set it in that
service's environment and restart the service. Unset it and restart to stop
collecting the events. Normal operation leaves these timings off.

Each `iris_stage` line contains a randomly generated turn correlation ID, a fixed
stage name, elapsed milliseconds and an outcome of `ok`, `failed` or `expired`.
It contains no transcript, model response, tool arguments, request headers,
conversation IDs or exception message. The correlation ID is temporary; it isn't
an archive identifier. Private and discarded turns can still produce these
text-free timing events. The separate `trace` setting can print prompt/response
content; it isn't needed for performance logging.

| Stage | What it measures |
| --- | --- |
| `queue` | Waiting for the HTTP turn queue after a bounded upload. |
| `application_queue` | Waiting for exclusive access to the application, including direct callers. |
| `recognition` | Converting recorded speech to text. |
| `memory_interpretation` | Preparing and validating the current turn's fact/topic interpretation. |
| `summary` | One serial summary checkpoint, including its model request and guarded commit. |
| `indexing` | One bounded batch of semantic indexing. |
| `retrieval` | Semantic candidate selection, query embedding and scoring. |
| `model` | One request through the chat-completions adapter. Several requests can belong to one turn. |
| `tts` | Buffered synthesis, or one step of streamed audio generation between yields. |
| `stream_delivery` | The whole streamed reply, including synthesis and waiting for its receiver. |
| `archive_commit` | Saving the completed original exchange and its source-linked metadata. |
| `playback` | The client's playback operation, including waiting for streamed audio where applicable. |
| `turn` | The application turn as a whole, including its application queue wait. |

Some measurements contain other measurements. A summary includes a model call,
and stream delivery includes several synthesis steps. Don't add every duration
together as if they were independent. Server and client IDs and clocks are local;
this isn't a distributed trace, and physical speaker latency still needs a
measurement on the actual device.

## Checking service health

The Iris server's authenticated `GET /v1/health` endpoint returns application
state, the number of waiting turns, whether a turn is active, and isolated speech
worker state. Use the same bearer token as the other API calls. It reports no
configuration values or conversation data. `ready` means the application is
available; it doesn't prove an external model host or physical speaker will
answer the next request. An in-process synthesizer is labelled `in_process`
rather than claiming it has a separate supervised worker.

The home service includes a `scheduler` object in its existing authenticated
`POST /v1/devices` result. Its state distinguishes ready, waiting for clock synchronization, degraded, stopped
and stopping, with safe error categories for affected devices or the scheduler.
A bad saved schedule or an unexpected adapter exception no longer quietly kills
all scheduling while the HTTP service continues to answer.

## Speech worker lifecycle

Pocket and Chatterbox run in separate Python processes. Each request carries a
protocol version and increasing request ID; every audio and terminal response
must match that request. Chatterbox now sends an explicit terminal frame too.
The worker scripts and their parent adapter are shipped together, so update the
Iris installation as a unit.

Requests are limited to 64 KiB and responses use bounded frames and a two-frame
queue. Chatterbox checks the waveform limit before encoding it; Pocket bounds
both chunks and the total reply. The parent validates the response type, request
identity and audio before using it. A pipe writer thread prevents a child that
has stopped reading from blocking the application indefinitely.

A request has an overall 180-second synthesis budget, further limited by the
remaining turn deadline. An input write gets at most five seconds. Queue waits
check shutdown at least every 100 milliseconds. Closing an unfinished stream
retires that worker immediately instead of draining it for another long interval.
The failed request is never automatically replayed.

A later request may start a fresh worker, with at most two recovery starts in a
rolling minute. Startup has a 180-second ceiling, also limited by the current
turn's remaining budget. If recovery keeps failing, the server reports the limit
and needs attention instead of starting processes repeatedly. The model may need
time to load again, so that first recovery turn can be slower.

Cleanup kills the child, waits at most two seconds for exit, then gives each pipe
thread at most half a second to stop. If a pipe thread cannot stop, Iris refuses
further restarts instead of accumulating abandoned threads. Cleanup doesn't take
a stream lock held by such a thread. These are bounds on Iris's process handling;
native in-process recognition or synthesis still needs its adapter to return
before the application can release that resource safely.

Workers inherit an explicit set of platform, path and model execution variables.
Iris API keys, home-service credentials and unrelated service environment values
are not passed wholesale into the speech environment. Voice and model paths
remain explicit command arguments. Explicit model downloads retain their proxy,
certificate and model-download authentication settings; offline synthesis does not
inherit those network credentials.

## Home scheduling while a device is slow

Database access and device calls use separate locks. Schedule reads and edits
therefore remain available while a plug is offline. Calls to the same device
remain ordered, and the Matter adapter still serializes access to its controller
fabric files. This doesn't introduce parallel Matter commands.

The scheduler captures the schedule revision and cursor, checks them again before
issuing an action, and advances the cursor only if they are still unchanged after
the action. A successful manual switch supersedes older pending events. An edit
made while an action is already executing cannot retract the physical command,
but the old action cannot overwrite the edited schedule's cursor.

An unconfirmed device action retains the existing retry policy. It doesn't stop
other schedules from being considered. Invalid stored rules or cursors appear
as unhealthy state and are skipped; reads return a controlled service error.
Replace the invalid schedule using its known revision, or stop the service and
restore a verified schedule database backup. Do not assume a reported failure
means a physical switch definitely stayed unchanged.

Shutdown waits up to ten seconds for the scheduler. If a custom device adapter
remains blocked, the database stays owned by that worker until it returns. Iris
doesn't close the database beneath active work. The supplied Matter subprocess
already has its own eight-second timeout. A scheduler error is retried after a
bounded wait, and the service's health result keeps the failure visible.

## Memory performance and what stays serial

Semantic retrieval still uses the same newest-4,096-candidate policy, filters,
scoring, tie order and source verification. The query now selects bounded
candidate IDs before fetching vector and text payloads. That avoids sorting large
payloads for the whole archive. It adds no schema migration, cache or new index,
and it doesn't make forgotten content available again.

On one local synthetic run with 10,000 exchanges, 20,000 chunks and
384-dimensional vectors, median undated semantic retrieval fell from about
247 ms to 74 ms. This excludes real embedding inference and isn't a promise about
PC or Pi latency. The benchmark records query plans and allocation peaks so the
result can be checked on another machine.

A separate batched-scoring experiment improved the median only slightly and
needed more memory, so it wasn't retained. The existing scoring code remains.
The older-memory horizon is still explicit: lexical search and narrower date
windows can find material outside an undated semantic search's candidate window.
Original exchanges are never pruned to fit that window.

Summaries and indexing remain serial. A summary can provide evidence used in the
current reply, and indexing can make a source available to that reply's search.
Moving either behind generation would change which evidence the model sees.
There is no new background model worker competing with interactive inference.
Safe deferral needs a separately defined evidence boundary and real model-resource
measurements before it can preserve the current contract.

## Reproducing measurements

Run these from a development checkout with the development dependencies installed:

```sh
python scripts/benchmark_memory.py --exchanges 10000 --dimensions 384 --repetitions 20
python scripts/benchmark_resilience.py
```

Both create disposable synthetic databases. Neither opens your archive, loads a
real model, switches a real device or sends a request to an external service.
The resilience benchmark includes the existing application orchestration harness,
a fake 100 ms device call and HTTP client-construction overhead using a mock
transport. That transport result excludes TCP and TLS handshakes; a persistent
client doesn't imply the remote server supports persistent connections.

Run timing comparisons without the test suite competing for CPU. Keep the
resulting JSON alongside the software version, Python/SQLite versions and machine
details. The tests check ordering and fault recovery; timings are measurements,
not machine-specific CI pass/fail thresholds.
