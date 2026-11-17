# Configuration, privacy and the code

Conversation memory is off by default. With it off, history stays in the PC's
RAM, with the configured number of turns and up to 16 recent client sessions.
A fresh client run starts a fresh conversation; saying `exit` clears that session,
and restarting the server clears them all. Turning memory on saves conversation
text in a local database, as described below. Iris never saves microphone recordings.
The server's `trace` setting can print conversation text to its terminal if you enable it.

To time spoken replies, add `--trace-voice` to your usual
`python iris.py client` command on Windows or Linux. Update the server and client
first. The client prints the reply word count, synthesis time, audio length,
and the estimated time from the start of synthesis to the first speech sample
at the speaker. This includes the transfer, client buffering and any playback
padding. The trace uses the audio driver's playback schedule, so Bluetooth or
speaker processing may add delay. If the server and client are on different
machines, keep both system clocks synchronized for the total to be useful.
This flag can be used with the server's conversation trace off.

## Conversation memory

If we've spent an evening talking, I'd quite like Iris to remember it when I turn
her back on. Run `python iris.py configure` on the PC and answer yes to saving
conversation memory. You can choose the database file and an owner name there,
or set these fields in `local-config.json`:

```json
{
    "memory_enabled": true,
    "memory_database": null,
    "memory_owner": "default",
    "memory_context_chars": 24000,
    "model_context_tokens": 8192,
    "context_margin_tokens": 512,
    "memory_summary_tokens": 1200,
    "memory_summary_interval": 8,
    "memory_summary_chars": 3000,
    "memory_retrieval_chars": 3000
}
```

These are additions to your existing configuration. A null database path uses
`%LOCALAPPDATA%/iris-llm/memory.sqlite3` on Windows,
`~/Library/Application Support/iris-llm/memory.sqlite3` on macOS, and
`$XDG_DATA_HOME/iris-llm/memory.sqlite3` on Linux (or
`~/.local/share/iris-llm/memory.sqlite3` if that variable isn't set). A custom path
can be absolute or relative to the server's working directory. Environment overrides
are `IRIS_MEMORY_ENABLED`, `IRIS_MEMORY_DATABASE`, `IRIS_MEMORY_OWNER` and
`IRIS_MEMORY_CONTEXT_CHARS`. Restart the server after changing these settings.

Iris resumes one active conversation per owner. The Pi and laptop
share that conversation, regardless of their temporary connection IDs. This is a
single-user setup: an owner name selects local data on the server; it isn't a login
or a way to give different people private accounts. If you're running several
servers, give each its own database. Don't open the same archive from two running
servers at once.

Iris saves each completed exchange after the reply and any speech have passed
validation. Stopping the client, restarting either machine, or evicting an old
RAM session doesn't remove the archive. “Stop listening” and companion mode off
keep the conversation available. A new client still starts with companion mode
off. “Clear context” starts a new conversation for all your clients; it doesn't
erase the old one. Just changing the subject continues the current conversation.
You can temporarily stop reading and saving conversation memory with the spoken
private-mode controls below. Setting `memory_enabled` off and restarting the
server still disables persistence altogether.

On a restart, Iris loads recent complete exchanges, up to `history_turns` and
`memory_context_chars` characters in total. She also has source-linked notes from
the active conversation and a few matching excerpts from the archive. An ordinary
question triggers a local keyword search using your words and recent conversation;
you don't have to ask for a memory tool. The excerpts include their original dates
and exchange IDs. This is keyword matching, so a distinctive name or subject helps.
Source-linked summary notes can help find a discussion even when their wording
differs from the original exchange. Optional semantic recall can also find
paraphrases with few shared words; its setup is below.

“Clear context” also blocks automatic recall of earlier conversations. Asking
explicitly to remember or resume a subject can search them again. That gives us a
fresh start without silently deleting the archive. `history_turns: 0` disables
history, summaries and retrieved excerpts in the chat prompt while still archiving
new exchanges if memory is enabled.

### Private conversations and forgetting

Sometimes I want a chat without adding it to Iris's saved history. Say **“private
mode on”** or **“start a private conversation”** as a complete utterance. Iris speaks
a confirmation. Switching into private mode clears the working context on every
client for your configured owner and starts with fresh RAM history. While private, she neither reads saved
conversations nor saves the new exchanges, summaries or embeddings. She can still
chat and use the enabled live skills.

Say **“private mode off”** or **“end the private conversation”** to leave it. Iris
discards private working history across those clients and resumes the saved
conversation from before private mode. Private exchanges aren't copied back into
it. **“Memory status”** tells you whether private mode and conversation saving are
on. These controls work by voice or typed through the text client; capitalisation
and surrounding punctuation don't matter.

Private mode applies to all clients sharing the configured owner. With persistence
enabled, the mode flag survives client and server restarts, `exit`, and eviction of
a RAM session, until you turn it off. The database stores that flag and a change
counter, without private conversation text. Private RAM history itself is bounded
by the usual history/context limits and is lost on clear, restart or eviction.
“Stop listening” and companion mode off keep the private flag on. “Clear context”
while private discards that client's private RAM context and leaves saved records
alone. If persistence is disabled in configuration, no database is created; the
private diagnostic settings then last only for the running server process.

Update both server and client code to use this feature. The server disables Iris's
request/tool tracing during private chat. An updated client receives the private
state on each successful response, hides the transcript from normal terminal and
service-log output, and skips `--save-reply` files while private. The reply still
plays aloud. Older clients can connect, but don't know to hide private transcripts.
No new client configuration is needed for the default behaviour.

For an interactive text conversation where you deliberately want private replies
visible, launch the client with `--text --show-private-text`. That opts into terminal
output, which your terminal or service may retain; it still doesn't enable reply
WAV saving. Typed text can also remain visible in your terminal's input display.
Private mode controls Iris's own memory and these diagnostics. The configured chat
and speech services still receive the live requests, so their own logging settings
matter too. Existing terminal logs, exports, recordings made outside Iris and
backups aren't erased by entering private mode.

If Iris picks up something by mistake, or gets the wrong end of the stick, say
or type **“delete previous”** on its own. This immediately removes the latest
completed exchange from the current conversation, including what you said and
Iris's reply. In voice mode, Iris tells you when she's deleted it or if there
was nothing to delete. Then you can give her the message again. With memory on, the
current saved conversation is shared across clients. In private mode or with
memory off, the command works on this client's live conversation.

When there's a saved copy, Iris removes it from her archive and search, and
rebuilds any affected summaries from the exchanges that remain. A command you
gave Iris may already have done something outside the conversation, such as
switching a light on. Old logs, exports and backups also keep their own copies.

To delete the **current saved conversation**, say **“forget this conversation”**.
Iris tells you how many exchanges it contains, then waits for **“confirm forget
this conversation”**. Say **“cancel forgetting”** to cancel. A bare yes doesn't
delete anything. The request expires after two minutes and is cancelled by another
recognised utterance, a context/mode change or restart. If another client adds an
exchange before you confirm, ask again so you can confirm the updated scope.

Confirmed deletion removes that conversation's source exchanges, summaries, notes,
search chunks, embeddings and queued summary work, then starts a fresh saved
conversation if the active one was removed. It also clears cached context and recall
choices across your clients, so old RAM state can't silently save it again. When a
later conversation used a retrieved source, Iris records that connection. Forgetting
the source also removes those dependent conversations, including their other topics;
the confirmation tells you how many before you agree. Independent conversations stay
in the archive. These links start with this version: older, untracked paraphrases
aren't automatically found and erased. Explicitly remembered details are separate
and stay until you forget them. Independent backups, exports and old terminal/service logs keep their
own copies. “Clear context” continues to start afresh while retaining the archive;
it doesn't mean forgetting.

If you've just recalled an older discussion, Iris distinguishes it from the
current saved conversation before accepting a deletion request. **“Forget the
current conversation”** makes that scope explicit and still needs the confirmation.
After selecting an older discussion, **“forget that conversation”** or **“forget the
recalled conversation”** targets its saved conversation instead. Iris identifies that
scope and asks for the same confirmation. The deletion unit is the whole saved
conversation, even if your recall referred to only one day's topic. You can also use
`memory list` and `memory delete --conversation ID` on the server. **“Forget this conversation” while private**
just discards the current client's private RAM conversation; it cannot delete the
saved conversation from before private mode.

### Remembering and changing a detail

For something I deliberately want Iris to keep, I can give it a short label. These
are explicit, human-authored details, separate from the summaries she makes herself.
For example, say **“remember story captain as fictional Captain Violet”**, then
**“confirm memory change”** after she reads it back. That confirmation is useful
when speech recognition has misheard a name. **“Cancel memory change”** cancels it;
a bare yes doesn't confirm. Requests expire after two minutes, and another utterance,
context change or restart cancels the pending change.

**“List remembered details”** reads up to eight at a time. **“Show the detail about
story captain”** reads that label's current value. Labels match exactly, ignoring
capitalisation. Say **“correct story captain to fictional Captain Emerald”** to
replace its value, or **“forget the detail about story captain”** to remove it. Both
read back the proposed change and wait for **“confirm memory change”**. Immediately
after showing or saving one detail, **“forget that detail”** and **“correct that detail
to …”** can use the selected label. An ordinary chat turn clears that selection.

There is no fixed limit on how many explicit details you can save. Each labelled
entry can have a label up to 80 characters and a value up to 500. Iris searches a
bounded selection and fits only a small amount into her context; saving more doesn't
mean every reply will include every detail. Include
“fictional” in the value when it describes a character rather than you. Private
mode can't read or change these details. No client options are needed.

Corrections and deletions remove the old stored value. They also remove whole saved
conversations that received it as reference, including any other topics in those
conversations. Iris states that count before you confirm; cancel if you'd rather
export them first. This deliberately broad deletion keeps old answers, summaries
and later recorded recalls from restoring the old value. The controls themselves
aren't archived as chat exchanges. A label doesn't find every independently spoken
copy of the same fact elsewhere: to remove an older discussion, forget its
conversation, or use the full archive deletion below. Backups and exports keep
their own copies.

Use these controls when you want a durable change. An ordinary model reply saying
“I'll remember” or “I've forgotten” cannot make that change on its own.

### Which memories come to mind

With conversation memory enabled, `memory_hierarchy_enabled` defaults to `true`.
Iris makes a small, separate metadata request before an ordinary chat reply, using
the configured model. It looks for clear human assertions, corrections and the
topics you're actually engaging with. The reply model receives compact reference
data, rather than a set of rules about scoring memories.

Confirmed lasting identity facts, such as your name, get priority in a small part
of the context, along with high-importance lasting preferences. Ongoing projects
and dated events are selected for relevance. An old bus journey stays in the archive
but doesn't occupy that core space simply because it was once discussed. Explicitly
saved name labels are also prioritised. This isn't unlimited context: candidate
searches and the final prompt remain bounded, even as the archive grows.

A natural correction such as “Actually, my name is David, not Dave” can supersede
the old interpretation immediately. The original exchanges and the older fact
version remain available for historical questions; they aren't rewritten or deleted.
If it clearly corrects a labelled detail, its current displayed value follows the
correction too. This is different from the explicit **“correct LABEL to VALUE”**
control above, which still reads back and confirms its broader deletion scope.
An uncertain or unresolved correction leaves the confirmed value alone. Nothing
about a model's own reply or a search result can correct a fact by itself.

Topic recency follows your attention. Returning to a story or developing it further
refreshes its activity. Iris merely mentioning it, offering an unanswered opener,
or retrieving it doesn't. “I finished that story ages ago, let's talk about The
Terminator” closes the known story without refreshing its engagement, then directs
attention towards the film. Discussing a completed project doesn't automatically
make it unfinished again. Original source timestamps stay separate from the last
engagement timestamp; “ages ago” doesn't acquire an invented completion date.

The interpretation is model-assisted and can make mistakes, including mistaking
fiction or a bad transcription for a real assertion. Source checks, uncertainty
labels and correction controls help, but don't make extraction infallible. Existing
archives remain searchable; startup doesn't reprocess your entire history. New
annotations build up as you talk. Private turns, automatic openers and failed turns
don't add these facts or engagement signals. Confirmed forgetting removes the
associated derived records and recorded dependencies too.

This adds one serial model request to ordinary human chat turns, so it can add
noticeable latency on a slower host. It uses `memory_summary_tokens` as its output
allowance. You can switch it off in advanced `python iris.py configure`, or set
`memory_hierarchy_enabled` to `false` and restart. That stops new interpretation and
hierarchy prompt selection without deleting the stored annotations or undoing
accepted corrections to labelled details. Normal conversation memory and explicit detail controls still work. No client settings
or additional model download are needed.

To inspect the current bounded candidates, their source evidence, validity and
last engagement, run on the server:

```sh
python iris.py memory inspect --query "Sapphire story"
```

The preview shows selected current facts; it isn't a transcript of the exact last
model prompt. `memory export` includes all stored fact versions, topics and attention
records alongside the original conversations. As with other maintenance commands,
stop the server first when inspecting a consistent snapshot of its archive.

### Asking about an earlier discussion

You can say “remember the Sapphire story?”, “what were we talking about yesterday?”
or “recall the garden project last week”. Iris searches your saved summary notes
and original exchanges, then uses a few source excerpts to continue. This all runs
on the server and uses the existing memory settings; the client needs no new options.

Spoken dates use `local_timezone`, including daylight-saving changes and days near
midnight. “Yesterday” means the previous local calendar day. “Last night” currently
uses that same whole-day window. “Last week” means the previous Monday-to-Sunday
week, and “last month” means the previous calendar month. “Two days ago” and similar
numbered dates work too. “The other day” searches the previous fourteen days rather
than assuming a particular date.

If several discussions fit, Iris offers up to three dated choices. Answer with
“the first one”, “the second one”, or a subject such as “the story”. After a selection,
“No, the other one” can switch between two choices. A weak match asks for confirmation;
a bare yes only selects a single offered choice. If she finds nothing, she asks for
another subject, name or date, and you can give that clue in your next turn.

A vague “remember that thing?” can borrow the subject from your last two human
messages. If there is no useful subject or date, Iris asks which discussion you mean.
She groups possible discussions by saved conversation and local day; several topics
within the same conversation on the same day may still need a more specific question.

Choices stay available in that client's server session for ten minutes. A new topic,
“never mind”, clearing context, a client/server restart, or eviction of the RAM session
ends that clarification. If you restart while choosing, repeat the recall request.
The original records are checked again when you choose, so deleting or superseding a
source makes an old choice unavailable. Selecting an older discussion supplies
reference excerpts to the current conversation; it doesn't move the archive's active
conversation back to the old one.

If a reply needs more evidence than the automatic excerpts provide, Iris can also
use a small, read-only `search_memory` skill. It searches within the configured owner,
returns dated source excerpts, and can't change memory. It allows two searches per
reply within the usual overall tool limits. Multiple matches still need a discussion
choice. Set `memory_search_tool` to `false` to disable it; `tools_enabled: false`,
private mode, or disabling prompt history/retrieval also removes it. Automatic
companion openers never receive tool definitions.

### Optional companion follow-ups

Iris can occasionally pick up an unfinished creative or practical topic in companion
mode. Enable **memory-based companion follow-ups** in `python iris.py configure`,
or set **`memory_companion_followups: true` on the server**. The default is off. It needs saved memory and the usual companion mode. The client
needs no extra configuration, and its listening timers and unanswered-opener backoff
work as before.

On each idle opener, there is a one-in-four chance to look for an eligible topic;
otherwise she uses the playful random seeds. The topic must have a current,
source-linked summary marked suitable for a follow-up, with human evidence from
the last fourteen days. Closed, superseded, cleared and uncertain topics aren't
eligible. Personal or sensitive subjects are excluded through both the summary
instructions and a conservative source-text filter. That classification can still
make mistakes, so leave the option off if unsolicited recollection isn't welcome.
She is instructed to invite a return to the topic without assuming you've made
progress since last time.

After a successful follow-up, the server waits at least six hours before another
memory follow-up, and at least a day before using the same conversation. Those
cooldowns survive restarts. Say **“don't bring that up again”** to exclude the last
memory follow-up's whole saved conversation from future companion choices. It
still exists for explicit recall. The last offered topic remains selectable for
that opt-out for a day after a restart. If memory search or derived data fails,
she falls back to a generic opener.

### Optional semantic recall

The keyword search is useful when I remember a name, but sometimes all I remember
is the idea. “Water collection arrangement” ought to find the discussion about a
rainwater tank. You can enable a small local embedding model to help with that.
It runs on the PC server alongside Iris and leaves the chat model as it is.

With the server stopped, install the extra in its Python environment:

```sh
python -m pip install -e ".[memory]"
```

Run `python iris.py configure` and enable local semantic recall, or add these fields
to your existing PC configuration:

```json
{
    "memory_enabled": true,
    "memory_semantic_enabled": true,
    "memory_embedding_model_dir": null,
    "memory_semantic_min_score": 0.45
}
```

Then explicitly prepare the model while online:

```sh
python iris.py memory download-model
```

This downloads the pinned, quantised
[all-MiniLM-L6-v2 ONNX export](https://huggingface.co/Xenova/all-MiniLM-L6-v2/tree/751bff37182d3f1213fa05d7196b954e230abad9)
and tokenizer from Hugging Face, about 24 MB in total, and verifies both checksums.
The model and tokenizer go in `models/all-MiniLM-L6-v2` next to your database when
the directory setting is null. You can choose another server directory with
`memory_embedding_model_dir`; `download-model --directory PATH` overrides it for
that preparation command. Ordinary startup and inference only use the verified
local files. Missing files or extras produce a setup error; startup never fetches
the model for you. The client needs no extra package or configuration.

If you already have an archive, build its vectors before restarting the server:

```sh
python iris.py memory index
```

This resumes from chunks that haven't been indexed for this exact model version.
If this is your first use of memory, you can skip that command: there is no old
archive to index yet. While the server runs, searches index at most eight pending
chunks per turn, then search the vectors available so far. The first few searches
can therefore miss material in a large backlog until indexing catches up. For a
large existing archive, running `index` with the server stopped is the quicker way
to get it ready. Ctrl-C during offline indexing preserves finished batches, so the
same command can resume later. `memory index --reset` rebuilds the current owner's
vectors for the selected model while keeping source history and summaries.

The local model encodes short passages into 384-number vectors, using the
[authors' mean pooling and normalisation method](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
It uses two CPU inference threads and at most 256 word-piece tokens per passage.
Vectors are stored in SQLite with their model revision and encoding version,
roughly 1.5 KB per chunk before database overhead. Different model versions aren't
mixed. Deleting source conversations or rebuilding their derived chunks also
removes their vectors. Index writes recheck the source after inference, so stale
work cannot bring back a deleted exchange.

Retrieval combines word overlap, semantic similarity and recency. Explicit date
filters and the clear-context boundary still apply before choosing sources. A
match found only through semantic similarity in an explicit recall request asks
for confirmation, even if there is only one candidate. `memory_semantic_min_score`
filters weak similarities; it isn't a confidence percentage, and a matching score doesn't prove two discussions
are the same. English paraphrases are the intended use. Names, unusual wording
and long passages can still need another clue. If inference fails during a search,
Iris logs the failure type and keeps the keyword matches available.

To compare both methods on the bundled synthetic evaluation set, run:

```sh
python iris.py memory evaluate --corpus tests/fixtures/semantic_recall_cases.json
```

It creates a temporary test archive and reports model load, indexing and query
timings plus the retrieved evidence for each case. It doesn't read or modify your
real conversations. The fixture includes paraphrases, ambiguity, unrelated queries
and an example this model still misses. It is a small regression check, rather
than a promise of accuracy on every conversation. Evaluation uses prepared local
files, and accepts `--directory PATH` if you keep the model somewhere else.

Environment overrides are `IRIS_MEMORY_SEMANTIC_ENABLED`,
`IRIS_MEMORY_EMBEDDING_MODEL_DIR` and `IRIS_MEMORY_SEMANTIC_MIN_SCORE`. Turn semantic
recall off and restart the server to return to keyword/summary retrieval; the
source archive remains available. Back up the database before its automatic schema
upgrade, and stop the server before offline indexing or maintenance.

### Context size and compaction

Set `model_context_tokens` to the context size actually enabled on your model
host, rather than the largest size advertised for the model. The default is 8192;
Iris doesn't discover that limit from the server. The configuration wizard asks
for it, and `IRIS_MODEL_CONTEXT_TOKENS` overrides it. This setting covers the whole
request, including instructions, history, tool definitions and the reply.

Iris reserves `max_tokens` for the reply and `context_margin_tokens` for template
overhead. Until the backend can supply its own tokenizer, she estimates input
size conservatively from UTF-8 bytes and message framing, including tool schemas,
reasoning and tool exchanges. This deliberately compacts earlier than an exact
token count usually would. It isn't a measurement of the model's native tokens;
leave a margin for your host's template.

As the estimated input approaches three quarters of its budget, Iris makes a
summary checkpoint. When the combined prompt is under pressure, she compacts the
older exchanges into notes and keeps up to two recent complete exchanges verbatim.
The usual `history_turns` limit is another checkpoint trigger. Every model request,
including tool follow-ups and automatic openers, gets a final size check. Whole
older turns can be removed to fit; a current message or tool exchange that still
won't fit produces an error rather than sending an oversized request.

The full conversation stays in SQLite even after it leaves the working context.
Summary notes distinguish topics, user statements, Iris's proposals, decisions and
open questions, with fiction and tentative details labelled. A summary reads a
bounded batch of original exchanges, rather than repeatedly rewriting one old
summary. Recent notes fit within `memory_summary_chars`; older source details can
come back through search. This extends useful continuity beyond one context window,
though it doesn't mean every detail of an evening is always in the prompt.

### Summary checkpoints and maintenance

With memory enabled, Iris normally summarises after eight unsummarised exchanges,
or sooner under context pressure. Saying “stop listening”, switching companion
mode off, clearing context or exiting also requests a checkpoint. There is no
extra client notification when its local wake window times out; pending work can
continue on the next conversation turn.

Summaries use the configured chat model with no tools, a separate
`memory_summary_tokens` output allowance, and at most twelve original exchanges
per request. Each turn or control checkpoint processes at most one batch. The
durable queue retains work for every pending conversation; the processing batch,
rather than the archive or backlog, is bounded. Summary calls run serially with
chat, so a checkpoint adds model latency; it doesn't generate simultaneously in
the background. Enable `trace` temporarily to see their timings, remembering that
trace also prints conversation text.

Malformed summaries leave the last valid notes intact. Failed jobs wait at least
a minute between attempts and pause after three failures. Original exchanges remain
available, and normal chat falls back to recent text and whatever reference data
still works. If a single source exchange is too large, the summary gets labelled
beginning-and-end excerpts; notes from that partial view stay tentative. The full
source is still stored and searchable. Corrections that the summariser identifies
supersede older notes. A source excerpt is excluded from normal search only when
none of its summary notes remains current; correcting one claim mustn't hide an
unrelated claim from the same original exchange. Retained excerpts are historical
text, not a declaration that every claim in them is still current. This is
model-assisted bookkeeping, so inspect the export if a recalled detail seems
wrong; it isn't a guarantee that the model recognises every contradiction.

The database upgrades existing archives automatically when the enabled server
starts. Back it up first. Keyword search needs SQLite FTS5; if your Python build
lacks it, startup explains what is missing. The normal Python distributions usually
include it. The optional semantic model has its own explicit preparation command,
described above.

The archive contains recognised or typed input, final replies, UTC timestamps,
source IDs and the names of any tools used. Automatic companion openers are marked
as automatic events, rather than something you said. It doesn't contain audio,
model reasoning, configured API keys, or raw tool arguments and results. Anything
you actually say or type can still appear in the transcript, including personal
details. The database is an ordinary local file, without application encryption.
Treat it and its exports like your other personal documents. Restored tool-based
answers describe an earlier exchange; they aren't fresh time or weather readings.

If saving fails, the server reports an error and rolls back the working turn.
It doesn't quietly continue while pretending the exchange was saved. A dropped
connection can still happen after a successful save, and Iris doesn't automatically
retry requests. Storage IDs prevent the same exchange object being inserted twice;
they don't deduplicate network requests or guarantee tools run only once.

To inspect or remove an archive, run these on the PC with the server stopped:

```sh
python iris.py memory list
python iris.py memory export > iris-memory-export.json
python iris.py memory retry
python iris.py memory rebuild
python iris.py memory delete --conversation CONVERSATION_ID
python iris.py memory delete --all
```

The export includes versioned summaries, current note statuses, embedding model
identifiers/indexed-chunk counts, the owner's private-mode flag/change counter,
explicit remembered details, versioned hierarchy facts and topic attention,
their conversation-use links, companion exclusions
and cooldowns, and pending summary jobs alongside the source exchanges. `retry` resets failed summary jobs after you've
fixed the model or configuration. `rebuild` discards derived summaries and search
chunks for this owner, rebuilds the keyword index and queues fresh summaries from
the originals. Any semantic vectors for those chunks are removed too; they can be
rebuilt with `memory index` or gradually during later searches. It keeps source history and clear-context boundaries. Those new
summaries are generated on later server turns, so recall may temporarily rely on
raw excerpts. Run the enabled server once to upgrade a phase-one archive before
using the new maintenance commands.

Use the ID from `list` in the selected deletion command. If it has recorded dependent
conversations, the CLI stops and explains the extra scope; review the export and add
`--include-dependent` to delete those whole conversations too. `--all` removes every
conversation and explicit remembered detail for the configured owner, which is handy
for clearing a test archive. It leaves the private-mode flag as it was.
For a different configuration file, put `--config PATH` after `memory` and before
`list`, `export`, `inspect`, `retry`, `rebuild` or `delete`. Inspection also works with memory
disabled and never creates a missing archive. Deletion removes source exchanges
and their summaries, notes, search chunks and queued jobs from the live database;
independent exports and backups keep their own copies. Start the server again after
maintenance so it can't retain deleted material in RAM.

The full conversation archive has no automatic expiry or maximum turn count.
Context limits control how much Iris reads for a reply, not how much history she
keeps. Summaries and search indexes help her find things; they don't replace or
trim the original completed exchanges. That leaves the source history available
for better retrieval and models in future. Private conversations still aren't
saved, and confirmed forgetting still deletes its stated scope.

To back up the archive, stop the server and copy the
database file somewhere safe. Restore that copy to the configured path with the
server stopped. Keep backups out of your source checkout, and delete test exports
when you're finished with them. The [memory storage guide](memory-storage.md)
explains how to check a backup, recover after an unclean shutdown and keep the
archive compatible when rolling back an update.

The local setup keeps model traffic on the PC and sends recordings and replies
between the client and PC. There is no automatic fallback to a hosted service.
A local model address can't tell us whether that model server itself forwards
data elsewhere, so check its configuration too. Optional `wav2vec2` recognition
needs its matching extra, while Google recognition requires outbound remote
services and sends audio online. Neither is needed for the normal Whisper setup.

`iris.py` provides the commands. `iris_app.runtime.Brain` composes the conversation,
speech and memory services, and `iris_droid/server.py` adapts them to HTTP.
`iris_droid/client.py` handles microphone capture and playback. The
[architecture guide](architecture.md) explains ownership and the turn lifecycle;
[contributing](../CONTRIBUTING.md) covers development and extension recipes.

For development, install `python -m pip install -e ".[dev]"`, then run:

```sh
ruff check .
ruff format --check .
mypy
python scripts/check.py
```

The tests use fake devices and models, plus local HTTP tests. They don't need a
microphone, GPU, downloaded weights or a working model server. Actual listening
quality and wake accuracy still need a conversation on the hardware you'll use.
The [testing guide](testing.md) covers branch coverage, installed-package checks,
browser tests and local performance measurements. There is also a
[compatibility reference](compatibility.md) for keeping existing behaviour
steady while changing the code.
The [prototypes branch](https://github.com/dkjroot/iris-llm/tree/prototypes/examples)
keeps the earlier experiments if you're interested in how this started.

[Back to Iris](../README.md) · [All guides](index.md)
