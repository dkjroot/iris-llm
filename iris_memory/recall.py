"""Date-aware spoken recall and bounded clarification state, without model guesses."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from iris_chat_engines.messages import Message
from iris_memory.index import MemoryIndex, query_terms

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


def topical_terms(text: str) -> list[str]:
    return [word for word in query_terms(text) if word not in RECALL_WORDS and not word.isdigit()]


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


@dataclass(frozen=True)
class DateWindow:
    start: datetime
    end: datetime
    description: str


def date_window(text: str, now: datetime, timezone: str) -> DateWindow | None:
    """Local calendar days first; convert each midnight independently for DST."""
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    lowered = text.casefold()
    start: date
    end: date
    if "day before yesterday" in lowered:
        start, end = today - timedelta(days=2), today - timedelta(days=1)
    elif re.search(r"\byesterday\b|\blast night\b", lowered):
        start, end = today - timedelta(days=1), today
    elif re.search(r"\btoday\b", lowered):
        start, end = today, today + timedelta(days=1)
    elif re.search(r"\bthe other day\b", lowered):
        start, end = today - timedelta(days=14), today
    elif re.search(r"\blast week\b", lowered):
        end = today - timedelta(days=today.weekday())
        start = end - timedelta(days=7)
    elif re.search(r"\blast month\b", lowered):
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
    elif match := re.search(r"\b(\d{1,3}|one|two|three|four|five|six|seven) days? ago\b", lowered):
        number = match[1]
        days = (
            int(number)
            if number.isdigit()
            else ("one", "two", "three", "four", "five", "six", "seven").index(number) + 1
        )
        start, end = today - timedelta(days=days), today - timedelta(days=days - 1)
    else:
        return None
    return DateWindow(
        datetime.combine(start, time.min, zone).astimezone(UTC),
        datetime.combine(end, time.min, zone).astimezone(UTC),
        start.isoformat()
        if (end - start).days == 1
        else f"{start.isoformat()} to {end.isoformat()} (end excluded)",
    )


@dataclass(frozen=True)
class Candidate:
    conversation: str
    day: str
    label: str
    sources: tuple[str, ...]
    score: int
    semantic_similarity: float = 0.0
    semantic_only: bool = False
    hierarchy_activity: float = 0.0


@dataclass(frozen=True)
class PendingRecall:
    active: str
    candidates: tuple[Candidate, ...]
    expires_at: datetime
    selected: int | None = None
    window: DateWindow | None = None
    query: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecallResult:
    handled: bool = False
    response: str | None = None
    excerpts: tuple[dict[str, Any], ...] = ()
    pending: PendingRecall | None = None


def _choice(text: str, pending: PendingRecall) -> int | None:
    lowered = text.casefold().strip(" .!?")
    prefix = r"(?:(?:no|actually|i mean|i meant|the|that|number)\s+)*"
    suffix = r"(?:\s+(?:one|story|project|discussion|conversation|please|thanks))*"
    lowered = lowered.replace(",", "")
    if (
        len(pending.candidates) == 1
        and pending.selected is None
        and lowered in {"yes", "yes please", "that's it", "that one"}
    ):
        return 0
    ordinals = (r"(?:first|1st|one|1)", r"(?:second|2nd|two|2)", r"(?:third|3rd|three|3)")
    found = [
        i
        for i, pattern in enumerate(ordinals[: len(pending.candidates)])
        if re.fullmatch(prefix + pattern + suffix, lowered)
    ]
    if len(found) == 1:
        return found[0]
    if (
        len(pending.candidates) == 2
        and pending.selected is not None
        and re.search(r"\bother one\b", lowered)
    ):
        return 1 - pending.selected
    terms = set(topical_terms(text))
    matches = [
        i
        for i, candidate in enumerate(pending.candidates)
        if terms and terms <= set(topical_terms(candidate.label))
    ]
    return matches[0] if len(matches) == 1 else None


class RecallResolver:
    def __init__(
        self, index: MemoryIndex, timezone: str, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.index = index
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(UTC))

    def resolve(
        self,
        owner: str,
        active: str,
        utterance: str,
        history: list[Message],
        pending: PendingRecall | None = None,
    ) -> RecallResult:
        now = self.clock()
        explicit = is_recall_request(utterance)
        if pending and (pending.active != active or pending.expires_at <= now):
            pending = None
        carry = None
        if pending and not explicit:
            choice = _choice(utterance, pending)
            if choice is not None:
                return self._selected(owner, pending, choice)
            if not explicit and pending.selected is None and len(utterance.split()) <= 12:
                if utterance.casefold().strip(" .!?") in {
                    "never mind",
                    "cancel",
                    "forget it",
                    "thanks",
                    "thank you",
                }:
                    return RecallResult(True, "All right. We can leave that for now.")
                if not re.search(
                    r"^(?:tell|write|help|what is|what's|how do|new topic)\b", utterance, re.I
                ):
                    if not topical_terms(utterance) and pending.candidates:
                        return RecallResult(
                            True, self._question(pending.candidates), pending=pending
                        )
                    explicit, carry = True, pending
        if not explicit:
            return RecallResult()
        window = date_window(utterance, now, self.timezone) or (carry.window if carry else None)
        terms = list(dict.fromkeys([*(carry.query if carry else ()), *topical_terms(utterance)]))[
            :16
        ]
        if not terms and (window is None or re.search(r"\b(?:that|it|same)\b", utterance, re.I)):
            # Only actual recent human speech supplies missing topical context.
            recent = [
                m.content or ""
                for m in history
                if m.role == "user" and not is_recall_request(m.content or "")
            ]
            terms = topical_terms(" ".join(recent[-2:]))[:8]
        if not terms and window is None:
            return RecallResult(
                True,
                "Which conversation do you mean? Give me a subject or a name to look for.",
                pending=PendingRecall(active, (), now + timedelta(minutes=10)),
            )
        rows = self.index.recall_sources(
            owner, terms, window.start if window else None, window.end if window else None
        )
        candidates = self._candidates(rows, terms)
        if not candidates:
            return RecallResult(
                True,
                "I couldn't find a matching discussion. "
                "Can you give me another detail or a different date?",
                pending=PendingRecall(
                    active, (), now + timedelta(minutes=10), window=window, query=tuple(terms)
                ),
            )
        state = PendingRecall(
            active,
            tuple(candidates),
            now + timedelta(minutes=10),
            window=window,
            query=tuple(terms),
        )
        enough_overlap = not candidates[0].semantic_only and (
            not terms or candidates[0].score >= min(2, len(terms))
        )
        if enough_overlap and (
            len(candidates) == 1
            or (terms and len(candidates) > 1 and candidates[0].score >= candidates[1].score + 2)
        ):
            return self._selected(owner, state, 0)
        return RecallResult(True, self._question(state.candidates), pending=state)

    def _selected(self, owner: str, pending: PendingRecall, choice: int) -> RecallResult:
        selected = pending.candidates[choice]
        # Re-read by owner and source ID: deletion or correction after the question
        # cannot be undone by an old in-RAM candidate list.
        rows = self.index.recall_evidence(
            owner, selected.conversation, selected.sources, pending.query
        )
        if not rows:
            return RecallResult(
                True,
                "That discussion is no longer available in memory. Can you give me another clue?",
            )
        state = PendingRecall(
            pending.active,
            pending.candidates,
            pending.expires_at,
            choice,
            pending.window,
            pending.query,
        )
        return RecallResult(True, excerpts=tuple(rows), pending=state)

    def _candidates(self, rows: list[dict[str, Any]], terms: list[str]) -> list[Candidate]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            day = (
                datetime.fromisoformat(row["created_at"])
                .astimezone(ZoneInfo(self.timezone))
                .date()
                .isoformat()
            )
            groups.setdefault((row["conversation"], day), []).append(row)
        result = []
        for (conversation, day), items in groups.items():
            label = next(
                (item["topic"] for item in items if item.get("topic")), items[0]["user_text"]
            )
            label = " ".join(label.split())[:100].strip(" .!?")
            words = set(
                topical_terms(
                    " ".join(item["user_text"] + " " + item.get("topic", "") for item in items)
                )
            )
            score = len(set(terms) & words)
            result.append(
                Candidate(
                    conversation,
                    day,
                    label,
                    tuple(dict.fromkeys(item["exchange"] for item in items))[:3],
                    score,
                    max((item.get("semantic_similarity", 0.0) for item in items), default=0.0),
                    all(item.get("semantic_only", False) for item in items),
                    max((item.get("hierarchy_activity", 0.0) for item in items), default=0.0),
                )
            )
        result.sort(
            key=lambda item: (
                item.score + 2 * item.semantic_similarity + item.hierarchy_activity,
                item.day,
            ),
            reverse=True,
        )
        return result[:3]

    @staticmethod
    def _question(candidates: tuple[Candidate, ...]) -> str:
        options = []
        for number, candidate in enumerate(candidates):
            day = date.fromisoformat(candidate.day)
            spoken_date = f"{day:%A} {day.day} {day:%B %Y}"
            options.append(f"{number + 1}: “{candidate.label[:70]}” on {spoken_date}")
        if len(options) == 1:
            return "I found one possible discussion: " + options[0] + ". Is that the one you mean?"
        return (
            "I found a few possible discussions. Which one do you mean: "
            + "; or ".join(options)
            + "?"
        )
