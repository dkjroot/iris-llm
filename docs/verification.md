# What the checks establish, and what still needs a person

A passing test is evidence for the behaviour it checks. It doesn't establish
that every microphone works, that a model understands your accent, or that a
fresh Windows installation will survive reboot. This map keeps those kinds of
evidence separate. [Testing](testing.md) gives the commands; [release acceptance](releasing.md)
is the checklist for the real installation.

## Feature and invariant map

The named tests below are starting points, not the only checks for each area.
They use synthetic conversations, temporary databases and fake physical devices.
“Loopback” means real HTTP between processes/threads on the test computer; it
does not mean traffic went to a home device or model provider.

| Feature or invariant | Documentation home | Automated evidence | Separate acceptance |
| --- | --- | --- | --- |
| Commands, defaults and compatibility | [Interfaces](interfaces.md), [compatibility](compatibility.md) | `tests/test_compatibility.py::test_exact_command_grammar`; shared menu grammar in `tests/test_documentation.py` | Read/listen to controls on Pi and phone. |
| Model requests, tool-loop correlation, provider failures | [Technical guide](technical-guide.md), [server](server-setup.md) | `tests/test_backends.py::test_an_injected_adapter_can_drive_the_entire_skill_loop_without_model_http`; `tests/test_tools.py` | Real selected GGUF/runtime, correct template, normal chat and actual tool call. |
| Remote opt-in, keys, config precedence and validation | [Settings](configuration-reference.md), [runtime contracts](runtime-contracts.md) | `tests/test_settings.py::test_environment_overrides_file_and_defaults`; `tests/test_configure.py`; `tests/test_phase1_persistence.py` | Service sees intended account/environment; no live secrets in reports. |
| Admission, uploads, FIFO, total deadlines | [Runtime contracts](runtime-contracts.md) | `tests/test_phase1_boundaries.py::test_queue_timeout_does_not_run_later_or_block_next_turn`; same file covers saturation, slow upload and decision bypass | Busy host latency; model may outlast cooperative native deadline. |
| Provisional voice acceptance and cancellation | [Client](client-setup.md), [technical guide](technical-guide.md) | `tests/test_voice_cancel.py::test_cancellation_prevents_tool_dispatch`; `tests/test_application_contracts.py::test_provisional_application_turn_restores_evicted_session_without_http` | Actual Vosk cancellation at different points; never use a hazardous action. |
| Effects, failed replies and ambiguous outcomes | [Runtime contracts](runtime-contracts.md), [extensions](extensions.md) | `tests/test_application_contracts.py::test_effect_receipt_survives_reply_failure_and_session_rollback`; `test_uncertain_effect_is_not_repeated_with_a_new_tool_call_id` in that file | Physical read-back; delivery is distinct from acceptance. |
| Buffered/streamed wire validation and completion | [Interfaces](interfaces.md) | `tests/test_phase1_boundaries.py::test_invalid_or_incomplete_stream_never_returns_success`; `test_incremental_frames_preserve_utf8_across_every_byte_boundary` | Slow LAN and phone disconnect; no assumed retry safety. |
| Recognition, resampling and empty audio | [Client](client-setup.md), [voice preparation](voice-reference.md) | `tests/test_speech.py`; `tests/test_droid.py::test_native_capture_is_converted_to_server_format` | Actual faster-whisper/wav2vec2/Google route, chosen model and language. |
| Speech workers and bounded recovery | [Performance/recovery](performance-and-recovery.md) | `tests/test_phase4_resilience.py::test_wrong_request_identity_retires_pipe_and_restart_budget_is_finite`; `tests/test_chatterbox.py`; `tests/test_pocket_contracts.py` | Real Pocket/Chatterbox environment, weights, prepared voice, audible quality. |
| Kokoro preparation and speech cleanup | [Server](server-setup.md) | `tests/test_kokoro.py::test_failed_download_preserves_previous_file_and_removes_partial`; `test_speech_cleanup_preserves_displayed_reply_and_model_history` | Real voice pack and audible onset on chosen hardware. |
| Capture, playback, overflow/device loss | [Client audio](client-setup.md) | `tests/test_droid.py::test_capture_overflow_aborts_before_any_network_turn`; `tests/test_reply_playback.py`; `tests/test_application_contracts.py::test_stalled_playback_aborts_with_a_finite_wait` | USB disconnect/reconnect, device names after reboot, no clipped words. |
| Wake, VAD, silence and companion timing | [Client](client-setup.md) | `tests/test_wake_conversation.py`; `tests/test_vad.py`; `tests/test_companion.py::test_timer_randomizes_backs_off_caps_and_resets_after_human_reply` | Accent, room noise, television, idle opener, off/sleep controls. |
| Volume and feedback | [Client](client-setup.md) | `tests/test_volume.py::test_client_never_retries_change_when_acknowledgement_fails`; `tests/test_client_feedback.py`; `tests/test_sounds.py` | ALSA mixer mapping and perceived levels; cancellation cue with no-sounds. |
| Face, touch menu and personality ordering | [Face](face-display.md) | `tests/test_face_console.py`; `tests/test_console_menu.py`; `tests/test_review_regressions.py::test_newer_touch_selection_survives_older_voice_acknowledgement` | Real KMS renderer, panel resolution/rotation, every touch target. |
| Browser chat, audio, stale responses and private display | [Phone](phone-access.md) | [Browser tests](../tests/web/test_chat.py), [speech tests](../tests/web/test_speech.py), [face tests](../tests/web/test_face.py) with `--browser` | Safari/iPhone trusted HTTPS, permission, actual audio, background/foreground. |
| Siri shortcut | [Setup F](setup-devices.md#f-build-the-siri-shortcut) | `tests/test_droid.py::test_real_http_transport_auth_validation_and_voice_turn` covers server boundary; text HTTP/restart in `tests/test_memory.py` | Build actions, invoke directly and through Siri; stable UUID, spoken reply. |
| Full original retention and context limits | [Memory/privacy](memory-and-privacy.md), [storage](memory-storage.md) | `tests/test_memory_completion.py::test_full_source_archive_survives_context_limits_summaries_rebuild_and_restart`; `tests/test_memory_context.py` | Stopped backup/export inspect using non-private acceptance data. |
| Owner-wide privacy and stale work | [Memory/privacy](memory-and-privacy.md) | `tests/test_memory_controls.py::test_private_storage_guard_and_stale_policy_commit_are_fail_closed`; `tests/test_phase3_memory.py::test_vector_inference_cannot_commit_across_policy_or_rebuild_boundary` | Private mode reflected on multiple real clients; external copies remain outside forgetting. |
| Corrections, provenance and confirmed forgetting | [Memory/privacy](memory-and-privacy.md) | `tests/test_review_regressions.py::test_correcting_one_claim_preserves_other_claims_in_the_same_exchange`; `tests/test_memory_completion.py`; `tests/test_memory_hierarchy.py` | Natural-language quality against the selected model; never infer deletion from a model sentence. |
| Migrations, corruption, WAL, restore and rebuild | [Storage](memory-storage.md) | `tests/test_phase3_memory.py::test_interruption_after_each_migration_rolls_back_entire_upgrade`; `test_sqlite_backup_recovers_committed_wal_after_unclean_exit`; `test_stopped_backup_restore_rebuild_keeps_full_sources_and_forgetting` | Private backup medium and actual recovery procedure; compatible old code if rolling back. |
| Durable maintenance, larger/older archives | [Storage](memory-storage.md), [performance](performance-and-recovery.md) | `tests/test_phase1_persistence.py::test_thousand_conversation_backlog_survives_old_queue_cap_restart_failure_and_rebuild`; `tests/test_phase3_memory.py::test_large_archive_old_topic_remains_lexically_recallable_beyond_vector_horizon` | Representative latency/resources on host; originals are not pruned. |
| Lexical/semantic recall and date interpretation | [Memory/privacy](memory-and-privacy.md) | `tests/test_memory_recall.py`; `tests/test_semantic_memory.py`; two explicit prepared-model tests described below | Real pinned embedding model and representative synthetic retrieval corpus. |
| Matter transport, meters and home HTTP/TLS | [Home](home-automation.md), [controller](matter-controller.md) | `tests/test_home.py::test_conversation_tools_reach_authenticated_home_service`; `tests/test_home_contracts.py::test_home_tls_checks_trust_hostname_and_key_then_recovers` plus malformed-request checks | Actual commissioning, units, firmware and physical switch/read-back. |
| Schedule conflicts, DST, outages, stale manual work | [Home](home-automation.md) | `tests/test_review_regressions.py::test_weekly_recovery_crosses_a_skipped_dst_occurrence`; `tests/test_phase4_resilience.py::test_manual_command_invalidates_an_already_planned_schedule_action` | Clock synchronization, near-future schedule, restart/catch-up on harmless device. |
| Weather and notifications | [Server](server-setup.md) | `tests/test_tools.py::test_weather_errors_are_data_for_model_without_fake_forecasts_or_private_details`; `tests/test_notifications.py` | Optional live provider response and one deliberately requested notification. |
| Shutdown and shared resource ownership | [Architecture](architecture.md), [technical guide](technical-guide.md) | `tests/test_application_contracts.py::test_shutdown_defers_cleanup_and_stops_work_after_native_returns`; `tests/test_phase4_resilience.py::test_scheduler_shutdown_does_not_close_database_beneath_blocked_adapter` | Actual OS signals/process tree, native model exit, clean reboot. |
| Updates, interrupted installs and readiness | [Updates](updates.md) | `tests/test_autoupdate.py`; `tests/test_updater_readiness.py::test_documented_home_probe_reaches_real_service`; actual disposable Git repositories | Windows watcher/process tree and Pi systemd lifecycle; never delete a pending marker blindly. |
| Package contents and installation profiles | [Profiles](install-profiles.md), [testing](testing.md) | [Wheel-from-sdist tests](../tests/artifacts/test_wheel.py); [fresh profile checker](../scripts/check_clean_install.py) | Each OS/Python/native-library route and separate speech environment. |
| Documentation examples and declarations | [Extensions](extensions.md), [settings](configuration-reference.md) | `tests/test_documentation.py`: local links, generated settings parity, executable recipes, actual CLI flags and referenced test identities | New reader follows each route from empty host/Pi, without author help. |

## Every setting has a verification route

[The generated reference](configuration-reference.md) enumerates every declared
server field, saved client field, command-only client switch and home field.
`test_configuration_reference_matches_declared_settings` fails on drift, and its
generator refuses an unexplained server field. This establishes inventory and
defaults, **not** that every possible setting combination works.

For any server field, first run `tests/test_settings.py`, `tests/test_configure.py`
and the documentation parity test. For any saved client field, run
`tests/test_client_config.py` and
`tests/test_application_contracts.py::test_client_options_preserve_parser_defaults`.
Then follow its behaviour group below. For home fields, use `tests/test_home.py`
and `tests/test_home_contracts.py`. Secret fields also need the redaction/isolation
tests and the relevant service-environment acceptance check.

| Setting family | Behaviour checks and documentation |
| --- | --- |
| Model URL/name/key/remote opt-in, prompts/style, sampling/output, template/request options, reply encoding | Backends/chat/tools tests; [server](server-setup.md). Test a real template/model separately. |
| History/context/margin/summary/retrieval budgets and all `memory_*` settings | Memory context, controls, hierarchy, completion, semantic and Phase 3 tests; [memory](memory-and-privacy.md). A schema-valid budget may still be too small for a particular prompt. |
| HTTP timeout, turn/upload/queue budgets, pending slots, trace | Phase 1 boundaries, Phase 4 diagnostics, server HTTP tests; [runtime contracts](runtime-contracts.md). |
| STT model/device/compute/language, TTS engine/voice/speed/model paths/interpreter, phrase limit | Speech/Kokoro/Chatterbox/Pocket tests and capture tests; [profiles](install-profiles.md), [voice](voice-reference.md). Real optional libraries/weights require separate checks. |
| Tools, timezone, weather, home URL/CA/key, notification credentials/device | Tools/notifications/home/schedule tests; [home](home-automation.md), [server](server-setup.md). |
| Client server/key, text/diagnostic modes, request timeout | Client configuration, droid HTTP and local-test tests; [client](client-setup.md). |
| Client microphone/rate/buffer/threshold/pause/calibration and VAD settings | Droid capture and VAD tests; [client audio](client-setup.md#4-set-up-listening-and-audio-devices). |
| Client speaker/volume, lead-in/primer, feedback, echo/save/stream/timing controls | Playback, volume, sounds, feedback and privacy tests; [client](client-setup.md). |
| Client activation/wake/cancel, conversation timeout, companion intervals | Wake conversation, voice cancellation, companion tests; [client](client-setup.md). |
| Client personalities, face/head/idle/port | Controller ordering, face/menu and browser tests; [face](face-display.md). |
| Home listener/TLS/port, timezone/clock, database, chip-tool/storage, node/endpoints | Config rejection and real home HTTP, schedule, adapter tests; [home](home-automation.md). |

CLI families outside the client (configure, model check/download, memory,
voice preparation, notification, display, updater) are mapped in
[interfaces](interfaces.md#commands-and-capability-ownership). Each setup milestone has an
expected result in the platform/shared walkthrough; record that result rather
than treating a command's zero exit code as the whole feature test.

## Coverage boundaries and gaps to keep visible

Run `python scripts/check.py --coverage --artifacts` and inspect missed **branches**
as well as lines. `tests/` is the collected root; expected failures are strict.
The default run deliberately skips browser tests unless `--browser` is set, and
wheel tests unless `--artifacts` is set. Two semantic tests need
`IRIS_TEST_EMBEDDING_MODEL_DIR`: `test_real_pinned_model_improves_synthetic_paraphrase`
and `test_real_model_server_text_recall_smoke`. They check a prepared local model;
fake vectors are not a replacement. No optional download happens just because
tests were collected. Use `pytest --collect-only -q` and `pytest -rs` when
reconciling a run's collected/skipped cases.

Python coverage includes application modules and scripts, but does not combine
the separately launched model workers or installed-wheel subprocesses. Browser
tests execute JavaScript separately; Python's percentage does not measure JS.
Ruff checks Python source across the checkout. Strict mypy covers the configured
runtime modules, not tests and developer scripts. `npm test` checks JS syntax and
face-art regeneration; it cannot establish phone audio permissions. CI defines
Windows/Linux Python 3.11–3.13 and macOS 3.12 runs, plus separate profile/browser/JS
jobs. A configured job is not a recorded successful run.

| Outstanding evidence | Why it matters | How to close it |
| --- | --- | --- |
| Fresh Windows 11, macOS Apple Silicon and Ubuntu 24.04 full walkthroughs | Package imports do not establish local inference, firewall, startup or phone trust. | Follow each route on a clean supported host; record OS/model/runtime/hardware versions and reboot results. |
| Pi image, USB audio, KMS/touch and actual wake/VAD | Fakes cannot establish driver timing, permissions or recognition quality. | Run shared A–D/I and release checklist on the actual Pi/panel. |
| iPhone Safari and Siri | Chromium with synthetic audio does not exercise iOS trust, dictation or Shortcuts. | Run E/F directly, with Siri and after host/Pi reboot. |
| Matter build/commissioning/firmware, physical scheduling and restore | Adapter tests do not perform radio discovery, attestation or electrical switching. | Run controller/G/J with a harmless device; record node/endpoints without credentials. |
| Each selected model/backend on its target platform | A real embedding test on one host does not validate speech, chat or other platforms; disabled dependencies leave genuine inference paths unexercised. | Prepare local assets explicitly, run opt-in embedding tests and real speech smoke/quality checks, recording each platform's result. |
| Native rendering and cross-platform services | Menu logic tests do not render every font/pixel or execute launchd/Task Scheduler/systemd on all hosts. | Inspect actual display and run each documented startup/stop/update path. |
| A beginner follows both documentation tracks | An author can unconsciously fill in omitted prerequisites. | Record each point where the reader needed help and repair the guide; repeat that section from a clean state. |

These are release/route gates, not silently waived tests. Keep local run logs,
exact device details and any private diagnostic material outside public docs.
The absence of another observed gap is not a proof that no bug remains.
