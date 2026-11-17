# Interfaces, capabilities and data leaving the machine

This is the contract map for a client or integration author. For the concepts
behind it, read [the technical guide](technical-guide.md). Values and defaults are
listed in [the configuration reference](configuration-reference.md).

## Iris HTTP interface

The server is HTTP/1.x and binds to `127.0.0.1:8765` by default. Use a trusted LAN
or a TLS reverse proxy for other machines. API routes require
`Authorization: Bearer KEY` with `IRIS_DROID_TOKEN`. Web assets are intentionally
public when `--web-chat` is enabled; that does not make conversation routes public.
Custom web personalities require authentication before their catalog is returned.

Turn requests require a valid UUID in `X-Iris-Session`, an explicit positive
`Content-Length` of at most 10,000,000 bytes, and the route's content type.
Chunked transfer encoding is not accepted. Text is UTF-8. Session identity is
not a multi-user authorization boundary: one server's memory owner is shared.

| Method and route | Input | Result and owner |
| --- | --- | --- |
| `GET /v1/health` | Bearer key | Application/queue/speech readiness; optional updater instance ID. No model inference. |
| `POST /v1/text` | `text/plain`, session UUID, text body | JSON `TurnReply`, with empty audio for text. |
| `POST /v1/voice` | `audio/wav`, session UUID | Buffered JSON/base64 WAV, or negotiated Pocket stream. |
| `POST /v1/voice/cancellable` | WAV, session UUID and turn UUID | NDJSON provisional processing/decision/result stream. |
| `POST /v1/voice/decision` | Same session/turn UUIDs, six bytes `accept` or `cancel` | Decision acknowledgement; no ordinary turn admission. |
| `POST /v1/volume-result` | JSON `VolumeResult`, session UUID | Spoken or text acknowledgement of the client's mixer result. |
| `GET /v1/web-personalities` | Web enabled; key for custom catalogs | JSON array of browser personality names. |
| `GET /chat` and its allowlisted assets | Web enabled | HTML (with configured time limits), JS, styles and shared menu data; no directory browsing. |

`TurnReply` contains `heard`, `text`, `audio`, `finished`, `companion_mode`, and
optional `volume`, `expression`, `selected_personality`. Audio is a base64 mono
PCM WAV at the synthesiser's sample rate. Reply text and the plain speech version
are distinct: formatting cleanup is not a rewrite of the archived source answer.
The authoritative definitions are [reply models](../iris_domain/replies.py),
[volume models](../iris_domain/volume.py) and [the HTTP adapter](../iris_droid/server.py).

| Header | Meaning |
| --- | --- |
| `X-Iris-Completion: 1` | Request authoritative final outcome metadata; require it if the server acknowledges negotiation. |
| `Accept: application/x-iris-voice+ndjson` | Allow streamed Pocket synthesis; buffered replies remain valid. |
| `X-Iris-Turn` | UUID identifying a cancellable voice turn within the session. |
| `X-Iris-Companion-Idle: 1` | Automatic opener; sends text rather than a recording and cannot use cancellable voice. |
| `X-Iris-Wake-Conversation: 1` | Enable wake-conversation control semantics. |
| `X-Iris-Volume: 1` | Client can apply a supported mixer command and report its result. |
| `X-Iris-Face: 1` | Client understands expression metadata. |
| `X-Iris-Personality` | Validated ASCII-encoded JSON catalog/active prompt, up to 7,800 header characters. |
| `X-Iris-Web-Personality` | Browser's configured personality name, resolved on the server. |
| `X-Iris-Text-Only: 1` | Request text-only acknowledgement on the volume-result route. |
| `X-Iris-Voice-Timing: 1` | Request diagnostic synthesis timing metadata. |

Buffered completion uses `X-Iris-Private`, `X-Iris-Exchange` and
`X-Iris-Deleted-Exchange` where applicable. Streaming completion uses a final
`completion` object with `outcome`, `private` and nullable exchange IDs. “Committed”
means the applicable server work finished, including private/no-archive turns;
it is not proof of physical delivery or an external action's final state.

NDJSON means one JSON object per newline. A normal stream has one `start`, audio
frames with base64 `pcm`, and one `done`; failure is `error`. A cancellable stream
begins with `processing`, can emit `ready` when acceptance is needed, and terminates
with `cancelled`, `failed`, a buffered `result`, or the accepted start/audio/done
sequence. Each frame can arrive across multiple network reads. Missing terminals,
repeated starts, malformed PCM and audio after completion must fail. See
[completion compatibility](runtime-contracts.md#streaming-completion-and-older-clients)
and [client parser](../iris_droid/client.py); do not infer success from HTTP 200
once a streaming response has already started.

Common errors are 400 (invalid input/session), 401 (key), 404 (route), 408 (upload
timeout), 413 (body limit), 415 (content type), 422 (no recognised speech), 502
(model failure), 503 (admission/service/storage/policy failure), and 504 (turn
deadline). A cancellation decision that is no longer applicable can conflict.
Do not retry effects based only on one status code; an accepted external action
may have succeeded before a later failure.

## Home service interface

All operations are authenticated **POST** requests with JSON, even read-only ones.
The correct listing/health path is `/v1/list`, not `/v1/devices`. The key is
`IRIS_HOME_TOKEN` on the Pi and `IRIS_HOME_SERVICE_TOKEN` on the PC. LAN binds require
TLS; the PC verifies the configured certificate with `home_service_ca`.
Bodies are 1–16,384 bytes with an explicit length and no transfer encoding.

| Route | Body | Result |
| --- | --- | --- |
| `/v1/list` | `{}` | Configured devices/meters and scheduler health. |
| `/v1/status` | `{"device":"lamp"}` | Read-back switch state. |
| `/v1/switch` | `{"device":"lamp","state":"on"}` | Accepted requested state; read back separately if needed. |
| `/v1/meter` | `{"device":"lamp","metric":"power"}` | Supported power/energy/voltage/current value and unit. |
| `/v1/schedule` | `{"device":"lamp"}` | Current revision and weekly rules. |
| `/v1/save-schedule` | Device, expected revision and complete `rules` list | New revision, or conflict if another edit won. |

A rule contains `days` (`mon`…`sun`), local `time` (`HH:MM`) and `state` (`on`/`off`).
At most 32 rules are allowed; a weekday/time slot cannot be duplicated. Saving an
empty list removes the schedule. Send the revision returned by the read; do not
invent it. The schema is [protocol.py](../iris_home/protocol.py). Responses include
401 authentication, 400 validation, 404 unknown operation/device, 409 revision
conflict and 503 device/database failure.

The face controller's `127.0.0.1:8767` interface is local display plumbing,
not another authenticated LAN conversation service. Do not expose it through a
public proxy. [Face display](face-display.md) covers native and browser clients.

## Commands and capability ownership

| Command family | Guide | Implementation/test boundary |
| --- | --- | --- |
| Server/configure/check/download | [Server setup](server-setup.md) | `iris.py`, `configure.py`, settings and startup tests |
| Client/configure-client and capture diagnostics | [Client setup](client-setup.md) | `client_settings`, capture/playback tests |
| Exact voice/text controls | [Shared menu](../iris_droid/web/commands.json) | Domain grammar, command router and compatibility corpus |
| Recall/detail/privacy/forgetting | [Memory](memory-and-privacy.md) | Memory controls, provenance and policy tests |
| Memory inspect/export/retry/rebuild/index/delete | [Storage](memory-storage.md) | `iris_memory.cli`, migrations and recovery tests |
| Voice download/prepare; Kokoro voice listing | [Voice preparation](server-setup.md#optional-cpu-voice-cloning-with-pocket-tts) | `voice_cli`, worker framing and preparation tests |
| Notification test sender | [Notifications](server-setup.md#push-notifications-with-pushover) | `iris_tools.notify`, validation/cooldown tests |
| Face console and native boot unit | [Display](face-display.md) | Console/menu/SDL adapters, browser checks and real panel acceptance |
| Git updater and health probes | [Updates](updates.md) | Disposable Git repos, process mocks, real local HTTP and platform acceptance |

The Python client supports local wake/cancellation and an optional ALSA mixer.
The browser supports microphone segmentation, personalities and face/menu state,
but does not thereby gain the Pi's Vosk cancel listener or ALSA hardware. Siri is
a text integration with phone speech. Home menu examples are model/tool requests;
they are not extra exact-command parser rules. Capability notes matter when
copying a phrase from one client to another.

## Where data can go

| Path | Data sent | What enables it |
| --- | --- | --- |
| Client → Iris | Recorded speech or typed words, session/capability metadata | The chosen client/server connection. |
| Iris → chat runtime | Prompts, eligible memory, tool results and conversation | `base_url`; non-loopback needs `allow_remote`. |
| Iris → Google STT | Captured speech | Explicit Google recogniser and remote opt-in. |
| Iris → Open-Meteo | Configured location and forecast parameters | Weather opt-in; no transcript/model credential is sent. |
| Iris → Pushover | Requested notification content and account/device credentials | Notification opt-in plus an explicit request. |
| Iris → home service | Named operations, state, meter or schedule arguments | Configured URL/key and tool permissions. |
| Setup → model/package providers | Requested assets and applicable download credentials | Explicit installation/download commands. |
| Phone → Apple services | Siri/dictation behaviour depends on phone settings | Phone feature, outside Iris's model execution. |

A localhost URL describes the immediate endpoint, not whether that endpoint
forwards elsewhere. Raw traces, terminal scrollback, browser key storage, diagnostic
WAVs and backups have their own retention. See [diagnostics](performance-and-recovery.md#what-to-include-in-a-bug-report)
and [privacy](memory-and-privacy.md#private-conversations-and-forgetting).
