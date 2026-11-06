"""Hierarchy semantics: facts, corrections, human attention and source lifecycle."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import numpy as np
import pytest

from iris_chat_engines.messages import Message
from iris_droid.server import Brain
from iris_memory import AUTOMATIC_EVENT, Exchange, SQLiteMemory
from iris_memory.controls import MemoryPolicyError
from iris_memory.details import DetailCommand, Details
from iris_memory.hierarchy import Hierarchy
from iris_settings import Settings


@pytest.fixture
def setup(tmp_path):
    settings = Settings(memory_enabled=True, memory_database=tmp_path / "memory.db")
    archive = SQLiteMemory(settings.memory_database)
    conversation = archive.active_conversation("default")
    backend = Mock()
    return settings, archive, conversation, Hierarchy(archive, settings, backend), backend


def fact(value="Dave", **changes):
    return {
        "key": "name",
        "value": value,
        "kind": "identity",
        "scope": "real",
        "lifetime": "stable",
        "importance": "high",
        "status": "confirmed",
        "evidence": f"My name is {value}",
        **changes,
    }


def topic(label="Sapphire story", **changes):
    return {
        "label": label,
        "scope": "fictional",
        "status": "unchanged",
        "engaged": True,
        "evidence": f"Let's discuss the {label}",
        **changes,
    }


def prepare(setup, text, result, *, selection=""):
    _, _, conversation, hierarchy, backend = setup
    backend.complete.return_value = Message(role="assistant", content=json.dumps(result))
    return hierarchy.prepare("default", conversation, text, selection)


def commit(setup, text, result):
    _, archive, conversation, _, _ = setup
    plan = prepare(setup, text, result)
    assert plan is not None
    exchange = Exchange(str(uuid4()), "human", text, "Reply")
    archive.append(
        "default",
        conversation,
        exchange,
        hierarchy=plan,
        source_conversations=tuple(plan.source_conversations),
    )
    return exchange


def test_name_remains_core_after_gap_while_episodes_require_relevance(setup):
    _, archive, _, hierarchy, _ = setup
    name = commit(setup, "My name is Dave", {"facts": [fact()]})
    commit(
        setup,
        "I caught a bus yesterday",
        {
            "facts": [
                fact(
                    "a bus yesterday",
                    key="bus journey",
                    kind="episode",
                    lifetime="episodic",
                    importance="low",
                    evidence="I caught a bus yesterday",
                )
            ]
        },
    )
    with archive.connect() as db:
        db.execute(
            "UPDATE exchanges SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (name.id,)
        )
    context, _ = hierarchy.context("default", "Tell me about cinema", None)
    assert "Dave" in context and "bus" not in context
    context, _ = hierarchy.context("default", "That bus journey", None)
    assert "Dave" in context and "bus" in context
    assert archive.export("default")["exchanges"][0]["user_text"] == "My name is Dave"


def test_natural_correction_supersedes_before_reply_preserves_originals_and_restart(setup):
    settings, archive, _, hierarchy, _ = setup
    commit(setup, "My name is Dave", {"facts": [fact()]})
    old = hierarchy.facts("default")[0]
    text = "Actually, my name is David, not Dave"
    plan = prepare(setup, text, {"facts": [fact("David", supersedes=old["id"], evidence=text)]})
    context, _ = hierarchy.context("default", "Hello", plan)
    assert '"value": "David"' in context and '"value": "Dave"' not in context
    assert hierarchy.facts("default")[0]["value"] == "Dave"  # No premature mutation.
    archive.append(
        "default", setup[2], Exchange(str(uuid4()), "human", text, "Hi David"), hierarchy=plan
    )
    exported = archive.export("default")
    assert len(exported["exchanges"]) == 2
    assert {r["status"] for r in exported["memory_facts"]} == {"superseded", "confirmed"}
    restarted = Hierarchy(SQLiteMemory(archive.path), settings, Mock())
    assert restarted.facts("default")[0]["value"] == "David"


def test_labelled_name_natural_correction_resolves_without_broad_deletion(setup):
    _, archive, conversation, hierarchy, _ = setup
    details = Details(archive)
    details.apply(
        "default",
        details.snapshot(
            "default",
            DetailCommand("remember", "name", "Dave"),
            datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    text = "Actually, my name is David"
    commit(setup, text, {"facts": [fact("David", supersedes="detail:name", evidence=text)]})
    assert details.items("default", "name")[0]["text"] == "David"
    assert archive.revision("default", conversation)
    assert "David" in details.context("default", "hello")[0]
    assert "Dave" in json.dumps(
        archive.export("default")["remembered_details"]
    )  # Original curated value.
    turn = details.snapshot(
        "default", DetailCommand("forget", "name"), datetime.now(UTC) + timedelta(minutes=1)
    )
    details.apply("default", turn)
    assert hierarchy.facts("default") == []  # Label deletion cascades to overrides too.
    assert archive.export("default")["exchanges"] == []


def test_story_closure_and_film_switch_update_different_signals(setup):
    _, archive, _, hierarchy, _ = setup
    first = commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = hierarchy.topics("default")[0]
    text = "I finished that story ages ago, let's talk about The Terminator"
    commit(
        setup,
        text,
        {
            "topics": [
                topic(
                    target=story["id"],
                    status="closed",
                    engaged=True,
                    evidence="I finished that story ages ago",
                ),
                topic("The Terminator", scope="real", evidence="let's talk about The Terminator"),
            ]
        },
    )
    rows = {r["label"]: r for r in hierarchy.topics("default")}
    assert rows["Sapphire story"]["status"] == "closed"
    assert rows["Sapphire story"]["last_engaged_at"] == story["last_engaged_at"]
    assert rows["The Terminator"]["last_engaged_at"] > story["last_engaged_at"]
    assert archive.export("default")["exchanges"][0]["id"] == first.id
    assert "Sapphire" not in hierarchy.query(text, None)


def test_switch_guard_rejects_old_topic_engagement_even_if_model_misclassifies(setup):
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    text = "I finished that story ages ago, let's talk about The Terminator"
    plan = prepare(setup, text, {"topics": [topic(target=story["id"], evidence=text)]})
    assert plan is not None and not plan.interpretation.topics[0].engaged


def test_old_topic_resumption_refreshes_engagement_not_source_date(setup):
    _, archive, _, hierarchy, _ = setup
    first = commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    with archive.connect() as db:
        db.execute(
            "UPDATE exchanges SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (first.id,)
        )
    story = hierarchy.topics("default")[0]
    text = "Let's return to that Sapphire story and change the ending"
    commit(setup, text, {"topics": [topic(target=story["id"], evidence=text)]})
    current = hierarchy.topics("default")[0]
    assert current["created_at"] == "2000-01-01T00:00:00+00:00"
    assert current["last_engaged_at"] > story["last_engaged_at"]


@pytest.mark.parametrize(
    "text", ["I'm bored of the Sapphire story", "Not that story", "We can discuss it later"]
)
def test_dismissal_negation_and_deferral_do_not_refresh(setup, text):
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    commit(setup, text, {"topics": [topic(target=story["id"], evidence=text)]})
    assert setup[3].topics("default")[0]["last_engaged_at"] == story["last_engaged_at"]


def test_short_selection_requires_resolved_recall_context(setup):
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    result = {"topics": [topic(target=story["id"], evidence="yes")]}
    assert not prepare(setup, "yes", result).interpretation.topics[0].engaged
    assert (
        prepare(setup, "yes", result, selection="Sapphire story").interpretation.topics[0].engaged
    )


def test_discussing_closed_work_does_not_implicitly_reopen_it(setup):
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    commit(
        setup,
        "The Sapphire story is finished",
        {
            "topics": [
                topic(
                    target=story["id"], status="closed", evidence="The Sapphire story is finished"
                )
            ]
        },
    )
    text = "Let's discuss the Sapphire story reviews"
    commit(setup, text, {"topics": [topic(target=story["id"], evidence=text)]})
    assert setup[3].topics("default")[0]["status"] == "closed"


@pytest.mark.parametrize(
    "bad",
    [
        {"facts": [fact("Made up", evidence="My name is Dave")]},
        {"facts": [fact(supersedes="invented")]},
        {"topics": [topic(target="invented", evidence="My name is Dave")]},
        {"topics": [topic(status="closed", evidence="My name is Dave")]},
        {"facts": [], "unexpected": "bad"},
    ],
)
def test_invalid_model_annotations_fail_closed_without_saving(setup, bad):
    assert prepare(setup, "My name is Dave", bad) is None
    assert not setup[3].facts("default") and not setup[3].topics("default")


def test_fictional_identity_not_promoted_to_real_core(setup):
    text = "In this story, my name is Dave"
    commit(setup, text, {"facts": [fact(evidence=text)]})
    assert setup[3].facts("default")[0]["status"] == "tentative"
    assert "Dave" not in setup[3].context("default", "hello", None)[0]


def test_model_and_retrieval_do_not_refresh_human_engagement(setup):
    _, archive, conversation, hierarchy, _ = setup
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    old = hierarchy.topics("default")[0]["last_engaged_at"]
    hierarchy.rank_sources("default", "Sapphire", [])
    archive.append(
        "default",
        conversation,
        Exchange(str(uuid4()), "automatic", AUTOMATIC_EVENT, "More Sapphire?"),
    )
    assert hierarchy.topics("default")[0]["last_engaged_at"] == old


def test_atomic_stale_private_and_automatic_metadata_commits(setup):
    _, archive, conversation, hierarchy, _ = setup
    plan = prepare(setup, "My name is Dave", {"facts": [fact()]})
    archive.append("default", conversation, Exchange(str(uuid4()), "human", "Intervening", "Reply"))
    with pytest.raises(MemoryPolicyError):
        archive.append(
            "default",
            conversation,
            Exchange("stale", "human", "My name is Dave", "Hi"),
            hierarchy=plan,
        )
    assert archive.revision("default", conversation) != "stale"
    assert not hierarchy.facts("default")
    plan = prepare(setup, "My name is Dave", {"facts": [fact()]})
    with pytest.raises(MemoryPolicyError):
        archive.append(
            "default",
            conversation,
            Exchange("auto", "automatic", AUTOMATIC_EVENT, "Hi"),
            hierarchy=plan,
        )
    archive.set_private("default", True)
    with patch.object(
        hierarchy.backend, "complete", side_effect=AssertionError("private inference")
    ):
        assert hierarchy.prepare("default", conversation, "My name is Private") is None


def test_forgetting_cascades_hierarchy_and_never_revives_old_name(setup):
    _, archive, conversation, hierarchy, _ = setup
    commit(setup, "My name is Dave", {"facts": [fact()]})
    old = hierarchy.facts("default")[0]
    text = "Actually, my name is David"
    commit(setup, text, {"facts": [fact("David", supersedes=old["id"], evidence=text)]})
    archive.delete("default", conversation)
    assert not hierarchy.facts("default") and not hierarchy.topics("default")


def test_core_budget_and_owner_isolation(setup):
    commit(setup, "My name is Dave", {"facts": [fact()]})
    assert setup[3].context("other", "name", None) == ("", set())
    assert setup[3].context("default", "hello", None, limit=10) == ("", set())
    assert setup[3].facts("other") == []


def test_schema_five_upgrade_preserves_sources_and_private_policy(tmp_path):
    path = tmp_path / "v5.db"
    archive = SQLiteMemory(path)
    conversation = archive.active_conversation("default")
    archive.append("default", conversation, Exchange("source", "human", "Original", "Reply"))
    archive.set_private("default", True)
    with archive.connect() as db:
        for table in ("detail_supersessions", "memory_attention", "memory_topics", "memory_facts"):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=5")
    upgraded = SQLiteMemory(path)
    assert upgraded.policy("default") == (True, 1)
    assert upgraded.revision("default", conversation) == "source"
    assert upgraded.export("default")["schema_version"] == 6


@pytest.fixture
def server(tmp_path):
    settings = Settings(memory_enabled=True, memory_database=tmp_path / "server.db")
    requests = []
    annotation = {"facts": [], "topics": []}

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        system = payload["messages"][0]["content"]
        content = (
            json.dumps(annotation)
            if system.startswith("Extract memory metadata")
            else '{"notes": []}'
            if system.startswith("Summarise these original")
            else "A reply."
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        brain = Brain(
            settings,
            client,
            Mock(),
            Mock(synthesize=Mock(return_value=(np.zeros(8, dtype=np.float32), 24000))),
        )
        yield brain, client, requests, annotation


def say(brain, text):
    return brain.turn("pi", text.encode(), text_only=True)


def test_server_natural_correction_reaches_reply_before_commit_and_survives_restart(server):
    brain, client, requests, annotation = server
    annotation["facts"] = [fact()]
    say(brain, "My name is Dave")
    old = brain.memory_context.hierarchy.facts("default")[0]
    text = "Actually, my name is David"
    annotation["facts"] = [fact("David", evidence=text, supersedes=old["id"])]
    say(brain, text)
    prompt = requests[-1]["messages"][0]["content"]
    assert '"value": "David"' in prompt and '"value": "Dave"' not in prompt
    restarted = Brain(brain.settings, client, brain.stt, brain.tts)
    annotation["facts"] = []
    say(restarted, "Hello again")
    assert '"value": "David"' in requests[-1]["messages"][0]["content"]
    assert len(brain.memory.export("default")["exchanges"]) == 3


def test_server_tts_and_storage_failures_do_not_commit_corrections_or_engagement(server):
    brain, _, _, annotation = server
    annotation["facts"] = [fact()]
    say(brain, "My name is Dave")
    old = brain.memory_context.hierarchy.facts("default")[0]
    annotation["facts"] = [fact("David", evidence="My name is David", supersedes=old["id"])]
    with patch.object(brain, "_reply", side_effect=ValueError("tts failed")):
        with pytest.raises(ValueError):
            say(brain, "My name is David")
    assert brain.memory_context.hierarchy.facts("default")[0]["value"] == "Dave"
    annotation["facts"] = []
    annotation["topics"] = [topic()]
    with patch.object(brain.memory, "append", side_effect=sqlite3.OperationalError("locked")):
        with pytest.raises(sqlite3.OperationalError):
            say(brain, "Let's discuss the Sapphire story")
    assert not brain.memory_context.hierarchy.topics("default")


def test_server_private_disabled_and_automatic_paths_do_not_interpret(server):
    brain, client, requests, annotation = server
    disabled = Brain(
        brain.settings.model_copy(update={"memory_hierarchy_enabled": False}),
        client,
        brain.stt,
        brain.tts,
    )
    say(disabled, "My name is Dave")
    assert not any(
        r["messages"][0]["content"].startswith("Extract memory metadata") for r in requests
    )
    requests.clear()
    say(brain, "private mode on")
    say(brain, "My name is Private")
    assert not any(
        r["messages"][0]["content"].startswith("Extract memory metadata") for r in requests
    )
    assert not brain.memory.export("default")["memory_facts"]
    say(brain, "private mode off")
    say(brain, "companion mode on")
    requests.clear()
    brain.turn("pi", b"", text_only=True, companion_idle=True)
    assert len(requests) == 1 and not requests[0].get("tools")
    assert not requests[0]["messages"][0]["content"].startswith("Extract memory metadata")


def test_model_failure_preserves_existing_core_and_chat(server):
    brain, _, requests, annotation = server
    annotation["facts"] = [fact()]
    say(brain, "My name is Dave")
    annotation.clear()
    annotation["unsupported"] = "Don't use this"
    assert say(brain, "Tell me about the moon").text == "A reply."
    assert '"value": "Dave"' in requests[-1]["messages"][0]["content"]


def test_metadata_requests_have_no_tools_and_respect_input_budget(server):
    from iris_chat_engines.budget import estimate

    brain, _, requests, _ = server
    say(brain, "Tell me about the moon")
    meta = next(
        r for r in requests if r["messages"][0]["content"].startswith("Extract memory metadata")
    )
    assert not meta.get("tools")
    assert meta["max_tokens"] == brain.settings.memory_summary_tokens
    assert estimate([Message.model_validate(m) for m in meta["messages"]], []) <= (
        brain.settings.model_context_tokens
        - meta["max_tokens"]
        - brain.settings.context_margin_tokens
    )
    assert "Extract memory metadata" not in requests[-1]["messages"][0]["content"]


def test_engagement_boost_ranks_old_relevant_sources_above_untouched_sources(setup):
    _, archive, _, hierarchy, _ = setup
    first = commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = hierarchy.topics("default")[0]
    with archive.connect() as db:
        db.execute(
            "UPDATE exchanges SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (first.id,)
        )
    commit(
        setup,
        "The Sapphire story ending needs work",
        {"topics": [topic(target=story["id"], evidence="The Sapphire story ending needs work")]},
    )
    rows = [
        {"text": "A Sapphire story", "created_at": "2000-01-01T00:00:00+00:00", "exchange": "old"},
        {"text": "A story", "created_at": datetime.now(UTC).isoformat(), "exchange": "recent"},
    ]
    assert hierarchy.rank_sources("default", "Sapphire story", rows)[0]["exchange"] == "old"


def test_historical_fact_does_not_replace_current_name(setup):
    commit(setup, "My name is Dave", {"facts": [fact()]})
    commit(
        setup,
        "I used to be called David",
        {"facts": [fact("David", status="historical", evidence="I used to be called David")]},
    )
    context, _ = setup[3].context("default", "name", None)
    assert '"value": "Dave"' in context and '"value": "David"' not in context
    assert len(setup[1].export("default")["memory_facts"]) == 2


def test_high_importance_stable_preference_has_small_core_allowance(setup):
    text = "I prefer concise replies"
    commit(
        setup,
        text,
        {
            "facts": [
                fact("concise replies", key="reply preference", kind="preference", evidence=text)
            ]
        },
    )
    assert "concise replies" in setup[3].context("default", "hello", None)[0]


def test_inspection_is_read_only_and_reports_original_and_engagement_dates(setup, tmp_path, capsys):
    from iris_memory.cli import main

    settings, archive, _, hierarchy, _ = setup
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    config = tmp_path / "config.json"
    config.write_text(settings.model_dump_json())
    before = archive.export("default")
    assert main(["--config", str(config), "inspect", "--query", "Sapphire"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["topics"][0]["created_at"] and output["topics"][0]["last_engaged_at"]
    assert archive.export("default") == before


def test_unrelated_metadata_candidates_do_not_create_cross_conversation_dependencies(setup):
    _, archive, _, hierarchy, _ = setup
    commit(setup, "My name is Dave", {"facts": [fact()]})
    other = archive.start_conversation("default")
    setup = (setup[0], archive, other, hierarchy, setup[4])
    plan = prepare(setup, "Hello", {})
    assert plan is not None and not plan.source_conversations


def test_schema_migration_failure_is_atomic_and_retryable(setup):
    _, archive, _, _, _ = setup
    with archive.connect() as db:
        for table in ("detail_supersessions", "memory_attention", "memory_topics", "memory_facts"):
            db.execute(f"DROP TABLE {table}")
        db.execute("CREATE TABLE memory_topics (bad TEXT)")
        db.execute("PRAGMA user_version=5")
    with pytest.raises(sqlite3.OperationalError):
        SQLiteMemory(archive.path)
    with archive.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_facts'").fetchone()
        db.execute("DROP TABLE memory_topics")
    assert SQLiteMemory(archive.path).export("default")["schema_version"] == 6


def test_uncertain_correction_cannot_resurrect_original_explicit_value(setup):
    _, archive, _, hierarchy, _ = setup
    details = Details(archive)
    details.apply(
        "default",
        details.snapshot(
            "default",
            DetailCommand("remember", "name", "Dave"),
            datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    text = "My name is David"
    commit(setup, text, {"facts": [fact("David", supersedes="detail:name", evidence=text)]})
    current = hierarchy.facts("default")[0]
    text = "Perhaps my name is Danny"
    assert (
        prepare(
            setup,
            text,
            {"facts": [fact("Danny", status="tentative", supersedes=current["id"], evidence=text)]},
        )
        is None
    )
    assert details.items("default", "name")[0]["text"] == "David"


def test_model_cannot_reopen_a_completed_project_by_merely_discussing_it(setup):
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    commit(
        setup,
        "The Sapphire story is finished",
        {
            "topics": [
                topic(
                    target=story["id"], status="closed", evidence="The Sapphire story is finished"
                )
            ]
        },
    )
    text = "Let's discuss reviews of the Sapphire story"
    commit(setup, text, {"topics": [topic(target=story["id"], status="open", evidence=text)]})
    assert setup[3].topics("default")[0]["status"] == "closed"


def test_clear_context_blocks_episode_reinjection_but_retains_core_identity(setup):
    _, archive, _, hierarchy, _ = setup
    commit(setup, "My name is Dave", {"facts": [fact()]})
    commit(
        setup,
        "I caught a bus yesterday",
        {
            "facts": [
                fact(
                    "a bus yesterday",
                    key="bus journey",
                    kind="episode",
                    lifetime="episodic",
                    evidence="I caught a bus yesterday",
                )
            ]
        },
    )
    archive.start_conversation("default")
    context, _ = hierarchy.context("default", "bus journey", None)
    assert "Dave" in context and "a bus yesterday" not in context
    assert "a bus yesterday" in hierarchy.context("default", "Remember the bus journey?", None)[0]


def test_prepared_annotations_must_match_the_committed_human_source(setup):
    plan = prepare(setup, "My name is Dave", {"facts": [fact()]})
    with pytest.raises(ValueError):
        setup[1].append(
            "default",
            setup[2],
            Exchange("wrong-source", "human", "Different text", "Reply"),
            hierarchy=plan,
        )
    assert not setup[3].facts("default")
    assert setup[1].revision("default", setup[2]) is None


def test_incidental_new_topic_mention_does_not_create_activity(setup):
    commit(
        setup,
        "I don't want to discuss the Sapphire story",
        {"topics": [topic(engaged=False, evidence="I don't want to discuss the Sapphire story")]},
    )
    assert setup[3].topics("default") == []


def test_explicit_recall_uses_attention_for_ordering_without_inventing_match_confidence(setup):
    from iris_memory.index import MemoryIndex
    from iris_memory.recall import RecallResolver

    _, archive, _, hierarchy, _ = setup
    commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    index = MemoryIndex(archive)
    index.hierarchy = hierarchy
    rows = index.recall_sources("default", ["Sapphire", "story"], None, None)
    assert any(r.get("hierarchy_activity", 0) > 0 for r in rows)
    resolver = RecallResolver(index, "Europe/London")
    candidates = resolver._candidates(
        [
            {
                "exchange": "old",
                "conversation": "old",
                "created_at": "2000-01-01T00:00:00+00:00",
                "user_text": "Sapphire story",
                "hierarchy_activity": 0.3,
            },
            {
                "exchange": "new",
                "conversation": "new",
                "created_at": "2026-01-01T00:00:00+00:00",
                "user_text": "Amber story",
                "hierarchy_activity": 0,
            },
        ],
        ["story"],
    )
    assert candidates[0].conversation == "old"
    assert candidates[0].score == candidates[1].score == 1


def test_corrected_label_is_ranked_and_dated_by_current_value(setup):
    _, archive, _, _, _ = setup
    details = Details(archive)
    details.apply(
        "default",
        details.snapshot(
            "default",
            DetailCommand("remember", "favourite colour", "blue"),
            datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    text = "Actually, I prefer red"
    commit(
        setup,
        text,
        {
            "facts": [
                fact(
                    "red",
                    key="favourite colour",
                    kind="preference",
                    evidence=text,
                    supersedes="detail:favourite colour",
                )
            ]
        },
    )
    item = details.items("default", "favourite colour")[0]
    assert item["text"] == "red"
    assert item["updated_at"] == archive.export("default")["exchanges"][-1]["created_at"]
    assert "red" in details.context("default", "red")[0]
    assert archive.export("default")["remembered_details"][0]["text"] == "blue"


def test_closed_topic_notes_do_not_compete_with_new_subject_unless_requested(setup):
    source = commit(setup, "Let's discuss the Sapphire story", {"topics": [topic()]})
    story = setup[3].topics("default")[0]
    commit(
        setup,
        "The Sapphire story is finished",
        {
            "topics": [
                topic(
                    target=story["id"], status="closed", evidence="The Sapphire story is finished"
                )
            ]
        },
    )
    notes = [
        {"text": "The Sapphire story needs an ending", "sources": json.dumps([source.id])},
        {"text": "The Terminator film has a time loop", "sources": "[]"},
    ]
    chosen = setup[3].select_notes("default", "Let's talk about The Terminator", notes)
    assert len(chosen) == 1 and "Terminator" in chosen[0]["text"]
    assert len(setup[3].select_notes("default", "Sapphire story reviews", notes)) == 2


def test_forgetting_a_correction_does_not_revive_superseded_curated_name(setup):
    _, archive, _, hierarchy, _ = setup
    details = Details(archive)
    details.apply(
        "default",
        details.snapshot(
            "default",
            DetailCommand("remember", "name", "Dave"),
            datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    correction_conversation = archive.start_conversation("default")
    current = (setup[0], archive, correction_conversation, hierarchy, setup[4])
    text = "Actually, my name is David"
    commit(current, text, {"facts": [fact("David", evidence=text, supersedes="detail:name")]})
    archive.delete("default", correction_conversation)
    assert not details.items("default", "name")[0]["available"]
    assert details.context("default", "hello")[0] == ""
    assert "Dave" not in hierarchy.context("default", "hello", None)[0]
    pending = details.snapshot(
        "default",
        DetailCommand("correct", "name", "Danny"),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    details.apply("default", pending)
    assert details.items("default", "name")[0]["text"] == "Danny"
    assert details.items("default", "name")[0]["available"]
