# Request limits, configuration and upgrades

These details matter when you update an existing installation, write a client,
or run Iris from somewhere other than its checkout. The normal commands and
successful conversations work as before. Failed or overloaded requests now have
clearer boundaries, and the configuration tools agree about which file to use.

## Finding and saving configuration

Server setup, diagnostics, the server itself, memory maintenance and voice
preparation use the same order:

1. An explicit `--config PATH` selects that file. A reader reports an error if it
   doesn't exist; a configuration wizard can create it.
2. Otherwise, use `local-config.json` in the current working directory.
3. If that file is absent and Iris is running from a source checkout containing
   an existing `local-config.json`, use that file with a warning naming its path.
   This keeps existing checkout installations usable from another directory.
4. With neither file present, readers use defaults and environment settings.
   The server wizard creates `local-config.json` in the working directory.

The client continues to use `client_config.json` in its working directory, or
the explicit client `--config` path. Command-line client options override its file.
Server environment settings still override the selected server file. Relative
paths inside either file remain relative to the process's working directory;
they have not silently become relative to the configuration file. Use absolute
paths in service configurations when you want to avoid that dependency.

Both wizards write a complete temporary file beside the destination, flush it,
then replace the destination atomically. A failed serialization, temporary-file
write or replacement leaves the previous valid file intact. Server secrets stay
out of the saved JSON. No existing configuration is automatically moved.

The installed commands are `iris-configure` and `iris-configure-client`. For
example, `iris-configure-client --config /path/to/client_config.json` selects a
particular client file. Checkout commands such as `python configure_client.py`
and `python iris.py configure` continue to work.

## Prepared speech assets outside a checkout

In a source checkout, Pocket and Chatterbox keep their existing environment,
cache and `prepared-voices` locations. In a wheel installation, their default
working root is writable user data, rather than the installed Python package:

| Platform | Default speech working root |
| --- | --- |
| Windows | `%LOCALAPPDATA%/iris-llm` |
| macOS | `~/Library/Application Support/iris-llm` |
| Linux | `$XDG_DATA_HOME/iris-llm`, or `~/.local/share/iris-llm` |

The `.venv-pocket`, `.venv-chatterbox`, `.cache` and `prepared-voices` directories
sit below that root. Explicit `pocket_python`, `chatterbox_python` and model-directory
settings still take precedence for their respective resources. Worker scripts
remain in the installed package. Iris does not copy existing voices or download
weights automatically: prepare them at the new location when changing from a
checkout installation to a wheel.

## Uploads, waiting and timeouts

These server settings are available in the advanced wizard, JSON configuration
and their usual `IRIS_` environment forms:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `max_pending_turns` | 8 | Maximum admitted requests across uploading, queued and running turns. |
| `upload_timeout` | 15 seconds | Total time allowed for an admitted request body to arrive. |
| `queue_timeout` | 30 seconds | Maximum wait for the serial brain after upload finishes. |
| `turn_timeout` | 600 seconds | Cooperative total budget from admission through completion. |
| `timeout` | 120 seconds | Existing model-request limit; also caps optional Google recognition. |

Requests still have the existing body-size limit. Completed uploads enter a FIFO
queue, and the brain processes one at a time, preserving shared-owner history
ordering. An unfinished upload does not hold the brain. The server also caps
connection threads at 64, including requests still reading headers; its socket
inactivity limit remains 15 seconds. Cancellation decisions bypass turn admission
and the brain queue, using the spare connection capacity.

A full admission limit or an expired queue wait returns HTTP 503 without starting
that turn. An upload timeout returns 408. Total turn expiry returns 504 if an
ordinary response has not started; a negotiated voice stream receives a failure
terminal event instead. Iris does not automatically retry these requests.

The deadline is shared by nested stages. HTTP adapters and speech-worker waits
use the remaining allowance; orchestration checks expiry between recognition,
memory/model calls, tool effects, synthesis, archive writes and delivery. Archive
transactions check before committing, so an expired transaction rolls back.

This is cooperative enforcement. An in-process native model call already running
cannot be forcibly interrupted, and a blocked operating-system operation may
finish later. The serial brain remains occupied until such a call returns;
expired work must not then advance to another effect or commit. Application shutdown stops admission and retains resources until active work
releases them, even if it outlasts the bounded drain wait. See
[architecture and ownership](architecture.md#shutdown-and-resource-ownership). A device action that already
happened cannot be undone by a timeout, and a lost response does not prove that
it failed. Check the device before repeating an uncertain action.

The web page gets the configured turn timeout plus five seconds for its network
margin. Expiry aborts the pending fetch, restores the controls and explains the
uncertain-action case. It does not retry the request or display a successful
exchange. A browser abort does not by itself cancel work already accepted by the
server.

## Streaming completion and older clients

Streaming voices that implement `StreamingSynthesizer` (including Pocket and
Kokoro) begin returning audio before full synthesis finishes, and clients sending
`X-Iris-Stream-Text: 1` when the model backend supports token streaming receive
sentence-by-sentence pipelined speech directly from streamed tokens. Short
private-mode, companion-toggle, confirmed-detail and confirmed-forgetting
acknowledgements prepare their complete outcome transactionally; normal
conversation turns commit their source exchange upon acceptance via
`BEFORE_STREAM` before streaming audio starts, so a commit failure never escapes
as a spoken success. Clients requesting streaming already support a buffered
reply where needed.

Updated clients send `X-Iris-Completion: 1`. An updated server acknowledges that
header and supplies final metadata as follows:

- Buffered text/voice replies carry authoritative `X-Iris-Private`,
  `X-Iris-Exchange` and `X-Iris-Deleted-Exchange` headers, as applicable.
- Streamed replies keep the existing `start`, `audio` and `done` events. `start`
  and audio are provisional; `done` contains timing and a `completion` object.
- Cancellable buffered replies put the same object in their final `result` event.

The completion object has `outcome: "committed"`, a boolean `private`, and nullable
`exchange_id` and `deleted_exchange_id` fields. It means server processing and
its applicable state/archive changes completed. It does not mean audio reached
a person's ears, nor that an external device action can be rolled back.

An archive failure after streamed audio produces an error, not a successful
completion. Updated clients reject missing negotiated metadata, missing terminal
events, repeated starts/completions and audio after completion. Frame size is
bounded while bytes arrive, before UTF-8/JSON decoding; total stream size, frame
count and decoded audio are bounded separately. Ordinary fragmentation, including
a UTF-8 character split across network chunks, remains valid.

Older clients do not request the extension and retain their original wire format.
Updated clients can use older servers that do not acknowledge it, but cannot
invent the authoritative final metadata those servers omit. Update both ends
when you need the stronger completion guarantee.

## Existing conversation archives

Keep a verified backup with the server stopped before upgrading an archive.
Starting the server upgrades supported older archives to schema 10 transactionally.
The version 8 migration repairs
derived chunk visibility where correcting one summary note had hidden unrelated,
still-current notes from the same original exchange. It does not change the
original exchange text, remove history, clear recall blocks or restore explicitly
deleted conversations. Fully superseded notes remain superseded. If the migration
fails, its changes and version increment roll back together.

Schema 9 adds durable vector failure tracking and fair summary queue ordering,
and schema 10 adds composite query indexes plus the `AFTER UPDATE OF text` FTS5
trigger on `chunks`. An older Iris build that only understands an earlier schema
will reject a newer archive version. Keep the backup with its matching software
version if you need to roll back; do not manually change the schema version to
bypass that check.

Summary jobs are now durable for every pending conversation. Discovery also finds
work omitted by the old 64-conversation cap. Each checkpoint still processes a
bounded batch serially, and a repeatedly failing job still needs the existing
explicit retry command. Such a job no longer consumes a scarce slot that could
prevent other conversations from ever being summarized. Original source history
remains retained regardless of backlog size.

See [memory storage and recovery](memory-storage.md) for the schema dictionary,
transaction boundaries, retry behaviour and checked backup/restore procedure.
