# Behaviour to preserve when changing Iris

This is a reference for refactoring Iris without changing how people use it.
It records the current boundaries, including a few quirks that are easy to lose
when tidying up. It isn't a promise that every possible client implements every
robot feature.

## Clients and capabilities

| Feature | Python text client | Robot voice client | Browser chat |
| --- | --- | --- | --- |
| Ordinary conversation and exact server commands | Yes; no microphone or reply playback | Yes, after transcription | Typed text or microphone over trusted HTTPS |
| Reply audio | Silent | Buffered WAV, or negotiated Pocket streaming | Buffered WAV for microphone turns |
| Wake phrase and wake conversation timeout | No | Optional local Vosk detection | No |
| Voice cancellation | No | Optional local “cancel cancel”, during provisional processing | No |
| Companion on/off commands | Yes | Yes | Server accepts the commands |
| Spontaneous companion turns | Client idle timer | Client idle timer in each activation mode | No idle scheduler |
| Speaker volume commands | Reports the configured client mixer result | Optional ALSA mixer and read-back | No negotiated robot mixer |
| Personalities | Client JSON catalogue | Same catalogue; local display can select it | Tab-local selection; server catalogue supplies prompts |
| Face display | Optional client display | Optional native display or local browser preview | Face in the chat page |
| Conversation memory/private mode | Server policy | Same server policy | Same server policy |

The server stores one active conversation per configured owner. Different clients
for that owner deliberately share saved history. Their live sessions, companion
settings and client personalities are separate. Without saving, or in private
mode, live history belongs to the session. Server turn processing is serial;
cancellation decisions can arrive independently while it is busy.

## Exact controls and their boundaries

| Control | Behaviour and conditions | Main regression references |
| --- | --- | --- |
| Clear context | Starts fresh working context. With memory enabled it rotates the owner's saved conversation and retains the earlier sources. Companion mode remains enabled if it was enabled. | `test_memory.py`, `test_companion.py`, `test_compatibility.py` |
| Delete previous | Immediately removes the most recent completed exchange in the active saved conversation, or the issuing session's live history when saving is unavailable. It is not a request to delete a whole conversation. | `test_memory.py`, `test_memory_hierarchy.py`, `test_voice_cancel.py` |
| Stop listening | Ends an active wake conversation or switches off companion mode. Outside those conditions the words are ordinary chat. It does not clear history. | `test_wake_conversation.py`, `test_companion.py` |
| Exit | Ends the client conversation/session; it does not terminate the shared server. | `test_droid.py`, `test_memory.py` |
| Companion mode on/off | Changes live session mode. Automatic openers are labelled automatic events, cannot execute tools, and count as complete pairs when trimming context. | `test_companion.py` |
| Private mode on/off | Changes owner policy and resets working state. Private turns neither read saved conversation evidence nor append to the archive. Leaving private mode discards its live context. | `test_memory_controls.py`, `test_memory_completion.py` |
| Forget conversation controls | Request, then explicitly confirm an unchanged target within two minutes. Recorded dependent conversations can be included in the described scope. | `test_memory_controls.py`, `test_memory_completion.py` |
| Remember/correct/forget detail | Labelled commands require confirmation. A model reply alone cannot make an explicit detail change. Natural, source-backed fact corrections are a separate memory feature. | `test_memory_completion.py`, `test_memory_hierarchy.py` |
| Recall and selection | Topic/date recall may ask which discussion was intended. Selection, cancellation, expiry and a new topic must not silently reuse the wrong discussion. | `test_memory_recall.py` |
| Volume commands | Recognise whole utterances, validate the echoed command on the client, act locally, and report actual read-back. No assumption of success. | `test_volume.py` |
| Personality commands | List configured names or switch on a completed acknowledgement. The client sends the selected prompt on each turn; clear context does not reset it. | `test_companion.py`, `test_face.py` |

`tests/fixtures/command_contract.json` gives concrete positive and negative phrase
examples. It is a characterization corpus, not a replacement production parser.
The existing recognisers intentionally differ in some punctuation and wording
rules; don't broaden or narrow them as a side effect of combining helpers.
Menu examples remain in `iris_droid/web/commands.json`, shared by Pi and web.

## Cancellation, audio and failures

- A cancellable voice turn remains provisional until the client accepts it.
  Successful discard must prevent archiving, playback and unaccepted tool effects.
  Recognition already in progress may finish later, but its result is discarded.
- The client does not automatically resend a failed turn. A request that changed
  a device may have succeeded even if its acknowledgement was lost.
- Text mode is silent. `--no-sounds` disables ordinary client feedback and the
  working loop; cancellation confirmation deliberately still plays. Spoken
  replies, server-supplied command audio and diagnostic echo are separate.
- Capture and playback remain half-duplex. Keep cue ordering, overflow rejection,
  wake calibration reuse, speech lead-in and the optional primer intact.
- Buffered synthesis failure must not apply a memory-control change. Streamed
  confirmation-before-commit is a known defect, tested as a desired outcome rather
  than preserved as a compatibility promise.
- Tool effects and delivered audio cannot be rolled back by restoring a Python
  history list. Tests must distinguish those effects from reversible live state.

## Memory, privacy and persistence

Keep full original completed, non-private exchanges indefinitely. Summaries,
embeddings and prompts are derived views; their limits must not trim the source
archive. Confirmed forgetting and private-mode exclusions take precedence.
History length zero disables prompt history, not source archiving when saving
is enabled. Restored history contains final text pairs, not executable old tool
calls or hidden model reasoning.

Preserve source ownership, policy epochs, revision checks and atomic writes.
Deleting or correcting data must not allow in-flight work to recreate stale
evidence. Clear context, forgetting and dependent-copy deletion have different
meanings. Independent backups and exports remain independent copies.

## Configuration and installation

Keep current command names, flags, JSON keys, environment precedence and default
values stable during structural changes. Secrets remain excluded from saved
server configuration. Startup must not silently download model weights.

Client configuration is resolved from its working directory or explicit
`--config`; CLI values override saved values. Server configuration currently has
some module-directory versus working-directory inconsistencies. That is a known
defect to resolve explicitly, not an invitation to move files during a refactor.
The wheel currently omits the client wizard; its installed-artifact regression
records the intended availability without hiding that defect.

Use [the testing guide](testing.md) for commands, expected failures and the limits
of automated coverage. For a deliberate behaviour change, update this reference,
its tests and the relevant user/menu documentation together.
