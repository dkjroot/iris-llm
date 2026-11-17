"""Durable summary queue ordering, shared by append and maintenance operations."""

import sqlite3


def enqueue_summary(db: sqlite3.Connection, owner: str, conversation: str) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] < 9:
        db.execute(
            "INSERT OR IGNORE INTO summary_jobs(conversation,owner) VALUES (?,?)",
            (conversation, owner),
        )
    else:
        db.execute(
            "INSERT OR IGNORE INTO summary_jobs(conversation,owner,last_served) "
            "VALUES (?,?,(SELECT COALESCE(MAX(last_served),0)+1 FROM summary_jobs))",
            (conversation, owner),
        )


def yield_summary(db: sqlite3.Connection, owner: str, conversation: str) -> None:
    """A partly processed or failed job goes behind work already in the queue."""
    if db.execute("PRAGMA user_version").fetchone()[0] >= 9:
        db.execute(
            "UPDATE summary_jobs SET last_served="
            "(SELECT COALESCE(MAX(last_served),0)+1 FROM summary_jobs) "
            "WHERE owner=? AND conversation=?",
            (owner, conversation),
        )
