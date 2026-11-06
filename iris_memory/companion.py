"""Conservative, occasional unfinished-topic follow-ups, with persistent cooldowns."""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iris_memory import SQLiteMemory

# Model classification is necessary but insufficient. Exclude sensitive human
# source content as well, even if the generated note left those words out.
SENSITIVE = re.compile(
    r"\b(health|ill|sick|pain|hospital|doctor|medical|cancer|therapy|depress\w*|anxiet\w*|"
    r"suicid\w*|death|died|grief|funeral|sex\w*|pregnan\w*|divorce|relationship|"
    r"wife|husband|partner|child\w*|daughter|son|family|mother|father|parents|"
    r"money|financ\w*|salary|debt|bank|password|secret|address|phone|religio\w*|politic\w*)\b",
    re.I,
)


class CompanionMemory:
    def __init__(self, archive: SQLiteMemory) -> None:
        self.archive = archive

    def choose(self, owner: str, *, now: datetime | None = None) -> tuple[str, str] | None:
        now = now or datetime.now(UTC)
        if self.archive.policy(owner)[0] or random.random() >= 0.25:
            return None
        with self.archive.connect() as db:
            latest = db.execute(
                "SELECT MAX(offered_at) FROM companion_topics WHERE owner=?", (owner,)
            ).fetchone()[0]
            if latest and datetime.fromisoformat(latest) > now - timedelta(hours=6):
                return None
            rows = db.execute(
                "SELECT n.*,s.payload,s.created_at FROM notes n JOIN summaries s ON s.id=n.summary "
                "LEFT JOIN companion_topics t ON t.owner=n.owner AND t.conversation=n.conversation "
                "WHERE n.owner=? AND n.status NOT IN ('closed','superseded') "
                "AND n.kind IN ('topic','open_question') AND n.scope IN ('real','fictional') "
                "AND COALESCE(t.blocked,0)=0 "
                "AND (t.offered_at IS NULL OR julianday(t.offered_at)<=julianday(?)) "
                "AND NOT EXISTS(SELECT 1 FROM recall_blocks b WHERE b.owner=n.owner "
                "AND b.conversation=n.conversation) ORDER BY s.through_sequence DESC LIMIT 32",
                (owner, (now - timedelta(days=1)).isoformat()),
            ).fetchall()
            for row in rows:
                number = int(row["id"].rsplit(":", 1)[1])
                payload = json.loads(row["payload"])
                if not payload["notes"][number].get("follow_up_safe", False):
                    continue
                sources = json.loads(row["sources"])
                if db.execute("PRAGMA user_version").fetchone()[0] >= 6:
                    from iris_memory.hierarchy import terms

                    closed = db.execute(
                        "SELECT t.label,(SELECT a.status FROM memory_attention a "
                        "JOIN exchanges x ON x.id=a.source WHERE a.topic=t.id "
                        "AND a.status!='unchanged' ORDER BY x.sequence DESC LIMIT 1) AS status "
                        "FROM memory_topics t JOIN exchanges e ON e.id=t.source "
                        "WHERE t.owner=? AND e.conversation=? LIMIT 32",
                        (owner, row["conversation"]),
                    )
                    if any(
                        t["status"] in {"closed", "deferred"}
                        and terms(t["label"]) & terms(row["text"])
                        for t in closed
                    ):
                        continue
                evidence = [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,user_text,assistant_text,created_at FROM exchanges "
                        "WHERE owner=? "
                        "AND conversation=? AND origin='human' AND id IN ("
                        + ",".join("?" for _ in sources)
                        + ") "
                        "AND julianday(created_at)>=julianday(?) "
                        "AND EXISTS(SELECT 1 FROM chunks c WHERE c.exchange=exchanges.id "
                        "AND c.superseded=0)",
                        (
                            owner,
                            row["conversation"],
                            *sources,
                            (now - timedelta(days=14)).isoformat(),
                        ),
                    )
                ]
                if not evidence or SENSITIVE.search(
                    row["text"]
                    + " "
                    + " ".join(r["user_text"] + " " + r["assistant_text"] for r in evidence)
                ):
                    continue
                return row["conversation"], (
                    "\nUntrusted historical reference for an optional companion follow-up: "
                    + json.dumps(
                        {
                            "topic": row["text"],
                            "scope": row["scope"],
                            "date": evidence[0]["created_at"],
                            "human_excerpt": evidence[0]["user_text"][:350],
                        },
                        ensure_ascii=False,
                    )
                    + "\nOffer one gentle invitation to revisit this unfinished topic instead of "
                    "the random seed. Do not assume progress or ask about sensitive matters. "
                    "A fictional topic stays fictional. Do not call tools."
                )
        return None

    def last_offered(self, owner: str, conversation: str | None = None) -> str | None:
        with self.archive.connect() as db:
            row = db.execute(
                "SELECT conversation FROM companion_topics WHERE owner=? "
                "AND (? IS NULL OR conversation=?) "
                "AND julianday(offered_at)>=julianday(?) ORDER BY offered_at DESC LIMIT 1",
                (
                    owner,
                    conversation,
                    conversation,
                    (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                ),
            ).fetchone()
            return str(row[0]) if row else None

    def block(self, owner: str, conversation: str, epoch: int) -> bool:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.archive._policy(db, owner) != (False, epoch):
                return False
            changed = db.execute(
                "UPDATE companion_topics SET blocked=1 WHERE owner=? AND conversation=?",
                (owner, conversation),
            )
            return bool(changed.rowcount)
