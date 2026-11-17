# How Iris keeps conversation memory

Iris keeps completed, non-private exchanges in a SQLite database. SQLite is a
small database engine that reads and writes a file on the server. The original
human text and Iris's final reply are the source record. Summaries, facts, topics
and vectors help Iris find relevant history, but they never replace that record.
A context limit controls what goes into a model request, not what stays saved.
Only explicit forgetting removes saved conversations; private turns aren't saved.

## Where the code lives

`iris_memory.repository` owns archive transactions, conversation lifecycle,
privacy policy and deletion. The familiar `from iris_memory import SQLiteMemory`
import still works. `records.py` defines the result types used between the
repository, retrieval code and application. SQL rows become typed records at the
repository boundary, while JSON exports remain plain JSON-compatible values.

`schema.py` contains the table definitions. `migrations.py` is the ordered registry
of upgrades, including data repairs. `index.py` owns keyword search and summaries;
`semantic.py` owns versioned vectors; `hierarchy.py` owns source-backed facts and
topic attention. `maintenance.py` shares summary queue ordering between normal
conversation writes and maintenance. These modules share one archive transaction
when a change must succeed or fail as a whole.

## Tables and their meaning

An *owner* is the server's configured memory owner, not a phone or Pi session ID.
Each owner has one shared active conversation. A *source* is an original exchange;
an *epoch* is an integer generation used to reject work started before a privacy,
deletion or rebuild boundary. Timestamps are stored as text; exchange timestamps
include their UTC offset. `sequence` is the increasing archive order, independent
of wall-clock time.

| Table | Fields and purpose |
| --- | --- |
| `owners` | `id` identifies a memory owner. |
| `conversations` | `id`, `owner`, `created_at` identify a saved conversation. The owner/id pair is unique. |
| `active_conversations` | `owner`, `conversation` point to each owner's current conversation. Clear context changes this pointer without deleting the old conversation. |
| `exchanges` | `sequence`, `id`, `owner`, `conversation`, `created_at`, `origin`, `user_text`, `assistant_text`, `tool_names`. Text is retained in full. Origin is `human` or `automatic`. Tool names are a JSON array; raw tool arguments, results and hidden reasoning aren't archived here. |
| `summaries` | `id`, `owner`, `conversation`, `through_sequence`, `created_at`, `payload`. Payload is validated JSON; the cursor records how far that conversation has been processed. |
| `notes` | `id`, `summary`, `owner`, `conversation`, `text`, `kind`, `scope`, `status`, `sources`. Sources are original exchange IDs in a JSON array. A note can be superseded without deleting its original exchange. |
| `summary_jobs` | `conversation`, `owner`, `attempts`, `retry_at`, `last_served`. One durable pending job per conversation. Retry times are Unix seconds; `last_served` is queue order, not a timestamp. |
| `recall_blocks` | `owner`, `conversation`. Keeps conversations from before clear context out of unsolicited retrieval. Explicit recall may still search them. |
| `chunks` | `id`, `exchange`, `owner`, `conversation`, `created_at`, `text`, `superseded`. Speaker-labelled search excerpts derived from human exchanges and their paired replies. A source can support several independent notes; correcting one does not hide the others. |
| `chunks_fts` | SQLite FTS5's full-text search index over chunk text. Insert/delete triggers maintain it. SQLite also creates internal FTS tables; application code shouldn't edit them directly. |
| `embedding_models` | `id`, `dimensions`. Identifies the model and preprocessing version used to produce a vector. Incompatible versions don't share vectors. |
| `embedding_vectors` | `chunk`, `model`, `text_hash`, `vector`. Unique per chunk/model. Vectors are normalized little-endian float32 bytes. An empty vector marks a processed chunk that isn't useful for semantic recall. |
| `embedding_failures` | `chunk`, `model`, `attempts`, `retry_at`. Durable failure/backoff state, separate from successful vectors. The source remains pending until processed or explicitly forgotten. |
| `memory_policy` | `owner`, `private`, `epoch`. Private mode survives server restarts. Epoch changes invalidate work based on an earlier policy. |
| `memory_details` | `owner`, `label`, `text`, `updated_at`. Explicitly remembered details are independent of conversation deletion unless the user deletes the detail or all owner memory. |
| `detail_uses` | `owner`, `label`, `conversation`. Records conversations that used an explicit detail. |
| `conversation_uses` | `owner`, `source`, `target`. Records that a target conversation used material from a source conversation. Forgetting follows this provenance so recorded dependent copies aren't left behind. |
| `companion_topics` | `owner`, `conversation`, `blocked`, `offered_at`. Stores companion-topic suppression and last-offered time. |
| `memory_facts` | `id`, `owner`, `fact_key`, `value`, `kind`, `scope`, `lifetime`, `importance`, `status`, `source`, `evidence`, `supersedes`, `detail_label`, `status_before_supersession`. Records grounded fact versions. The saved previous status lets deleting a correction undo its supersession. |
| `detail_supersessions` | `owner`, `label`. Keeps a superseded explicit detail from competing with its natural-language correction. |
| `memory_topics` | `id`, `owner`, `label`, `scope`, `source`. Names a topic grounded in an original exchange. |
| `memory_attention` | `topic`, `source`, `engaged`, `status`, `evidence`. Records human engagement and topic status changes, rather than treating every assistant mention as renewed interest. |

Foreign keys are enabled on every connection. Deleting a conversation cascades
to its exchanges, summaries and source-linked derived rows. Deleting a chunk
also deletes its vectors and retry state. Provenance traversal and restoration
of superseded facts happen in the same transaction as forgetting. Independent
copies spoken separately, exports and older backups are outside that traversal.

The existing indexes cover conversation/sequence reads, note ownership, chunk
sources, fact selection and topic selection. Vector and retry tables have a
unique chunk/model key. Extra indexes should follow measurements of real query
plans; each index also adds work to every affected write.

## Upgrades and transaction boundaries

The current schema version is **9**. `PRAGMA user_version` is SQLite's stored
version number. Iris applies pending migrations in order inside one explicit
transaction, then commits the data and version together. An interruption rolls
them back together. An archive from a newer, unsupported version is rejected.
Opening an existing archive for inspection with `create=False` does not upgrade
it. Normal server startup and semantic indexing can upgrade it.

| Version | Change |
| --- | --- |
| 1 | Full original exchanges, owners and active conversations. |
| 2 | FTS5 search, chunks, summaries, pending summary jobs and recall blocks. |
| 3 | Model-versioned semantic vectors. |
| 4 | Durable private-mode policy and epoch. |
| 5 | Explicit details, use provenance and companion-topic state. |
| 6 | Facts, topics, attention and detail supersession. |
| 7 | Previous fact status for reversible corrections. |
| 8 | Repair chunk visibility where an independent current note still uses the same source as a superseded note. |
| 9 | Fair summary queue order and persistent vector failure/backoff state. |

A read snapshot is a short transaction that sees one consistent database state.
Restoring a session reads its active conversation, policy, revision and history
together. Summary preparation reads policy, revision, job, sources and prior
notes together. Hierarchy preparation and the keyword portion of explicit recall
also group their related reads. Export uses one snapshot for all its tables.
Nested reads on the same archive reuse the snapshot connection.

Model inference runs after these transactions close. The write side checks the
captured epoch, source identity and relevant revision before committing a result.
A private-mode transition, deletion or rebuild therefore prevents an earlier
inference result from recreating derived data. Read snapshots reject writes;
they must never span network requests, inference or waiting for user input.
Iris retains its existing SQLite journal mode and finite lock wait.

## Pending work, failures and rebuilds

Summary work is durable for every pending conversation, without a queue-size
cutoff. A successful partial batch goes behind jobs already waiting. A failed
batch also yields its place, waits 60 seconds and stops being eligible after
three failures until explicitly retried. This keeps a long or troublesome old
conversation from continually taking the first slot. Normal scheduling still
uses bounded serial work; it doesn't launch new background inference threads.

Missing vectors are the semantic work queue. Failures are tracked per chunk and
model with the same 60-second backoff and three-failure limit. Later eligible
chunks can progress while earlier ones wait. The existing retry command resets
failed summary work and vector failures for the configured owner:

```sh
python iris.py memory --config local-config.json retry
```

Stop the server before offline maintenance. `memory index` resumes vector work;
if pending work cannot currently progress, it reports that and exits instead of
spinning indefinitely. An exhausted job needs explicit retry after its underlying
problem is addressed. Progress also needs another normal maintenance opportunity
or an offline indexing run; a stopped server doesn't process work by itself.

`memory rebuild` recreates search chunks and queues summaries from retained
originals. Deleting chunks invalidates vectors and their failure records. It
preserves explicit details, provenance, recall blocks, facts and topic state.
`memory index --reset` resets the configured model's vectors for the owner.
Both operations advance the policy epoch so an older in-flight result cannot
undo the reset. Neither restores explicitly forgotten sources or expires history.

Semantic search currently scores at most the newest 4,096 eligible chunks after
owner, date and visibility filters. A very old topic can therefore be absent
from an undated paraphrase search even though its original exchange is still
saved. Keyword searches and narrower date windows can reach it. The synthetic
large-archive test keeps this limitation visible; improving the retrieval policy
needs a separate measured change.

## Backing up, checking and restoring an archive

1. Find the database selected by `memory_database` in your server configuration.
   If it isn't set, the [README's memory section](../README.md#conversation-memory)
   explains the default paths. Stop the server and any memory maintenance commands
   cleanly so no process is writing the archive.
2. Copy the database to a safe location outside the source checkout. Keep the
   configuration and the Iris commit/version used with that copy. A backup contains
   private conversation data even when the current conversation is in private mode.
3. Check a separate copy of the backup. From the directory containing that copy,
   this works with Python on Windows, macOS and Linux; replace the filename:

   ```sh
   python -c "import sqlite3; d=sqlite3.connect('file:backup.sqlite3?mode=ro', uri=True); print('Schema:', d.execute('PRAGMA user_version').fetchone()[0]); print('Integrity:', d.execute('PRAGMA integrity_check').fetchall()); print('Foreign keys:', d.execute('PRAGMA foreign_key_check').fetchall()); print('Exchanges:', d.execute('SELECT count(*) FROM exchanges').fetchone()[0]); d.close()"
   ```

   Expect integrity `ok` and an empty foreign-key result. Record the schema and
   exchange count. These checks detect structural problems; also inspect an
   owner-scoped export and compare a few known original exchanges, including
   long ones, with what you expected to save. Export is for inspection, not a
   replacement for the database backup: it isn't a supported restore format.
4. To restore, keep all Iris processes stopped. Preserve the current archive as
   another backup, then put the checked copy at the configured database path.
   Use an Iris version that understands its schema. Repeat the integrity checks
   and inspect the owner, active conversation, private-mode state and known text
   before putting the server back into normal use.
5. If rolling back the software after an upgrade, restore the matching older
   archive too. An older build that only supports version 8 cannot open version 9.
   Never lower `user_version` manually to make an old build accept a new database.

A plain file copy assumes a clean shutdown. If a crash has left a `-wal` or
`-journal` file beside the archive, don't delete it or copy just the main file.
With all Iris processes stopped, use SQLite's backup operation to produce a
consistent new file, then check that file as above:

```sh
python -c "import sqlite3; s=sqlite3.connect('file:memory.sqlite3?mode=rw', uri=True); d=sqlite3.connect('recovered-backup.sqlite3'); s.backup(d); d.close(); s.close()"
```

Replace both filenames and use a new destination. Keep the original database and
sidecar files together until recovery has been verified. Restore into a location
without sidecars left over from a different database.

A backup made **before** a forget request still contains the forgotten material.
Restoring it restores that older state. Manage or remove those independent copies
as part of forgetting. A backup made after forgetting, followed by rebuild, cannot
recover deleted source rows from the derived indexes. The automated recovery test
exercises that sequence, including recorded dependent conversations and long
Unicode originals, using disposable synthetic archives.

## Understanding rejected memory updates

`Memory hierarchy unavailable (ValidationError); using existing evidence` means
an older Iris build rejected the model's proposed structured memory update. The
conversation can continue using existing evidence, but that particular update
wasn't accepted. It doesn't mean the SQLite archive has disappeared.

Iris now accepts an otherwise valid JSON object inside one complete Markdown
JSON fence. It still rejects extra prose, partial JSON, wrong field types,
unknown fields and unsupported evidence. The extraction prompt also spells out
the expected field names more clearly. This handles a common formatting mistake;
it doesn't guarantee that every model output will be valid.

Rejected updates now identify safe field paths and error categories, such as
`facts.0.scope: literal_error`. If the backend reports a token-limit stop, the
message identifies truncated output. Model text, validation inputs and arbitrary
extra-field names are omitted from diagnostics. Existing evidence remains the
fallback when validation or grounding fails.
