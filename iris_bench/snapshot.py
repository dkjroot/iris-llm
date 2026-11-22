"""Read-only database snapshotting, probe drafting, synthetic fixtures and safe working copies."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from iris_bench.models import MemoryProbe, MemoryProbeFile
from iris_memory import Exchange, SQLiteMemory
from iris_memory.index import index_exchange
from iris_memory.text import topical_terms

_PROBE_INSTRUCTIONS = (
    "Review and optionally tweak these local memory probes before running benchmarks. "
    "This file stays in .cache/iris-bench/ (gitignored) and is never pushed to GitHub. "
    "All drafted probes start with judge_safe=false so no private memories are ever sent "
    "to an external LLM judge. Only set judge_safe=true (and fill sanitized_reference) on "
    "entries you are completely happy to send to OpenRouter for quality grading."
)

_COUNT_TABLES = (
    "conversations",
    "exchanges",
    "memory_details",
    "memory_facts",
    "notes",
    "memory_topics",
)


def _ro_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _extract_expected_terms(text: str, *, limit: int = 4) -> list[str]:
    terms = topical_terms(text)
    if terms:
        return terms[:limit]
    words = re.findall(r"[A-Za-z0-9]{2,30}", text)
    seen: list[str] = []
    for word in words:
        low = word.lower()
        if low not in seen:
            seen.append(low)
        if len(seen) >= limit:
            break
    return seen or ([text.strip()[:40]] if text.strip() else [])


def snapshot_database(source: Path, destination: Path) -> dict[str, int]:
    """Create a read-only backup of *source* at *destination* without mutating *source*."""
    resolved_source = source.expanduser().resolve()
    if not resolved_source.is_file():
        raise FileNotFoundError(f"Iris memory database does not exist: {resolved_source}")
    resolved_dest = destination.expanduser().resolve()
    if resolved_dest == resolved_source:
        raise ValueError("Snapshot destination must not overwrite the live database")
    resolved_dest.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{resolved_dest.name}.", suffix=".tmp", dir=resolved_dest.parent
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        src = sqlite3.connect(_ro_uri(resolved_source), uri=True, timeout=5.0)
        try:
            dst = sqlite3.connect(str(tmp_path), timeout=5.0)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
            counts = count_tables(src)
        finally:
            src.close()
        os.replace(tmp_path, resolved_dest)
        return counts
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def count_tables(db: sqlite3.Connection) -> dict[str, int]:
    """Return row counts for known Iris memory tables present in *db*."""
    existing = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    counts: dict[str, int] = {}
    for table in _COUNT_TABLES:
        if table in existing:
            row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0]) if row is not None else 0
    return counts


def draft_memory_probes(
    snapshot_path: Path, owner: str = "default", *, max_probes: int = 8
) -> MemoryProbeFile:
    """Draft candidate memory probes from a read-only snapshot; all start with judge_safe=False."""
    resolved = snapshot_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Snapshot database does not exist: {resolved}")

    probes: list[MemoryProbe] = []
    db = sqlite3.connect(_ro_uri(resolved), uri=True, timeout=5.0)
    db.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        active_row = (
            db.execute(
                "SELECT conversation FROM active_conversations WHERE owner=?", (owner,)
            ).fetchone()
            if "active_conversations" in tables
            else None
        )
        active_conv = str(active_row["conversation"]) if active_row is not None else ""

        # 1. Explicit remembered details (up to 3)
        if "memory_details" in tables:
            detail_rows = db.execute(
                "SELECT label, text FROM memory_details WHERE owner=? "
                "ORDER BY updated_at DESC, label LIMIT 3",
                (owner,),
            ).fetchall()
            for row in detail_rows:
                if len(probes) >= max_probes:
                    break
                label = str(row["label"]).strip()
                text = str(row["text"]).strip()
                if not label or not text:
                    continue
                expected = _extract_expected_terms(text)
                if not expected:
                    continue
                probes.append(
                    MemoryProbe(
                        id=f"probe_detail_{len(probes) + 1:02d}",
                        kind="detail",
                        prompt=f"What do you remember about my {label}?",
                        expected_any=expected,
                        judge_safe=False,
                        source_table="memory_details",
                        source_id=label,
                    )
                )

        # 2. Source-backed hierarchy facts (up to 3, distinct fact_keys)
        if "memory_facts" in tables and "exchanges" in tables:
            fact_rows = db.execute(
                "SELECT f.id, f.fact_key, f.value, f.importance, e.conversation "
                "FROM memory_facts f "
                "LEFT JOIN exchanges e ON e.id = f.source "
                "WHERE f.owner=? AND f.status IN ('confirmed', 'tentative') "
                "AND f.scope='real' AND f.detail_label IS NULL "
                "ORDER BY CASE f.importance WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
                "f.rowid DESC LIMIT 12",
                (owner,),
            ).fetchall()
            seen_keys: set[str] = set()
            added_facts = 0
            for row in fact_rows:
                if added_facts >= 3 or len(probes) >= max_probes:
                    break
                key = str(row["fact_key"]).strip()
                value = str(row["value"]).strip()
                if not key or not value or key.casefold() in seen_keys:
                    continue
                seen_keys.add(key.casefold())
                expected = _extract_expected_terms(value)
                if not expected:
                    continue
                readable_key = key.replace("_", " ")
                conv = str(row["conversation"]) if row["conversation"] else ""
                probes.append(
                    MemoryProbe(
                        id=f"probe_fact_{len(probes) + 1:02d}",
                        kind="fact",
                        prompt=f"Do you remember what you know about my {readable_key}?",
                        expected_any=expected,
                        expected_source_conversations=[conv] if conv else [],
                        judge_safe=False,
                        source_table="memory_facts",
                        source_id=str(row["id"]),
                    )
                )
                added_facts += 1

        # 3. Older non-active conversation search probes (up to 2)
        if "exchanges" in tables:
            seen_convs: set[str] = set()
            added_search = 0
            if "notes" in tables:
                note_rows = db.execute(
                    "SELECT id, conversation, text FROM notes "
                    "WHERE owner=? AND scope='real' AND conversation != ? "
                    "ORDER BY rowid DESC LIMIT 12",
                    (owner, active_conv),
                ).fetchall()
                for row in note_rows:
                    if added_search >= 2 or len(probes) >= max_probes:
                        break
                    conv = str(row["conversation"])
                    if conv in seen_convs:
                        continue
                    terms = topical_terms(str(row["text"]))
                    if len(terms) < 2:
                        continue
                    seen_convs.add(conv)
                    topic_phrase = " ".join(terms[:2])
                    probes.append(
                        MemoryProbe(
                            id=f"probe_search_{len(probes) + 1:02d}",
                            kind="search",
                            prompt=(
                                f"Can you check your saved memory for what we discussed "
                                f"about {topic_phrase}?"
                            ),
                            expected_any=terms[:4],
                            expected_source_conversations=[conv],
                            require_search_tool=True,
                            judge_safe=False,
                            source_table="notes",
                            source_id=str(row["id"]),
                        )
                    )
                    added_search += 1

            if added_search < 2 and len(probes) < max_probes:
                ex_rows = db.execute(
                    "SELECT id, conversation, user_text FROM exchanges "
                    "WHERE owner=? AND origin='human' AND conversation != ? "
                    "ORDER BY sequence DESC LIMIT 20",
                    (owner, active_conv),
                ).fetchall()
                for row in ex_rows:
                    if added_search >= 2 or len(probes) >= max_probes:
                        break
                    conv = str(row["conversation"])
                    if conv in seen_convs:
                        continue
                    terms = topical_terms(str(row["user_text"]))
                    if len(terms) < 2:
                        continue
                    seen_convs.add(conv)
                    topic_phrase = " ".join(terms[:2])
                    probes.append(
                        MemoryProbe(
                            id=f"probe_search_{len(probes) + 1:02d}",
                            kind="search",
                            prompt=(
                                f"Can you search your saved conversations for what we talked "
                                f"about regarding {topic_phrase}?"
                            ),
                            expected_any=terms[:4],
                            expected_source_conversations=[conv],
                            require_search_tool=True,
                            judge_safe=False,
                            source_table="exchanges",
                            source_id=str(row["id"]),
                        )
                    )
                    added_search += 1
    finally:
        db.close()

    return MemoryProbeFile(
        owner=owner,
        created_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        instructions=_PROBE_INSTRUCTIONS,
        probes=probes,
    )


def create_synthetic_memory_db(destination: Path, owner: str = "default") -> MemoryProbeFile:
    """Create a clean, non-private synthetic Iris archive and matching judge-safe probes."""
    resolved = destination.expanduser().resolve()
    if resolved.exists():
        resolved.unlink()
    archive = SQLiteMemory(resolved)
    older_conv = archive.start_conversation(owner)
    ex1 = Exchange(
        "synth-ex-1",
        "human",
        "I finished building the cedar greenhouse irrigation manifold and tested the brass valves.",
        "That sounds tidy — brass valves should hold up well in the damp.",
    )
    archive.append(owner, older_conv, ex1)
    ex2 = Exchange(
        "synth-ex-2",
        "human",
        "My Dobsonian telescope uses an eight-inch parabolic mirror for planetary viewing.",
        "An eight-inch Dobsonian is lovely for Jupiter and Saturn.",
    )
    archive.append(owner, older_conv, ex2)

    active_conv = archive.start_conversation(owner)
    ex3 = Exchange(
        "synth-ex-3",
        "human",
        "Remember that my favourite tea is Earl Grey.",
        "Noted — Earl Grey it is.",
    )
    archive.append(owner, active_conv, ex3)

    now_iso = "2026-10-01T10:00:00+00:00"
    with archive.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR REPLACE INTO memory_details(owner,label,text,updated_at) VALUES (?,?,?,?)",
            (owner, "favourite tea", "Earl Grey with a twist of lemon", now_iso),
        )
        db.execute(
            "INSERT INTO memory_facts(id,owner,fact_key,value,kind,scope,lifetime,importance,"
            "status,source,evidence,supersedes,detail_label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                owner,
                "telescope_aperture",
                "eight-inch parabolic mirror Dobsonian",
                "project",
                "real",
                "ongoing",
                "high",
                "confirmed",
                ex2.id,
                "eight-inch parabolic mirror",
                None,
                None,
            ),
        )
        for row in db.execute("SELECT * FROM exchanges ORDER BY sequence").fetchall():
            has_chunk = db.execute("SELECT 1 FROM chunks WHERE exchange=?", (row["id"],)).fetchone()
            if has_chunk is None:
                index_exchange(db, row)

    probes = [
        MemoryProbe(
            id="synth_detail_01",
            kind="detail",
            prompt="What do you remember about my favourite tea?",
            expected_any=["earl grey", "lemon"],
            judge_safe=True,
            sanitized_reference="Expected answer mentions Earl Grey tea (optionally with lemon).",
            source_table="memory_details",
            source_id="favourite tea",
        ),
        MemoryProbe(
            id="synth_fact_01",
            kind="fact",
            prompt="Do you remember what kind of mirror my telescope uses?",
            expected_any=["eight-inch", "8-inch", "parabolic", "dobsonian"],
            expected_source_conversations=[older_conv],
            judge_safe=True,
            sanitized_reference=(
                "Expected answer recalls an eight-inch parabolic Dobsonian mirror."
            ),
            source_table="memory_facts",
            source_id="telescope_aperture",
        ),
        MemoryProbe(
            id="synth_search_01",
            kind="search",
            prompt=("Can you search your saved memory for what we discussed about the greenhouse?"),
            expected_any=["cedar", "irrigation", "brass", "valve", "manifold"],
            expected_source_conversations=[older_conv],
            require_search_tool=True,
            judge_safe=True,
            sanitized_reference=(
                "Expected answer searches memory and mentions the cedar greenhouse "
                "irrigation manifold / brass valves."
            ),
            source_table="exchanges",
            source_id=ex1.id,
        ),
    ]
    return MemoryProbeFile(
        owner=owner,
        created_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        instructions=_PROBE_INSTRUCTIONS,
        probes=probes,
    )


@contextmanager
def working_db_copy(
    snapshot_path: Path | None,
    *,
    live_db_path: Path | None = None,
) -> Iterator[Path]:
    """Yield an isolated temporary SQLite file copied from *snapshot_path* (or empty)."""
    snap_resolved = snapshot_path.expanduser().resolve() if snapshot_path is not None else None
    live_resolved = live_db_path.expanduser().resolve() if live_db_path is not None else None
    with tempfile.TemporaryDirectory(prefix="iris-bench-db-") as temp_dir:
        working = (Path(temp_dir) / "working.sqlite3").resolve()
        if snap_resolved is not None:
            if not snap_resolved.is_file():
                raise FileNotFoundError(f"Snapshot database not found: {snap_resolved}")
            assert working != snap_resolved, "Working DB must never equal the snapshot path"
        if live_resolved is not None:
            assert working != live_resolved, "Working DB must never equal the live DB path"
        if snap_resolved is not None:
            shutil.copy2(snap_resolved, working)
        yield working


def load_dotenv_if_present(root: Path) -> dict[str, str]:
    """Read key=value pairs from *root*/.env without mutating os.environ or logging values."""
    env_file = root / ".env"
    if not env_file.is_file():
        return {}
    parsed: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed
