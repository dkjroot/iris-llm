# Benchmarking local models with Iris Bench

Iris Bench runs a repeatable suite of real Iris turns against whatever local model server you currently have running, so you can compare engines, quantisations and models on your own hardware without touching your live memory archive or leaking private data to GitHub.

## What it tests

- **Warm-up turns** (default 2, excluded from scores and timings) so the server's KV cache and runtime settle before metering begins.
- **Voice responsiveness**: streams replies through Iris's real sentence pipeline (skipping microphone STT and speaker TTS) to record time to first token (TTFT), time to first spoken sentence (TTFS), total model latency and tokens/sec.
- **Conversation & context growth**: a 3-turn chat check plus a 6-turn evolving shelf project that changes a constraint mid-thread, calls the clock mid-conversation and tracks how TTFS scales as context grows.
- **Facial expressions**: checks that raw replies open with a valid `[[expression:...]]` cue, match the emotional tone, use varied expressions and never leak cues into spoken text.
- **Home automation (with independent verification)**: inspects the living room lamp, switches it on and off, and checks `iris_home` state directly after each turn instead of trusting what the model claims in prose. Restores the lamp's original state in `finally` even if a turn fails.
- **Weather, time & personality**: tests `get_local_time`, `get_local_weather` (with Open-Meteo credit) and adherence to a built-in test personality without breaking arithmetic.
- **Memory recall & extraction**: probes your read-only DB snapshot (or a built-in synthetic archive) including `search_memory`, and tests JSON hierarchy extraction and summary checkpointing with the background worker stopped.

## Privacy and database safety

- `prepare` reads your live SQLite archive strictly in read-only mode (`?mode=ro`) and backs it up into `.cache/iris-bench/snapshot.sqlite3` (gitignored).
- Every benchmark run copies that snapshot into a fresh temporary directory and deletes the copy afterwards. Your live memory database is never opened or written during a run.
- Public result JSONs go into `benchmarks/results/` with an empty `"comments": ""` field for your own notes, coarse hardware info, scores and timings — no prompts, replies, memory contents, town names, custom device names or secrets.
- Drafted memory probes start with `"judge_safe": false`. The optional OpenRouter LLM-as-a-judge pass (`google/gemini-3.8-flash` by default) excludes any unvetted private memory probe, scrubs your configured weather town and custom device name, and shows you one batched human-readable preview before sending anything.

## 1. Prepare your local snapshot (one-time or whenever you refresh)

Start with your local configuration in place (`local-config.json`), then snapshot the database and draft your local probes.

Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe scripts\iris_bench.py prepare
```

Linux (Bash):

```bash
.venv/bin/python scripts/iris_bench.py prepare
```

Review `.cache/iris-bench/memory_probes.json` if you want to tidy up the auto-drafted questions. Leave private probes as `"judge_safe": false`, or set `"judge_safe": true` with a clean `"sanitized_reference"` on any non-sensitive entry you don't mind sending to the OpenRouter judge.

## 2. Run the benchmark suite unattended

Make sure your model server (for example KoboldCPP, PolyStrata or llama.cpp) is already running with the model you want to test. You can either run `full` with no arguments and paste the model/engine card printed by your server start script (press Enter on a blank line when done), or pass `"<engine>" "<model>"` directly (or `--card path/to/card.txt`).

Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe scripts\iris_bench.py full
# Or pass engine and model directly:
.\.venv\Scripts\python.exe scripts\iris_bench.py full "koboldcpp 1.106" "qwen3-8b-q4_k_m"
```

Linux (Bash):

```bash
.venv/bin/python scripts/iris_bench.py full
# Or pass engine and model directly:
.venv/bin/python scripts/iris_bench.py full "koboldcpp 1.106" "qwen3-8b-q4_k_m"
```

When you paste a model/engine card, Iris Bench automatically picks up `Engine`, `Engine Version`, `Model`, `Model Version`, `Model File` (filename only), `Benchmark Label`, and the `Endpoint` URL, `alias` and `ctx` size (ignoring `auth`). Once the run starts, it never asks for input until the end, so you can walk away while it runs. At the end, `full` asks once for an optional one-line `"comments"` note and shows the single OpenRouter judge preview (pass `--no-prompt` with engine/model or `--card` to skip end-of-run prompts too).

If you don't have your home service or internet weather available, add `--mock-tools`; if you want to test without a personal snapshot, add `--synthetic-memory`.

## 3. Batch-judge multiple runs and view the report

If you benchmarked several models unattended with `run` (or `full --no-judge`), you can review and judge all pending runs under one confirmation prompt:

Shared commands (inside your activated virtual environment, or prefixed with `.venv/bin/` / `.\.venv\Scripts\`):

```sh
iris-bench judge --pending
iris-bench report
```

`iris-bench report` prints the leaderboard to the terminal and writes a self-contained offline graphical comparison to `benchmarks/report.html`. You can also edit the `"comments"` field in any JSON file under `benchmarks/results/` and re-run `iris-bench report` to update the report.

[Back to guides](index.md)
