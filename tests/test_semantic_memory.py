"""Optional semantic retrieval, versioned indexing and preparation safety."""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

import iris
from iris_droid.server import Brain
from iris_memory import Exchange, SQLiteMemory
from iris_memory.embeddings import EmbeddingError, MiniLMEncoder, download_model, verify
from iris_memory.evaluate import evaluate
from iris_memory.index import MemoryIndex
from iris_memory.recall import RecallResolver
from iris_memory.semantic import SemanticMemory
from iris_settings import Settings


class FakeEncoder:
    model_id = "test-model:revision-one:paired-v1"
    dimensions = 3

    def __init__(self):
        self.calls = []
        self.during = None

    def encode(self, texts):
        self.calls.append(texts)
        if self.during:
            self.during()
        rows = []
        for text in texts:
            text = text.lower()
            if any(
                word in text
                for word in ("rainwater", "collecting water", "water collection", "garden")
            ):
                rows.append([1, 0, 0])
            elif any(word in text for word in ("robot", "sapphire", "story")):
                rows.append([0, 1, 0])
            else:
                rows.append([0, 0, 1])
        return np.asarray(rows, dtype=np.float32).reshape((-1, 3))


@pytest.fixture
def setup(tmp_path):
    settings = Settings(memory_enabled=True, memory_database=tmp_path / "memory.sqlite3")
    archive = SQLiteMemory(settings.memory_database)
    garden = archive.start_conversation("default")
    archive.append(
        "default",
        garden,
        Exchange("garden", "human", "A rainwater tank for the garden.", "Choose its capacity."),
    )
    story = archive.start_conversation("default")
    archive.append(
        "default",
        story,
        Exchange("story", "human", "A Sapphire robot story.", "Its ending is open."),
    )
    active = archive.start_conversation("default")
    index = MemoryIndex(archive)
    encoder = FakeEncoder()
    semantic = SemanticMemory(archive, encoder)
    return settings, archive, index, encoder, semantic, active, garden, story


def fill(semantic, owner="default"):
    while semantic.pending(owner):
        semantic.index_batch(owner, 2)


def test_paraphrase_keeps_existing_clarification_and_verified_sources(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    resolver = RecallResolver(index, "Europe/London")
    assert not resolver.resolve(
        "default", active, "Remember the water collection arrangement?", []
    ).excerpts
    index.semantic = semantic
    result = resolver.resolve("default", active, "Remember the water collection arrangement?", [])
    assert not result.excerpts and result.pending and "possible discussion" in result.response
    assert result.pending.candidates[0].sources == ("garden",)
    selected = resolver.resolve("default", active, "yes", [], result.pending)
    assert selected.excerpts[0]["exchange"] == "garden"
    assert "rainwater tank" in selected.excerpts[0]["text"]


def test_indexing_is_bounded_and_resumes_after_restart(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    assert semantic.pending("default") == 4
    assert semantic.index_batch("default", 1) == 1
    assert semantic.pending("default") == 3
    reopened = SemanticMemory(SQLiteMemory(settings.memory_database), encoder)
    fill(reopened)
    assert reopened.pending("default") == 0
    calls = len(encoder.calls)
    assert reopened.index_batch("default") == 0 and len(encoder.calls) == calls
    assert all(len(batch) <= 2 for batch in encoder.calls)


def test_versions_never_mix_and_dimensions_are_checked(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    fill(semantic)
    second = FakeEncoder()
    second.model_id = "test-model:revision-two:paired-v1"
    updated = SemanticMemory(archive, second)
    assert updated.pending("default") == 4
    assert not updated.search("default", "collecting water", explicit=True)
    fill(updated)
    assert updated.search("default", "collecting water", explicit=True)[0]["exchange"] == "garden"
    wrong = FakeEncoder()
    wrong.dimensions = 7
    with pytest.raises(EmbeddingError, match="dimensions"):
        SemanticMemory(archive, wrong)


def test_owner_dates_clear_and_superseded_filters_apply_before_similarity(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    other = archive.start_conversation("another-owner")
    archive.append(
        "another-owner", other, Exchange("private", "human", "Rainwater private plans.", "Private.")
    )
    fill(semantic)
    fill(semantic, "another-owner")
    assert not semantic.search("default", "collecting water", active=active)
    assert {
        r["exchange"] for r in semantic.search("default", "collecting water", explicit=True)
    } == {"garden"}
    with archive.connect() as db:
        db.execute("UPDATE exchanges SET created_at='2026-10-24T23:30:00+00:00' WHERE id='garden'")
    inside = semantic.search(
        "default",
        "collecting water",
        explicit=True,
        start=datetime(2026, 10, 24, 23, tzinfo=UTC),
        end=datetime(2026, 10, 25, 23, tzinfo=UTC),
    )
    assert inside and inside[0]["exchange"] == "garden"
    assert not semantic.search(
        "default", "collecting water", explicit=True, start=datetime(2026, 10, 25, tzinfo=UTC)
    )
    with archive.connect() as db:
        db.execute("UPDATE chunks SET superseded=1 WHERE exchange='garden'")
    assert not semantic.search("default", "collecting water", explicit=True)


def test_deleted_and_rebuilt_chunks_remove_all_model_vectors(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    fill(semantic)
    archive.delete("default", garden)
    assert not semantic.search("default", "collecting water", explicit=True)
    index.rebuild("default")
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 0
    fill(semantic)
    assert semantic.search("default", "robot", explicit=True)[0]["exchange"] == "story"


def test_stale_inference_cannot_recreate_deleted_rows_or_reused_chunk_ids(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup

    def delete_and_replace():
        archive.delete("default", None)
        new = archive.start_conversation("default")
        archive.append(
            "default",
            new,
            Exchange(
                "replacement", "human", "A rainwater tank for the garden.", "Choose its capacity."
            ),
        )

    encoder.during = delete_and_replace
    semantic.index_batch("default", 2)
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 0
    encoder.during = None
    fill(semantic)
    assert (
        semantic.search("default", "collecting water", explicit=True)[0]["exchange"]
        == "replacement"
    )


@pytest.mark.parametrize("kind", ["exception", "nan", "dimensions", "zero"])
def test_failed_batches_leave_work_pending(kind, setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    if kind == "exception":
        encoder.encode = Mock(side_effect=EmbeddingError("private text must not be logged"))
    else:
        encoder.encode = lambda texts: np.full(
            (len(texts), 7 if kind == "dimensions" else 3),
            np.nan if kind == "nan" else 0,
            dtype=np.float32,
        )
    with pytest.raises(EmbeddingError):
        semantic.index_batch("default")
    assert semantic.pending("default") == 4


def test_query_failure_preserves_lexical_recall_and_logs_no_text(setup, caplog):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    fill(semantic)
    index.semantic = semantic
    encoder.encode = Mock(side_effect=EmbeddingError("private text"))
    rows = index.recall_sources("default", ["rainwater"], None, None)
    assert rows and rows[0]["exchange"] == "garden"
    assert "private text" not in caplog.text


def test_non_evidence_turns_are_not_semantic_candidates(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    archive.append(
        "default",
        active,
        Exchange("recall", "human", "Remember the rainwater plan?", "Invented recall."),
    )
    archive.append(
        "default", active, Exchange("selection", "human", "the first one", "Invented recall.")
    )
    archive.append(
        "default", active, Exchange("opener", "automatic", "Automatic event", "Garden water idea.")
    )
    fill(semantic)
    assert {
        row["exchange"] for row in semantic.search("default", "collecting water", explicit=True)
    } == {"garden"}


def test_reset_is_scoped_to_owner_and_current_model(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    fill(semantic)
    other = archive.start_conversation("another-owner")
    archive.append("another-owner", other, Exchange("other", "human", "A robot story.", "An idea."))
    fill(semantic, "another-owner")
    semantic.reset("default")
    assert semantic.pending("default") == 4
    assert semantic.pending("another-owner") == 0


def test_corrupt_vectors_are_skipped(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    fill(semantic)
    with archive.connect() as db:
        db.execute("UPDATE embedding_vectors SET vector=?", (b"bad-length",))
    assert not semantic.search("default", "collecting water", explicit=True)


def test_missing_or_modified_model_never_downloads_implicitly(tmp_path):
    with patch("httpx.Client") as network:
        with pytest.raises(EmbeddingError, match="missing"):
            MiniLMEncoder(tmp_path)
        network.assert_not_called()
    target = tmp_path / "test.onnx"
    target.write_bytes(b"bad!")
    with patch(
        "iris_memory.embeddings.FILES",
        {"test.onnx": ("remote", 4, hashlib.sha256(b"good").hexdigest())},
    ):
        with pytest.raises(EmbeddingError, match="checksum"):
            verify(tmp_path)


def test_download_checksums_sizes_cleanup_and_keeps_existing_files(tmp_path):
    data = b"verified model"
    files = {"test.onnx": ("onnx/test.onnx", len(data), hashlib.sha256(data).hexdigest())}
    with patch("iris_memory.embeddings.FILES", files):
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data))
        ) as client:
            download_model(tmp_path, client)
            assert (tmp_path / "test.onnx").read_bytes() == data
            with patch.object(client, "stream", side_effect=AssertionError("don't redownload")):
                download_model(tmp_path, client)
        (tmp_path / "test.onnx").write_bytes(b"older model")
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"wrong"))
        ) as client:
            with pytest.raises(EmbeddingError):
                download_model(tmp_path, client)
        assert (tmp_path / "test.onnx").read_bytes() == b"older model"
        assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob(".*.part"))


def test_disabled_semantic_does_not_load_optional_runtime_or_model(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    with (
        httpx.Client() as client,
        patch("iris_droid.server.MiniLMEncoder", side_effect=AssertionError("no load")),
    ):
        brain = Brain(settings, client, Mock(), Mock())
        assert brain.recall.index.semantic is None


def test_enabled_semantic_requires_persistence_and_prepared_model(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    with pytest.raises(ValueError, match="requires memory_enabled"):
        Settings(memory_semantic_enabled=True)
    settings = settings.model_copy(update={"memory_semantic_enabled": True})
    with httpx.Client() as client, pytest.raises(EmbeddingError, match="missing"):
        Brain(settings, client, Mock(), Mock())


def test_cli_explicit_preparation_index_and_reset(setup, tmp_path, capsys):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    config = tmp_path / "settings.json"
    config.write_text(settings.model_dump_json())
    command = ["memory", "--config", str(config)]
    with patch("iris_memory.cli.MiniLMEncoder", return_value=encoder):
        assert iris.main([*command, "index"]) == 0
        assert semantic.pending("default") == 0
        assert iris.main([*command, "index", "--reset"]) == 0
    assert "index ready" in capsys.readouterr().out
    with patch("iris_memory.cli.download_model") as download:
        directory = tmp_path / "model"
        assert iris.main([*command, "download-model", "--directory", str(directory)]) == 0
        assert download.call_args.args[0] == directory


def test_migration_three_rolls_back_on_schema_failure(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    with archive.connect() as db:
        db.execute("DROP TABLE embedding_vectors")
        db.execute("DROP TABLE embedding_models")
        db.execute("PRAGMA user_version=2")
        db.execute("CREATE TABLE embedding_vectors (conflicting TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        SQLiteMemory(settings.memory_database)
    with archive.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='embedding_models'").fetchone()
            is None
        )
        assert db.execute("SELECT count(*) FROM exchanges").fetchone()[0] == 2


@pytest.mark.skipif(
    not os.environ.get("IRIS_TEST_EMBEDDING_MODEL_DIR"), reason="explicit local model test"
)
def test_real_pinned_model_improves_synthetic_paraphrase():
    report = evaluate(
        Path(os.environ["IRIS_TEST_EMBEDDING_MODEL_DIR"]),
        Path(__file__).parent / "fixtures" / "semantic_recall_cases.json",
    )
    assert (
        report["modes"]["hybrid"]["evidence_matches"]
        > report["modes"]["lexical"]["evidence_matches"]
    )
    row = next(row for row in report["modes"]["hybrid"]["cases"] if "arrangement" in row["query"])
    assert row["found"] == ["garden"] and row["confirmation_required"]


def test_semantic_only_always_confirms_even_with_incidental_word_overlap(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    resolver = RecallResolver(index, "Europe/London")
    with patch.object(
        index,
        "recall_sources",
        return_value=[
            {
                "exchange": "garden",
                "conversation": garden,
                "created_at": "2026-11-04T12:00:00+00:00",
                "user_text": "water collection arrangement",
                "semantic_similarity": 0.9,
                "semantic_only": True,
            }
        ],
    ):
        result = resolver.resolve(
            "default", active, "Remember the water collection arrangement?", []
        )
    assert result.response and result.pending and not result.excerpts


def test_wizard_can_enable_semantic_and_turn_memory_off(tmp_path):
    import configure

    config = tmp_path / "local-config.json"

    def answer(prompt):
        if prompt.startswith("Save conversation memory") or prompt.startswith(
            "Enable local semantic"
        ):
            return "yes"
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    assert json.loads(config.read_text())["memory_semantic_enabled"]

    def disable(prompt):
        return "no" if prompt.startswith("Save conversation memory") else ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=disable):
        assert configure.main() == 0
    saved = json.loads(config.read_text())
    assert not saved["memory_enabled"] and not saved["memory_semantic_enabled"]


@pytest.mark.skipif(
    not os.environ.get("IRIS_TEST_EMBEDDING_MODEL_DIR"), reason="explicit local model test"
)
def test_real_model_server_text_recall_smoke(setup):
    settings, archive, index, encoder, semantic, active, garden, story = setup
    settings = settings.model_copy(
        update={
            "memory_semantic_enabled": True,
            "memory_embedding_model_dir": Path(os.environ["IRIS_TEST_EMBEDDING_MODEL_DIR"]),
        }
    )
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        if payload["messages"][0]["content"].startswith("Extract memory metadata"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": '{"facts": [], "topics": []}'}}
                    ]
                },
            )
        if payload["messages"][0]["content"].startswith("Summarise these original"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": '{"notes": []}'}}]},
            )
        requests.append(payload)
        assert "rainwater tank" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "We were choosing the tank capacity.",
                        }
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        brain = Brain(settings, client, Mock(), Mock())
        question = brain.turn("pi", b"Remember our water collection arrangement?", text_only=True)
        assert "possible discussion" in question.text and not requests
        reply = brain.turn("pi", b"yes", text_only=True)
        assert reply.text == "We were choosing the tank capacity."
        assert len(requests) == 1
