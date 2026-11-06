"""Final memory controls, read-only tools, lineage, and companion lifecycle checks."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import numpy as np
import pytest

from iris_droid.server import Brain
from iris_memory import Exchange, SQLiteMemory
from iris_memory.companion import CompanionMemory
from iris_memory.controls import MemoryPolicyError
from iris_memory.details import Details, detail_command
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_memory.search import SearchArguments, search_memory
from iris_settings import Settings


@pytest.fixture
def setup(tmp_path):
    settings = Settings(memory_enabled=True, memory_database=tmp_path / "memory.db")
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        content = (
            '{"notes": []}'
            if payload["messages"][0]["content"].startswith("Summarise")
            else "We can continue the spaceship story."
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": content,
                        }
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        brain = Brain(
            settings,
            client,
            Mock(),
            Mock(synthesize=Mock(return_value=(np.zeros(10, dtype=np.float32), 24000))),
        )
        yield brain, client, requests


def turn(brain, text, session="pi"):
    return brain.turn(session, text.encode(), text_only=True)


def remember(brain, label="story captain", value="Fictional Captain Violet"):
    turn(brain, f"remember {label} as {value}")
    return turn(brain, "confirm memory change")


def add(
    archive, conversation, text="We are building a cardboard spaceship.", owner="default", **kwargs
):
    exchange = Exchange(str(uuid4()), "human", text, "Try blue cardboard.")
    archive.append(owner, conversation, exchange, **kwargs)
    return exchange


@pytest.mark.parametrize(
    "text",
    [
        "Remember the earlier story?",
        "remember to buy milk",
        "forget everything",
        "the character says remember captain as Violet",
        "correct that",
        "yes",
    ],
)
def test_no_implicit_detail_mutation(text):
    assert detail_command(text) is None


def test_details_require_confirmation_and_survive_restart_without_archiving_controls(setup):
    brain, client, requests = setup
    reply = turn(brain, "remember story captain as Fictional Captain Violet")
    assert "confirm memory change" in reply.text
    assert brain.details.items("default") == []
    turn(brain, "confirm memory change")
    assert brain.memory.export("default")["exchanges"] == []
    restarted = Brain(brain.settings, client, brain.stt, brain.tts)
    assert "Fictional Captain Violet" in turn(restarted, "list remembered details").text
    turn(restarted, "Continue our story")
    assert "Fictional Captain Violet" in requests[-1]["messages"][0]["content"]
    assert restarted.details.items("other") == []


def test_correct_detail_confirms_and_removes_conversations_using_old_value(setup):
    brain, client, requests = setup
    remember(brain)
    turn(brain, "Start a spaceship story")
    old = brain.memory.active_conversation("default")
    turn(brain, "clear context")
    unused = brain.memory.active_conversation("default")
    prompt = turn(brain, "correct story captain to Fictional Captain Emerald")
    assert "1 whole saved conversations" in prompt.text
    assert brain.memory.revision("default", old)
    turn(brain, "confirm memory change")
    exported = json.dumps(brain.memory.export("default"))
    assert "Violet" not in exported
    assert "Emerald" in exported
    assert old not in {c["id"] for c in brain.memory.conversations("default")}
    assert brain.memory.active_conversation("default") == unused
    turn(brain, "What is our story captain called?")
    assert "Emerald" in requests[-1]["messages"][0]["content"]


def test_forget_detail_removes_transitive_copies_and_derived_rows(setup):
    brain, _, _ = setup
    remember(brain)
    turn(brain, "Start a spaceship story")
    first = brain.memory.active_conversation("default")
    second = brain.memory.start_conversation("default")
    add(brain.memory, second, source_conversations=(first,))
    third = brain.memory.start_conversation("default")
    add(brain.memory, third, source_conversations=(second,))
    unrelated = brain.memory.start_conversation("other")
    add(brain.memory, unrelated, owner="other")
    prompt = turn(brain, "forget the detail about story captain")
    assert "3 whole saved conversations" in prompt.text
    turn(brain, "confirm memory change")
    assert brain.details.items("default") == []
    assert not brain.memory.export("default")["exchanges"]
    with brain.memory.connect() as db:
        assert db.execute("SELECT count(*) FROM chunks WHERE owner='default'").fetchone()[0] == 0
        assert (
            db.execute("SELECT count(*) FROM detail_uses WHERE owner='default'").fetchone()[0] == 0
        )
    assert brain.memory.revision("other", unrelated)


@pytest.mark.parametrize("change", ["unrelated", "cancel", "expired", "other_client"])
def test_detail_confirmation_invalidation(setup, change):
    brain, _, _ = setup
    turn(brain, "remember story captain as Violet")
    if change == "unrelated":
        turn(brain, "Hello")
    elif change == "cancel":
        turn(brain, "cancel memory change")
    elif change == "expired":
        chat = brain.sessions["pi"]
        chat.pending_detail = replace(
            chat.pending_detail, expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    else:
        turn(brain, "remember flower as fictional daisy", "other")
        turn(brain, "confirm memory change", "other")
    reply = turn(brain, "confirm memory change")
    assert "no current" in reply.text
    assert not brain.details.items("default", "story captain")


def test_confirm_detail_tts_failure_rolls_back_and_can_retry(setup):
    brain, _, _ = setup
    turn(brain, "remember story captain as Violet")
    brain.stt.recognize.return_value = "confirm memory change"
    from iris_droid.protocol import write_wav

    body = write_wav(b"\x00\x00" * 100, 16000)
    with patch.object(brain.tts, "synthesize", side_effect=ValueError("failed")):
        with pytest.raises(ValueError, match="failed"):
            brain.turn("pi", body)
    assert not brain.details.items("default")
    turn(brain, "confirm memory change")
    assert brain.details.items("default")


def test_detail_change_checks_new_use_and_revision_inside_transaction(setup):
    brain, _, _ = setup
    remember(brain)
    turn(brain, "Continue story")
    turn(brain, "forget the detail story captain")
    pending = brain.sessions["pi"].pending_detail
    active = brain.memory.active_conversation("default")
    add(brain.memory, active)
    with pytest.raises(MemoryPolicyError):
        brain.details.apply("default", pending)
    assert brain.details.items("default")
    assert brain.memory.revision("default", active)


def test_private_details_no_reads_writes_or_memory_tool(setup):
    brain, _, requests = setup
    remember(brain)
    turn(brain, "private mode on")
    with patch.object(brain.details, "items", side_effect=AssertionError("private archive read")):
        assert "unavailable" in turn(brain, "list remembered details").text
        assert "unavailable" in turn(brain, "remember secret as unicorn").text
        turn(brain, "Tell me a joke")
    assert "search_memory" not in brain.sessions["pi"].tools.skills
    assert "Violet" not in json.dumps(requests[-1])
    turn(brain, "private mode off")
    assert len(brain.details.items("default")) == 1


def test_detail_retention_exceeds_old_cap_while_prompt_and_listing_stay_bounded(setup):
    brain, client, _ = setup
    remember(brain, "sapphire captain", "fictional Violet")
    for number in range(110):
        remember(brain, f"label {number}", f"value {number}")
    assert "exact detail label" in turn(brain, "correct label to no").text
    assert len(brain.details.items("default")) == 111
    context, labels = brain.details.context("default", "sapphire captain", limit=400)
    assert "fictional Violet" in context  # Older than the most recent 96 candidates.
    assert "sapphire captain" in labels
    assert len(context) < 800 and len(labels) < 8
    assert len(brain.details.items("default", limit=9)) == 9
    assert "There are more" in turn(brain, "list remembered details").text
    assert "fictional Violet" in turn(brain, "show the detail sapphire captain").text
    restarted = Brain(brain.settings, client, brain.stt, brain.tts)
    assert len(restarted.details.items("default")) == 111
    assert len(restarted.memory.export("default")["remembered_details"]) == 111
    assert not restarted.details.items("other")


def test_readonly_tool_has_bound_owner_dates_sources_and_call_limit(setup):
    brain, _, _ = setup
    active = brain.memory.active_conversation("default")
    source = add(brain.memory, active)
    other = brain.memory.active_conversation("other")
    add(brain.memory, other, "Spaceship private other-owner marker", owner="other")
    chat = brain._session("pi")
    assert set(chat.tools.skills) == {"get_local_time", "search_memory"}
    arguments = json.dumps({"query": "cardboard spaceship today"})
    result = json.loads(chat.tools.execute("search_memory", arguments))
    assert result["sources"][0]["exchange"] == source.id
    assert "other-owner marker" not in json.dumps(result)
    assert active in chat.memory_sources
    assert "error" in json.loads(
        chat.tools.execute("search_memory", '{"query":"ship","owner":"other"}')
    )
    chat.tools.execute("search_memory", arguments)
    assert "limit" in chat.tools.execute("search_memory", arguments)
    brain.memory.set_private("default", True)
    result = search_memory(
        brain.memory_context.index,
        "default",
        "Europe/London",
        0,
        chat.memory_sources.add,
        SearchArguments(query="spaceship"),
    )
    assert "unavailable" in result["error"]


@pytest.mark.parametrize(
    "updates",
    [
        {"memory_search_tool": False},
        {"tools_enabled": False},
        {"memory_enabled": False},
        {"history_turns": 0},
        {"memory_retrieval_chars": 0},
    ],
)
def test_search_tool_configuration_gates(setup, updates):
    brain, client, _ = setup
    fresh = Brain(brain.settings.model_copy(update=updates), client, brain.stt, brain.tts)
    assert "search_memory" not in fresh._session("pi").tools.skills


def test_conversation_deletion_tracks_copies_cycles_and_stale_targets(setup):
    brain, _, _ = setup
    first = brain.memory.active_conversation("default")
    add(brain.memory, first)
    second = brain.memory.start_conversation("default")
    add(brain.memory, second, source_conversations=(first,))
    add(brain.memory, first, source_conversations=(second,))  # Cycle must terminate.
    unrelated = brain.memory.start_conversation("default")
    add(brain.memory, unrelated, "An independent garden project")
    scope = brain.memory.deletion_scope("default", first)
    assert {r[0] for r in scope} == {first, second}
    brain.memory.forget_current(
        "default",
        first,
        brain.memory.revision("default", first),
        expected_epoch=0,
        scope=scope,
        require_active=False,
    )
    assert brain.memory.revision("default", first) is None
    assert brain.memory.revision("default", second) is None
    assert brain.memory.revision("default", unrelated)


def safe_note(
    archive,
    conversation,
    *,
    text="An unfinished cardboard spaceship",
    safe=True,
    scope="fictional",
    status="tentative",
    human="Build a cardboard spaceship.",
):
    source = add(archive, conversation, human)
    index = MemoryIndex(archive)
    note = Note(
        kind="open_question",
        text=text,
        sources=[source.id],
        scope=scope,
        status=status,
        follow_up_safe=safe,
    )
    with archive.connect() as db:
        sequence = db.execute("SELECT sequence FROM exchanges WHERE id=?", (source.id,)).fetchone()[
            0
        ]
    assert index.save("default", conversation, source.id, 0, sequence, Summary(notes=[note]))
    return source


def test_companion_recent_safe_topic_persistent_cooldown_and_block(setup):
    brain, _, _ = setup
    archive = brain.memory
    conversation = archive.active_conversation("default")
    safe_note(archive, conversation)
    companion = CompanionMemory(archive)
    with patch("iris_memory.companion.random.random", return_value=0):
        selected = companion.choose("default")
        assert selected[0] == conversation and "fictional" in selected[1]
        add(archive, conversation, followup_conversation=conversation)
        assert companion.choose("default") is None
        assert companion.choose("other") is None
        later = datetime.now(UTC) + timedelta(days=2)
        assert companion.choose("default", now=later)
        assert companion.block("default", conversation, 0)
        assert CompanionMemory(SQLiteMemory(archive.path)).choose("default", now=later) is None


@pytest.mark.parametrize(
    "updates",
    [
        {"safe": False},
        {"status": "closed"},
        {"scope": "uncertain"},
        {"human": "My hospital treatment is painful, but I want to build a spaceship"},
        {"text": "An unfinished financial project"},
    ],
)
def test_companion_rejects_unsafe_or_closed_topics(setup, updates):
    brain, _, _ = setup
    conversation = brain.memory.active_conversation("default")
    safe_note(brain.memory, conversation, **updates)
    with patch("iris_memory.companion.random.random", return_value=0):
        assert brain.companion_memory.choose("default") is None


def test_companion_clear_old_sources_and_private_mode_are_excluded(setup):
    brain, _, _ = setup
    conversation = brain.memory.active_conversation("default")
    source = safe_note(brain.memory, conversation)
    with patch("iris_memory.companion.random.random", return_value=0):
        brain.memory.set_private("default", True)
        assert brain.companion_memory.choose("default") is None
        brain.memory.set_private("default", False)
        with brain.memory.connect() as db:
            db.execute(
                "UPDATE exchanges SET created_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(days=20)).isoformat(), source.id),
            )
        assert brain.companion_memory.choose("default") is None
        with brain.memory.connect() as db:
            db.execute(
                "UPDATE exchanges SET created_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), source.id),
            )
        brain.memory.start_conversation("default")
        assert brain.companion_memory.choose("default") is None


def test_companion_server_commit_rollback_optout_and_tools_disabled(setup):
    brain, _, requests = setup
    brain.settings.memory_companion_followups = True
    conversation = brain.memory.active_conversation("default")
    safe_note(brain.memory, conversation)
    turn(brain, "companion mode on")
    with patch("iris_memory.companion.random.random", return_value=0):
        with patch.object(brain, "_reply", side_effect=ValueError("tts failed")):
            with pytest.raises(ValueError):
                brain.turn("pi", b"", text_only=True, companion_idle=True)
        assert brain.sessions["pi"].last_followup is None
        assert brain.companion_memory.choose("default") is not None
        brain.turn("pi", b"", text_only=True, companion_idle=True)
    assert not requests[-1].get("tools")
    assert "cardboard spaceship" in requests[-1]["messages"][0]["content"]
    assert brain.sessions["pi"].last_followup == conversation
    assert "won't use" in turn(brain, "don't bring that up again").text
    with brain.memory.connect() as db:
        assert db.execute("SELECT blocked FROM companion_topics").fetchone()[0] == 1


def test_whole_archive_delete_removes_explicit_details_too(setup):
    brain, _, _ = setup
    remember(brain)
    brain.memory.delete("default", None)
    assert not brain.details.items("default")


def test_schema_four_upgrade_preserves_archive_and_policy(tmp_path):
    path = tmp_path / "upgrade.db"
    archive = SQLiteMemory(path)
    conversation = archive.active_conversation("default")
    add(archive, conversation)
    archive.set_private("default", True)
    with archive.connect() as db:
        for table in (
            "detail_supersessions",
            "memory_attention",
            "memory_topics",
            "memory_facts",
            "conversation_uses",
            "detail_uses",
            "memory_details",
            "companion_topics",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=4")
    upgraded = SQLiteMemory(path)
    assert upgraded.policy("default") == (True, 1)
    assert upgraded.revision("default", conversation)
    assert Details(upgraded).items("default") == []


def test_selected_detail_controls_and_new_topic_invalidation(setup):
    brain, _, _ = setup
    remember(brain)
    assert "Violet" in turn(brain, "show the detail story captain").text
    assert "Emerald" in turn(brain, "correct that detail to fictional Emerald").text
    turn(brain, "confirm memory change")
    assert "Forget the detail" in turn(brain, "forget that detail").text
    turn(brain, "cancel memory change")
    turn(brain, "Now let's discuss plants")
    assert "Show a remembered detail" in turn(brain, "forget that detail").text


def test_selected_older_conversation_delete_keeps_independent_archive(setup):
    brain, _, _ = setup
    turn(brain, "The spaceship is called Sapphire and built from cardboard")
    original = brain.memory.active_conversation("default")
    turn(brain, "clear context")
    unrelated = brain.memory.active_conversation("default")
    add(brain.memory, unrelated, "We are growing sunflowers in the garden")
    brain.memory.start_conversation("default")
    turn(brain, "Remember the Sapphire spaceship story?")
    pending = brain.sessions["pi"].pending_recall
    if pending.selected is None:
        turn(brain, "yes")
    reply = turn(brain, "forget that conversation")
    assert "selected saved conversation" in reply.text
    assert "1 saved conversations" in reply.text  # Current recalled copy is dependent.
    turn(brain, "confirm forget this conversation")
    assert brain.memory.revision("default", original) is None
    assert brain.memory.revision("default", unrelated)


def test_cli_requires_explicit_dependent_scope_before_deleting(setup, tmp_path, capsys):
    from iris_memory.cli import main

    brain, _, _ = setup
    first = brain.memory.active_conversation("default")
    add(brain.memory, first)
    second = brain.memory.start_conversation("default")
    add(brain.memory, second, source_conversations=(first,))
    config = tmp_path / "config.json"
    config.write_text(brain.settings.model_dump_json())
    args = ["--config", str(config), "delete", "--conversation", first]
    assert main(args) == 1
    assert "--include-dependent" in capsys.readouterr().out
    assert brain.memory.revision("default", first)
    assert main([*args, "--include-dependent"]) == 0
    assert brain.memory.revision("default", second) is None


def test_companion_generic_fallback_and_restart_optout(setup):
    brain, client, requests = setup
    brain.settings.memory_companion_followups = True
    conversation = brain.memory.active_conversation("default")
    safe_note(brain.memory, conversation)
    turn(brain, "companion mode on")
    with patch.object(brain.companion_memory, "choose", side_effect=ValueError("bad index")):
        brain.turn("pi", b"", text_only=True, companion_idle=True)
    assert brain.sessions["pi"].last_followup is None
    assert (
        "Untrusted historical reference for an optional"
        not in requests[-1]["messages"][0]["content"]
    )
    with patch("iris_memory.companion.random.random", return_value=0):
        brain.turn("pi", b"", text_only=True, companion_idle=True)
    restarted = Brain(brain.settings, client, brain.stt, brain.tts)
    assert "won't use" in turn(restarted, "don't bring that up again").text
    with restarted.memory.connect() as db:
        assert db.execute("SELECT blocked FROM companion_topics").fetchone()[0] == 1


def test_stale_summary_cannot_commit_after_policy_change(setup):
    brain, _, _ = setup
    archive = brain.memory
    conversation = archive.active_conversation("default")
    source = add(archive, conversation)
    epoch = archive.policy("default")[1]
    archive.set_private("default", True)
    archive.set_private("default", False)
    assert not MemoryIndex(archive).save(
        "default", conversation, source.id, 0, 1, Summary(notes=[]), expected_epoch=epoch
    )
    assert not archive.export("default")["summaries"]


def test_real_http_details_restart_correction_and_forgetting(setup):
    import threading
    from http.server import HTTPServer

    from iris_droid.server import handler_for

    brain, model_client, _ = setup
    session = str(uuid4())
    for phase in ("save", "restart", "forget"):
        if phase != "save":
            brain = Brain(brain.settings, model_client, brain.stt, brain.tts)
        with HTTPServer(("127.0.0.1", 0), handler_for(brain, "test")) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with httpx.Client(trust_env=False) as client:

                    def post(text):
                        response = client.post(
                            f"http://127.0.0.1:{server.server_port}/v1/text",
                            headers={
                                "Authorization": "Bearer test",
                                "X-Iris-Session": session,
                                "Content-Type": "text/plain",
                            },
                            content=text,
                        )
                        assert response.status_code == 200
                        return response.json()["text"]

                    if phase == "save":
                        post("remember story captain as fictional Violet")
                        post("confirm memory change")
                        post("Continue the spaceship story")
                    elif phase == "restart":
                        assert "Violet" in post("show the detail story captain")
                        assert "whole saved conversations" in post(
                            "correct that detail to fictional Emerald"
                        )
                        post("confirm memory change")
                        assert "Violet" not in json.dumps(brain.memory.export("default"))
                    else:
                        assert "Emerald" in post("show the detail story captain")
                        post("forget that detail")
                        post("confirm memory change")
                        assert not brain.details.items("default")
            finally:
                server.shutdown()
                worker.join()


def test_repeated_companion_optout_does_not_target_an_older_topic(setup):
    brain, _, _ = setup
    older = brain.memory.active_conversation("default")
    add(brain.memory, older, followup_conversation=older)
    newer = brain.memory.start_conversation("default")
    add(brain.memory, newer, followup_conversation=newer)
    turn(brain, "don't bring that up again")
    turn(brain, "don't bring that up again")
    with brain.memory.connect() as db:
        blocked = dict(db.execute("SELECT conversation,blocked FROM companion_topics"))
    assert blocked == {older: 0, newer: 1}


def test_detail_context_without_search_terms_keeps_recent_values(setup):
    brain, _, _ = setup
    remember(brain)
    for query in ("", "hi", "?!"):
        context, labels = brain.details.context("default", query)
        assert "Violet" in context and labels == ("story captain",)


def test_full_source_archive_survives_context_limits_summaries_rebuild_and_restart(setup):
    brain, _, _ = setup
    archive = brain.memory
    conversation = archive.active_conversation("default")
    original = "Start of the old story. " + "Original source detail. " * 300 + "The ending: café."
    first = add(archive, conversation, original)
    with archive.connect() as db:
        db.execute(
            "UPDATE exchanges SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (first.id,)
        )
    for number in range(130):
        add(archive, conversation, f"Later discussion {number}")
    before = archive.export("default")["exchanges"]
    assert len(before) == 131 and before[0]["user_text"] == original
    assert len(archive.recent("default", conversation, 2, 200)) < len(before)
    assert brain.memory_context.checkpoint("default", conversation, force=True)
    assert archive.export("default")["exchanges"] == before
    brain.memory_context.index.rebuild("default")
    assert archive.export("default")["exchanges"] == before
    restarted = SQLiteMemory(archive.path)
    assert restarted.export("default")["exchanges"] == before
    assert restarted.export("other")["exchanges"] == []
