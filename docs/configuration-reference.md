# Settings and command-line reference

This page lists declared defaults, not values from your installation. Server
environment variables named `IRIS_<UPPERCASE_SETTING>` override the JSON file.
A missing optional value is written as `null`; it is different from an empty string.
Unknown JSON keys fail validation. Relative paths use the process's working
directory. See [configuration precedence](runtime-contracts.md#finding-and-saving-configuration)
and [the technical guide](technical-guide.md) before changing timing or memory budgets.

The tables are built from declarations with `python scripts/build_reference.py`.
The documentation test checks that they stay in step; this command never reads
your configuration, keys, models or archive.

## Server JSON and environment

The same fields are available through `python iris.py configure`. Credentials
are environment-only in the wizard and are excluded from saved JSON. Use
the validation rules together: for example, output tokens plus the context
margin and 1,024 prompt tokens must fit inside the model context window.

| Key | Default | Type and field limits | Meaning |
| --- | --- | --- | --- |
| `pocket_voice` | `null` | string; pattern="^[a-zA-Z0-9_-]{1,64}$" or null | Required prepared Pocket voice basename; null cannot start this engine. |
| `pocket_python` | `null` | string or null | Pocket TTS Python interpreter; null uses .venv-pocket |
| `pocket_model_dir` | `null` | string or null | Pocket TTS model directory; null uses .cache/pocket-tts |
| `chatterbox_device` | `"cuda"` | "cpu", "cuda" | Isolated Chatterbox execution device. |
| `chatterbox_voice` | `null` | string; pattern="^[a-zA-Z0-9_-]{1,64}$" or null | Prepared voice basename; null selects the bundled voice. |
| `chatterbox_python` | `null` | string or null | Chatterbox Python interpreter; null uses .venv-chatterbox |
| `chatterbox_model_dir` | `null` | string or null | Chatterbox Turbo model directory; null uses .cache/chatterbox-turbo |
| `base_url` | `"http://127.0.0.1:5001/v1"` | string | Model API root, including /v1; localhost unless allow_remote is enabled. |
| `model` | `"local-model"` | string | Exact model identifier expected by that API. |
| `api_key` | `null` | string or null | Set IRIS_API_KEY in the server environment; it isn't saved here. |
| `allow_remote` | `false` | boolean | Permit a non-loopback chat endpoint or Google recognition; not client access. |
| `system_prompt` | `"You are Iris, a friendly assistant. Keep spoken replies fairly short."` | string | Iris's instructions and personality |
| `conversation_style_prompt` | `"Only ask a follow-up question when you need more information to respond. When your reply is complete, stop there."` | string | Character's conversation style; enter "" to remove the nudge |
| `temperature` | `0.7` | number; minimum=0; maximum=2 | Reply randomness, from 0 to 2 |
| `max_tokens` | `250` | integer; maximum=32768; exclusiveMinimum=0 | Maximum tokens in a reply |
| `model_context_tokens` | `8192` | integer; minimum=2048; maximum=2000000 | Model context window; match the allocation in the model host. |
| `context_margin_tokens` | `512` | integer; minimum=128; maximum=32768 | Context space reserved for the host's formatting |
| `memory_summary_tokens` | `1200` | integer; minimum=256; maximum=8192 | Maximum tokens in a memory summary |
| `memory_summary_interval` | `8` | integer; minimum=1; maximum=100 | Unsummarised exchanges before a checkpoint |
| `memory_summary_chars` | `3000` | integer; minimum=0; maximum=16000 | Maximum summary characters in chat context |
| `memory_retrieval_chars` | `3000` | integer; minimum=0; maximum=16000 | Maximum retrieved source characters in chat context |
| `history_turns` | `12` | integer; minimum=0; maximum=100 | Recent whole turns retained in context; 0 disables prompt history |
| `memory_enabled` | `false` | boolean | Store completed non-private source exchanges; no automatic expiry. |
| `memory_database` | `null` | string or null | Archive path; null uses the OS user-data location. |
| `memory_companion_followups` | `false` | boolean | Allow eligible archived topics in companion openers. |
| `memory_search_tool` | `true` | boolean | Expose read-only archive search when memory and tools permit it. |
| `memory_hierarchy_enabled` | `true` | boolean | Interpret facts, corrections and topic attention with a bounded metadata model pass |
| `memory_extraction_model` | `null` | string or null | Optional model name for background memory summaries and hierarchy extraction; null uses model |
| `memory_semantic_enabled` | `false` | boolean | Enable prepared embedding retrieval; requires memory_enabled. |
| `memory_embedding_model_dir` | `null` | string or null | Prepared semantic model directory; null uses the default |
| `memory_semantic_min_score` | `0.45` | number; minimum=0; maximum=1 | Minimum semantic similarity, from 0 to 1 |
| `memory_owner` | `"default"` | string; minLength=1; maxLength=100; pattern="^\\S+$" | Shared archive/privacy identity for this server, not a client UUID. |
| `memory_context_chars` | `24000` | integer; minimum=0; maximum=200000 | Maximum restored conversation characters |
| `timeout` | `120` | number; maximum=600; exclusiveMinimum=0 | Model request timeout in seconds |
| `turn_timeout` | `600` | number; maximum=3600; exclusiveMinimum=0 | Cooperative total turn budget, including upload and admission, in seconds. |
| `upload_timeout` | `15` | number; maximum=120; exclusiveMinimum=0 | Total admitted body-upload allowance in seconds. |
| `queue_timeout` | `30` | number; maximum=600; exclusiveMinimum=0 | Maximum wait for the serial brain after upload, in seconds. |
| `max_pending_turns` | `8` | integer; minimum=1; maximum=32 | Admission slots including uploads, queued turns and the active turn. |
| `trace` | `false` | boolean | Print model/tool diagnostics, including conversation text |
| `reply_encoding` | `"text"` | "text", "html", "double-html" | Reply text encoding: text, html or double-html |
| `tools_enabled` | `true` | boolean | Expose configured skills to a model that supports tools. |
| `chat_template_kwargs` | `null` | object | Host template options as a JSON object |
| `chat_request_options` | `null` | object | Additional model request options as a JSON object |
| `local_timezone` | `"Europe/London"` | string | IANA timezone for clock, memory dates and conversation context. |
| `weather_enabled` | `false` | boolean | Opt in to Open-Meteo requests independently of local model traffic. |
| `weather_location` | `"Stroud"` | string | Town sent to the weather geocoder. |
| `weather_country` | `"GB"` | string | Two-letter country used to disambiguate that town. |
| `home_service_url` | `null` | string or null | URL of the standalone home automation service |
| `home_service_ca` | `null` | string or null | PEM certificate to trust for the home automation service |
| `home_service_token` | `null` | string or null | Set IRIS_HOME_SERVICE_TOKEN in the server environment; it isn't saved here. |
| `notifications_enabled` | `false` | boolean | Opt in to explicitly requested online Pushover notifications. |
| `pushover_api_token` | `null` | string or null | Set IRIS_PUSHOVER_API_TOKEN in the server environment; it isn't saved here. |
| `pushover_user_key` | `null` | string or null | Set IRIS_PUSHOVER_USER_KEY in the server environment; it isn't saved here. |
| `pushover_device` | `""` | string; maxLength=25; pattern="^[A-Za-z0-9_-]*$" | Optional Pushover destination; empty uses account defaults. |
| `stt_engine` | `"whisper"` | "whisper", "wav2vec2", "google" | Speech recogniser: local whisper/wav2vec2 or explicitly online google. |
| `whisper_model` | `"base.en"` | string | Whisper model name or local path |
| `whisper_vad_filter` | `true` | boolean | Filter silence in Whisper STT; disable if client VAD is already active |
| `whisper_beam_size` | `5` | integer; minimum=1; maximum=10 | Whisper beam search width (1 is fastest greedy decoding, 5 is default) |
| `device` | `"cpu"` | "cpu", "cuda", "auto" | Speech recognition device: cpu, cuda or auto |
| `compute_type` | `"int8"` | string | Whisper compute type, such as int8 or float16 |
| `language` | `"en"` | string or null | Recognition language code; null enables automatic detection |
| `tokenizer_model` | `"jonatasgrosman/wav2vec2-large-xlsr-53-english"` | string | Wav2vec2 tokenizer model name or path |
| `ctc_model` | `"jonatasgrosman/wav2vec2-large-xlsr-53-english"` | string | Wav2vec2 speech model name or path |
| `tts_engine` | `"kokoro"` | "kokoro", "chatterbox", "pocket" | Local speech engine: kokoro, chatterbox or pocket. |
| `kokoro_voice` | `"af_heart"` | string; pattern="^[ab][fm]_[a-z]+$" | Voice identifier from the installed Kokoro pack. |
| `kokoro_speed` | `1.0` | number; minimum=0.5; maximum=2.0 | Speech speed multiplier, not model temperature. |
| `kokoro_model_dir` | `null` | string or null | Kokoro model directory; null uses the default |
| `phrase_time_limit` | `60` | number; maximum=300; exclusiveMinimum=0 | Maximum accepted recording length in seconds; match the client too |

## Client file and command line

Saved keys use underscores; command-line flags use hyphens. Explicit flags
override `client_config.json`. The connection key is `IRIS_DROID_TOKEN`, not
a saved client setting. These defaults do not select your actual audio devices.

| Saved key | Flag | Default | Choices or explanation |
| --- | --- | --- | --- |
| `server` | `--server` | `"http://127.0.0.1:8765"` | Iris server URL (default: this machine) |
| `personalities_file` | `--personalities-file` | `null` | JSON file with this client's personality prompts |
| `face` | `--face` | `null` | Serve a face for the droid's display Choices: ["humanoid-android", "sprout", "signal", "rivet"]. |
| `face_idle` | `--face-idle` | `"snow"` | Idle display. Off puts the native Pi display to sleep (default: snow) Choices: ["snow", "black", "off"]. |
| `face_port` | `--face-port` | `8767` | Loopback port for the face display (default: 8767) |
| `volume_backend` | `--volume-backend` | `"none"` | Client speaker mixer backend (default: disabled) Choices: ["none", "alsa"]. |
| `alsa_card` | `--alsa-card` | `null` | ALSA card number or ID; separate from --speaker |
| `alsa_control` | `--alsa-control` | `"Master"` | ALSA playback mixer control |
| `no_sounds` | `--no-sounds` | `false` | Disable optional cues and busy sound (voice-cancel acknowledgement, spoken replies and recording echo still play) |
| `echo_recording` | `--echo-recording` | `false` | Play each captured recording locally before sending it (voice mode only) |
| `activation` | `--activation` | `"enter"` | activation Choices: ["enter", "continuous", "wake"]. |
| `wake_model` | `--wake-model` | `null` | wake model |
| `wake_phrase` | `--wake-phrase` | `"hey iris"` | wake phrase |
| `voice_cancel` | `--voice-cancel` | `false` | Listen locally for "cancel, cancel" during processing (requires --wake-model) |
| `vad_model` | `--vad-model` | `null` | Enable local Silero speech detection |
| `vad_threshold` | `--vad-threshold` | `0.5` | Speech probability needed to start a turn (0–1 exclusive; default: 0.5) |
| `conversation_timeout` | `--conversation-timeout` | `300` | Wake conversation idle timeout in seconds (0 < value <= 3600; default: 300) |
| `microphone` | `--microphone` | `null` | PyAudio input index or name |
| `microphone_rate` | `--microphone-rate` | `null` | Capture rate in Hz (8000–192000; default: device rate). Must be supported by input. |
| `energy_threshold` | `--energy-threshold` | `null` | Fixed speech energy threshold (0–32768, exclusive of 0); skips ambient calibration |
| `calibration_duration` | `--calibration-duration` | `0.6` | Ambient calibration in seconds (0.1–10; default: 0.6, at least one input buffer) |
| `speaker` | `--speaker` | `null` | sounddevice output index or name |
| `speech_lead_in` | `--speech-lead-in` | `0.3` | Silence prepended in the reply playback stream (0–2 seconds; default: 0.3) |
| `speech_primer` | `--speech-primer` | `false` | Diagnostic: prepend a soft chirp in the same stream as spoken replies |
| `trace_voice` | `--trace-voice` | `false` | Report reply word count and timing from synthesis to speaker output |
| `no_speech_stream` | `--no-speech-stream` | `false` | Wait for the complete reply audio before playing it |
| `microphone_buffer` | `--microphone-buffer` | `8192` | Capture buffer in frames (256–65536; default: 8192). Increase for input overflow. |
| `phrase_time_limit` | `--phrase-time-limit` | `60` | phrase time limit |
| `listen_timeout` | `--listen-timeout` | `10` | listen timeout |
| `companion_idle_min` | `--companion-idle-min` | `90` | Shortest companion silence before an opener, in seconds (default: 90) |
| `companion_idle_max` | `--companion-idle-max` | `240` | Longest initial companion silence, in seconds (default: 240; backs off if ignored) |
| `pause_threshold` | `--pause-threshold` | `1.2` | Seconds of quiet that end a recording (0.5–10; default: 1.2) |

These switches are command-only and are not saved in the client file:

| Flag | Meaning |
| --- | --- |
| `-h / --help` | show this help message and exit |
| `--config` | Client settings file (default: ./client_config.json) |
| `--list-microphones` | List microphone indices |
| `--text` | Test transport without any audio |
| `--download-vad-model` | Download the pinned Silero model here and exit (no microphone or server) |
| `--test-microphone` | Record and play one local test; no server, key, wake model or saved files needed |
| `--save-reply` | Diagnostic: save the unmodified reply WAV here, replacing it each voice turn |
| `--play-wav` | Play a local WAV with reply playback settings, without server or microphone |
| `--show-private-text` | Display private conversation text in this terminal (may be logged) |

## Home service JSON

Home configuration is selected with `python -m iris_home.service --config PATH`.
It does not use the server's `IRIS_<SETTING>` overrides. `IRIS_HOME_TOKEN` is
its bearer key; the PC uses the same value as `IRIS_HOME_SERVICE_TOKEN`.
LAN listening requires both TLS paths. Device names and switch addresses must
be unique. See [home setup](home-automation.md) for pairing and an explained example.

### Service fields

| Key | Default | Type and limits |
| --- | --- | --- |
| `listen` | `"127.0.0.1"` | string |
| `port` | `8766` | integer; minimum=1; maximum=65535 |
| `tls_cert` | `null` | string or null |
| `tls_key` | `null` | string or null |
| `timezone` | `"Europe/London"` | string |
| `database` | `required` | string |
| `clock_sync_file` | `null` | string or null |
| `chip_tool` | `"chip-tool"` | string |
| `chip_tool_interactive` | `false` | boolean |
| `matter_storage` | `required` | string |
| `devices` | `required` | object |

### Each devices entry

| Key | Default | Type and limits |
| --- | --- | --- |
| `node` | `required` | integer; minimum=1; maximum=18446744004990074879 |
| `switch_endpoint` | `1` | integer; minimum=1; maximum=65534 |
| `power_endpoint` | `null` | integer; minimum=1; maximum=65534 or null |
| `energy_endpoint` | `null` | integer; minimum=1; maximum=65534 or null |
| `protocol` | `"matter"` | string |

## Other process settings

| Name | Where and why |
| --- | --- |
| `IRIS_DROID_TOKEN` | Shared bearer key on server, client, phone and Windows updater. Short keys warn but remain accepted for compatibility. |
| `IRIS_HOME_TOKEN` | Home-service key: at least 32 ASCII letters/digits. |
| `IRIS_PERFORMANCE_LOG=1` | Text-free stage timing on the process where it is set. |
| `IRIS_INSTANCE_ID` | Opaque 32-hex Windows-updater identity; inherited by the launched server and echoed in authenticated health. |
| `LOCALAPPDATA`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | OS-dependent user data/state roots; see the path reference below. |
| `IRIS_TEST_EMBEDDING_MODEL_DIR`, `IRIS_TEST_BROWSER_CHANNEL`, `PLAYWRIGHT_BROWSERS_PATH` | Test-only opt-ins, described in the testing guide. |

Server listening and web switches are launch options, not server JSON keys:
`--config`, `--host` (127.0.0.1), `--port` (8765), `--web-chat`, and
`--web-personalities-file`. Use `python iris.py server --help` for their descriptions.
The updater, memory, voice, notification and display commands have their own
`--help`; [interface reference](interfaces.md) maps each to its guide and tests.

Use [runtime paths](runtime-contracts.md#prepared-speech-assets-outside-a-checkout),
[memory storage](memory-storage.md) and [installation profiles](install-profiles.md)
for asset locations and external model/runtime variables. Iris does not treat a
`.env` file as an automatically loaded secret store.
