"""Pure-Python text normalization and token extraction helpers for memory indexing and recall."""

from __future__ import annotations

import re

STOP_WORDS = set(
    "the and that this with from have what about were your would could should "
    "there then they them into just know tell please remember discussed talked "
    "earlier yesterday other some more when where which again next time now".split()
)

# Strip conversational scaffolding before lexical retrieval. These words do not
# identify a topic; in particular, "that thing" must not match an arbitrary archive.
RECALL_WORDS = set(
    "can could you me our we did does was said say discuss discussion conversation "
    "conversations talking talk remember recall resume continue pick left off thing "
    "something yesterday today night last week month days ago other day before "
    "first second third one ones yes no actually mean meant discussed decide decided "
    "decision decisions about had its it two three four five six seven "
    "hello hi iris discussing leave".split()
)


def query_terms(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            w.lower() for w in re.findall(r"[^\W_]{3,40}", text) if w.lower() not in STOP_WORDS
        )
    )[:16]


def topical_terms(text: str) -> list[str]:
    return [word for word in query_terms(text) if word not in RECALL_WORDS and not word.isdigit()]


def topic_terms(text: str) -> set[str]:
    return set(query_terms(text)) - {"let", "lets", "talk", "discuss", "finished", "ago"}


def is_recall_request(text: str) -> bool:
    if re.search(r"\bremember to\b", text, re.I):
        return False
    return bool(
        re.search(
            r"^(?:(?:please|can you|could you|do you|let'?s)\s+)*(?:remember|recall|resume)\b"
            r"|^(?:what|where|how|which|when)\b.{0,50}\b(?:we (?:discussed|talked|decided)"
            r"|did we (?:discuss|talk|decide|say|leave)|were we (?:talking|discussing)"
            r"|our (?:discussion|conversation))\b"
            r"|^pick up where we left off\b"
            r"|\bcontinue (?:our|the|that) (?:discussion|conversation|story)\b"
            r".{0,40}\b(?:yesterday|last|earlier|previous|other day)\b",
            text.strip(),
            re.I,
        )
    )
