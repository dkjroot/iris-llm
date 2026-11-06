"""A repeatable synthetic recall corpus and spoken-reference lifecycle checks."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

from iris_chat_engines.budget import estimate
from iris_chat_engines.messages import Message
from iris_droid.server import Brain
from iris_memory import Exchange, SQLiteMemory
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_memory.recall import RecallResolver, date_window
from iris_settings import Settings

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "recall_cases.json").read_text())
NOW = datetime.fromisoformat(CORPUS["now"])


@pytest.fixture
def archive(tmp_path):
    settings = Settings(
        memory_enabled=True, memory_database=tmp_path / "memory.sqlite3", tools_enabled=False
    )
    memory = SQLiteMemory(settings.memory_database)
    conversations = {}
    for source in CORPUS["exchanges"]:
        conversation = memory.start_conversation("default")
        conversations[source["conversation"]] = conversation
        memory.append(
            "default",
            conversation,
            Exchange(source["id"], "human", source["user"], source["assistant"]),
        )
        with memory.connect() as db:
            db.execute("UPDATE exchanges SET created_at=? WHERE id=?", (source["at"], source["id"]))
            db.execute(
                "UPDATE chunks SET created_at=? WHERE exchange=?", (source["at"], source["id"])
            )
    active = memory.start_conversation("default")
    index = MemoryIndex(memory)
    resolver = RecallResolver(index, "Europe/London", clock=lambda: NOW)
    return settings, memory, index, resolver, active, conversations


def source_ids(result):
    return {row["exchange"] for row in result.excerpts}


def candidate_ids(result):
    return {source for candidate in result.pending.candidates for source in candidate.sources}


@pytest.mark.parametrize("case", CORPUS["queries"], ids=lambda c: c["utterance"])
def test_recall_corpus(archive, case):
    settings, memory, index, resolver, active, conversations = archive
    result = resolver.resolve("default", active, case["utterance"], [])
    if case["outcome"] == "found":
        assert result.handled and result.response is None
        assert source_ids(result) == set(case["sources"])
    elif case["outcome"] == "clarify":
        assert result.response and "Which one" in result.response
        assert candidate_ids(result) == set(case["sources"])
    elif case["outcome"] == "clue":
        assert result.response and not result.excerpts and result.pending
    else:
        assert not result.handled and result.pending is None


@pytest.mark.parametrize(
    "wording,expected_start,expected_end",
    [
        ("yesterday", "2026-11-04T00:00:00+00:00", "2026-11-05T00:00:00+00:00"),
        ("the day before yesterday", "2026-11-03T00:00:00+00:00", "2026-11-04T00:00:00+00:00"),
        ("today", "2026-11-05T00:00:00+00:00", "2026-11-06T00:00:00+00:00"),
        ("last night", "2026-11-04T00:00:00+00:00", "2026-11-05T00:00:00+00:00"),
        ("last week", "2026-10-26T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
        ("last month", "2026-09-30T23:00:00+00:00", "2026-11-01T00:00:00+00:00"),
        ("the other day", "2026-10-21T23:00:00+00:00", "2026-11-05T00:00:00+00:00"),
        ("two days ago", "2026-11-03T00:00:00+00:00", "2026-11-04T00:00:00+00:00"),
    ],
)
def test_relative_calendar_dates(wording, expected_start, expected_end):
    window = date_window(wording, NOW, "Europe/London")
    assert window.start.isoformat() == expected_start
    assert window.end.isoformat() == expected_end


@pytest.mark.parametrize(
    "now,hours", [("2026-03-30T01:00:00+00:00", 23), ("2026-10-26T01:00:00+00:00", 25)]
)
def test_yesterday_crosses_daylight_saving_correctly(now, hours):
    window = date_window("yesterday", datetime.fromisoformat(now), "Europe/London")
    assert (window.end - window.start).total_seconds() == hours * 3600


def test_local_midnight_not_utc_date():
    # It is still November 4 in New York, so yesterday is November 3 there.
    window = date_window("yesterday", datetime(2026, 11, 5, 0, 30, tzinfo=UTC), "America/New_York")
    assert window.start.isoformat() == "2026-11-03T05:00:00+00:00"
    assert window.end.isoformat() == "2026-11-04T05:00:00+00:00"


def test_number_topic_selection_and_mistaken_match_correction(archive):
    settings, memory, index, resolver, active, conversations = archive
    options = resolver.resolve("default", active, "What did we discuss yesterday?", [])
    first = resolver.resolve("default", active, "the first one", [], options.pending)
    assert source_ids(first) == {options.pending.candidates[0].sources[0]}
    other = resolver.resolve("default", active, "No, the other one.", [], first.pending)
    assert source_ids(other) != source_ids(first)
    story = resolver.resolve("default", active, "the story", [], options.pending)
    assert source_ids(story) == {"story"}
    uncertain = resolver.resolve("default", active, "yes", [], options.pending)
    assert uncertain.response == options.response


def test_no_match_or_vague_request_accepts_another_clue(archive):
    settings, memory, index, resolver, active, conversations = archive
    vague = resolver.resolve("default", active, "Remember that thing?", [])
    assert vague.pending and vague.response
    refined = resolver.resolve("default", active, "the garden rainwater tank", [], vague.pending)
    assert source_ids(refined) == {"garden"}
    missing = resolver.resolve("default", active, "Remember Sapphire two days ago?", [])
    assert missing.response and missing.pending
    corrected = resolver.resolve("default", active, "Actually yesterday", [], missing.pending)
    assert source_ids(corrected) == {"story"}


def test_new_request_date_overrides_previous_selection(archive):
    settings, memory, index, resolver, active, conversations = archive
    old = resolver.resolve("default", active, "Remember Sapphire three days ago?", [])
    assert source_ids(old) == {"old-story"}
    new = resolver.resolve("default", active, "Remember Sapphire yesterday?", [], old.pending)
    assert source_ids(new) == {"story"}


def test_contextual_reference_uses_recent_human_topic(archive):
    settings, memory, index, resolver, active, conversations = archive
    history = [
        Message(role="user", content="I want to revisit the garden project."),
        Message(role="assistant", content="An unrelated Sapphire robot idea."),
    ]
    result = resolver.resolve("default", active, "Remember that thing yesterday?", history)
    assert source_ids(result) == {"garden"}
    # A broad date-only question should not silently inherit today's topic.
    all_day = resolver.resolve("default", active, "What did we discuss yesterday?", history)
    assert candidate_ids(all_day) == {"story", "garden"}


def test_expiry_clear_cancel_and_new_topic_do_not_reuse_choices(archive):
    settings, memory, index, resolver, active, conversations = archive
    options = resolver.resolve("default", active, "What did we discuss yesterday?", [])
    for pending in (
        replace(options.pending, expires_at=NOW),
        replace(options.pending, active="old-active"),
    ):
        result = resolver.resolve("default", active, "the first one", [], pending)
        assert not result.handled and result.pending is None
    result = resolver.resolve(
        "default", active, "Tell me about the first train tomorrow.", [], options.pending
    )
    assert not result.handled
    cancel = resolver.resolve("default", active, "never mind", [], options.pending)
    assert cancel.pending is None and cancel.response


def test_deleted_superseded_and_other_owner_evidence_is_not_recalled(archive):
    settings, memory, index, resolver, active, conversations = archive
    options = resolver.resolve("default", active, "What did we discuss yesterday?", [])
    source = options.pending.candidates[0].sources[0]
    with memory.connect() as db:
        db.execute("UPDATE chunks SET superseded=1 WHERE exchange=?", (source,))
    assert not resolver.resolve("default", active, "the first one", [], options.pending).excerpts
    second = options.pending.candidates[1]
    memory.delete("default", second.conversation)
    assert not resolver.resolve("default", active, "the second one", [], options.pending).excerpts
    private = memory.start_conversation("someone-else")
    memory.append(
        "someone-else",
        private,
        Exchange("secret", "human", "Sapphire private details", "private reply"),
    )
    assert not resolver.resolve("default", active, "Remember private details?", []).excerpts
    assert index.recall_evidence("someone-else", conversations["old"], ("old-story",)) == []


def test_summary_topic_search_is_grounded_in_original_sources(archive):
    settings, memory, index, resolver, active, conversations = archive
    conversation = conversations["garden"]
    # The exact phrase appears only in a source-linked summary.
    with memory.connect() as db:
        seq = db.execute("SELECT sequence FROM exchanges WHERE id='garden'").fetchone()[0]
    summary = Summary(
        notes=[
            Note(
                kind="topic",
                text="Water collection arrangement for the garden.",
                sources=["garden"],
                scope="real",
                status="tentative",
            )
        ]
    )
    assert index.save("default", conversation, "garden", 0, seq, summary)
    result = resolver.resolve("default", active, "Remember our water collection arrangement?", [])
    assert source_ids(result) == {"garden"}
    assert "rainwater tank" in result.excerpts[0]["text"]
    assert "Water collection" not in result.excerpts[0]["text"]


def make_brain(settings, requests):
    def complete(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert (
            estimate(
                [Message.model_validate(m) for m in payload["messages"]], payload.get("tools", [])
            )
            <= settings.model_context_tokens
            - payload["max_tokens"]
            - settings.context_margin_tokens
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Let's continue from that source.",
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(complete))
    brain = Brain(
        settings,
        client,
        Mock(),
        Mock(synthesize=Mock(return_value=(np.zeros(10, dtype=np.float32), 24000))),
    )
    brain.recall.clock = lambda: NOW
    return brain, client


def test_server_clarification_selection_is_archived_and_rollback_safe(archive):
    settings, memory, index, resolver, active, conversations = archive
    requests = []
    brain, client = make_brain(settings, requests)
    with client:
        question = brain.turn("pi", b"What did we discuss yesterday?", text_only=True)
        assert "Which one" in question.text and not requests
        before = brain.sessions["pi"].pending_recall
        previous = brain.sessions["pi"].history.copy()
        brain.tts.synthesize.side_effect = RuntimeError("audio failed")
        brain.stt.recognize.return_value = "the story"
        from iris_droid.protocol import write_wav

        with pytest.raises(RuntimeError):
            brain.turn("pi", write_wav(b"\0\0" * 1600, 16000))
        assert brain.sessions["pi"].pending_recall == before
        assert brain.sessions["pi"].history == previous
        selected = brain.turn("pi", b"the story", text_only=True)
        assert selected.text == "Let's continue from that source."
        prompt = requests[-1]["messages"][0]["content"]
        assert "greenhouse" in prompt and "desert planet" not in prompt
        assert "rainwater tank" not in prompt
        assert len(memory.export("default")["exchanges"]) == 5


def test_server_pending_options_rollback_when_archive_commit_fails(archive):
    settings, memory, index, resolver, active, conversations = archive
    brain, client = make_brain(settings, [])
    with (
        client,
        patch.object(brain.memory, "append", side_effect=sqlite3.OperationalError("disk full")),
    ):
        with pytest.raises(sqlite3.Error):
            brain.turn("pi", b"What did we discuss yesterday?", text_only=True)
        assert brain.sessions["pi"].pending_recall is None
        assert brain.sessions["pi"].history == []


@pytest.mark.parametrize(
    "update", [{"memory_enabled": False}, {"history_turns": 0}, {"memory_retrieval_chars": 0}]
)
def test_disabled_recall_does_not_intercept_normal_chat(archive, update):
    settings, memory, index, resolver, active, conversations = archive
    settings = settings.model_copy(update=update)
    requests = []
    brain, client = (
        make_brain(settings, requests) if update.get("memory_enabled", True) else (None, None)
    )
    if brain is None:
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"choices": [{"message": {"role": "assistant", "content": "normal"}}]}
                )
            )
        ) as client:
            brain = Brain(settings, client, Mock(), Mock())
            assert brain.turn("pi", b"Remember that thing?", text_only=True).text == "normal"
        return
    with client:
        assert (
            brain.turn("pi", b"Remember that thing?", text_only=True).text
            == "Let's continue from that source."
        )
        assert brain.sessions["pi"].pending_recall is None


def test_long_source_excerpt_includes_the_requested_detail(archive):
    settings, memory, index, resolver, active, conversations = archive
    long = memory.start_conversation("default")
    memory.append(
        "default",
        long,
        Exchange(
            "long",
            "human",
            "Background. " * 100 + "The submarine is named Indigo.",
            "We can return to Indigo later.",
        ),
    )
    result = resolver.resolve("default", active, "Remember Indigo?", [])
    assert source_ids(result) == {"long"}
    assert "submarine is named Indigo" in result.excerpts[0]["text"]
    assert "…" in result.excerpts[0]["text"]


def test_source_date_filter_uses_local_day_and_exclusive_end(archive):
    settings, memory, index, resolver, active, conversations = archive
    with memory.connect() as db:
        db.execute("UPDATE exchanges SET created_at='2026-07-02T23:30:00+00:00' WHERE id='story'")
        db.execute(
            "UPDATE exchanges SET created_at='2026-07-03T23:00:00+00:00' WHERE id='old-story'"
        )
    # July 3 in London runs from July 2 at 23:00 UTC to July 3 at 23:00 UTC.
    resolver.clock = lambda: datetime(2026, 7, 4, 0, 30, tzinfo=UTC)
    result = resolver.resolve("default", active, "Remember Sapphire yesterday?", [])
    assert source_ids(result) == {"story"}


def test_summary_generation_date_does_not_replace_original_date(archive):
    settings, memory, index, resolver, active, conversations = archive
    conversation = conversations["old"]
    with memory.connect() as db:
        seq = db.execute("SELECT sequence FROM exchanges WHERE id='old-story'").fetchone()[0]
    summary = Summary(
        notes=[
            Note(
                kind="topic",
                text="Fictional adventure on a distant world.",
                sources=["old-story"],
                scope="fictional",
                status="tentative",
            )
        ]
    )
    assert index.save("default", conversation, "old-story", 0, seq, summary)
    assert source_ids(
        resolver.resolve("default", active, "Remember the adventure three days ago?", [])
    ) == {"old-story"}
    assert not resolver.resolve("default", active, "Remember the adventure yesterday?", []).excerpts


def test_selected_recall_excludes_unrelated_active_notes(archive):
    from iris_memory.context import MemoryContext

    settings, memory, index, resolver, active, conversations = archive
    memory.append(
        "default",
        active,
        Exchange(
            "current",
            "human",
            "Today we changed the robot's setting to a mountain.",
            "Mountain version.",
        ),
    )
    with memory.connect() as db:
        seq = db.execute("SELECT sequence FROM exchanges WHERE id='current'").fetchone()[0]
    assert index.save(
        "default",
        active,
        "current",
        0,
        seq,
        Summary(
            notes=[
                Note(
                    kind="topic",
                    text="Mountain version.",
                    sources=["current"],
                    scope="fictional",
                    status="tentative",
                )
            ]
        ),
    )
    selected = resolver.resolve("default", active, "Remember Sapphire three days ago?", [])
    context = MemoryContext(settings, index, Mock()).prepare(
        "default", active, "Remember Sapphire three days ago?", [], "Iris", [], selected.excerpts
    )
    assert "desert planet" in context and "Mountain version" not in context


def test_partial_topic_match_asks_for_confirmation(archive):
    settings, memory, index, resolver, active, conversations = archive
    result = resolver.resolve("default", active, "Remember the garden submarine?", [])
    assert not result.excerpts and result.pending and "possible discussion" in result.response
    assert source_ids(resolver.resolve("default", active, "yes", [], result.pending)) == {"garden"}


def test_trivial_selection_is_not_a_new_recall_candidate(archive):
    settings, memory, index, resolver, active, conversations = archive
    memory.append(
        "default",
        active,
        Exchange("selection", "human", "the first one", "Iris repeated a past story."),
    )
    assert not resolver.resolve("default", active, "What did we discuss today?", []).excerpts
    assert (
        index.recall_sources(
            "default", [], NOW.replace(hour=0), NOW.replace(hour=0) + timedelta(days=1)
        )
        == []
    )


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("What were we talking about yesterday?", True),
        ("What did we decide about the garden?", True),
        ("Where did we leave the story last week?", True),
        ("Let's continue our story from yesterday.", True),
        ("How did we calculate that number?", False),
        ("I told Jane we talked about the garden.", False),
        ("I remember a story I read as a child.", False),
        ("Remember to buy milk.", False),
    ],
)
def test_recall_intent_leaves_ordinary_conversation_alone(phrase, expected):
    from iris_memory.recall import is_recall_request

    assert is_recall_request(phrase) is expected


def test_selected_recall_does_not_ask_model_to_guess_after_context_failure(archive):
    settings, memory, index, resolver, active, conversations = archive
    requests = []
    brain, client = make_brain(settings, requests)
    with (
        client,
        patch.object(
            brain.memory_context,
            "prepare",
            side_effect=sqlite3.OperationalError("derived data unavailable"),
        ),
    ):
        reply = brain.turn("pi", b"Remember the garden project yesterday?", text_only=True)
        assert "can't load its details" in reply.text
        assert not [
            r
            for r in requests
            if not r["messages"][0]["content"].startswith("Extract memory metadata")
        ]
