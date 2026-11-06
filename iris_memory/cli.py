"""Offline archive maintenance. Stop the Iris server before deletion or backup."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import httpx

from iris_memory import SQLiteMemory, database_path
from iris_memory.embeddings import EmbeddingError, MiniLMEncoder, download_model, model_directory
from iris_memory.semantic import SemanticMemory
from iris_settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local-config.json"))
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list", help="List conversation IDs, dates and exchange counts")
    commands.add_parser(
        "rebuild", help="Rebuild search and queue fresh summaries from source history"
    )
    commands.add_parser("retry", help="Reset failed summary jobs for this owner")
    commands.add_parser("export", help="Write the owner's archive as JSON to standard output")
    delete = commands.add_parser(
        "delete", help="Delete this owner's selected conversation or archive"
    )
    delete.add_argument(
        "--include-dependent",
        action="store_true",
        help="Also delete whole conversations that used the selected conversation",
    )
    selection = delete.add_mutually_exclusive_group(required=True)
    selection.add_argument("--conversation", help="Conversation ID from list")
    selection.add_argument(
        "--all",
        action="store_true",
        help="Delete all conversations and explicitly remembered details for this owner",
    )
    prepare = commands.add_parser(
        "download-model", help="Explicitly download the pinned local embedding model"
    )
    prepare.add_argument("--directory", type=Path, help="Override the configured model directory")
    indexing = commands.add_parser(
        "index", help="Resume building semantic vectors from source chunks"
    )
    indexing.add_argument(
        "--reset", action="store_true", help="Rebuild this owner's current model vectors"
    )
    evaluation = commands.add_parser(
        "evaluate", help="Compare lexical and semantic recall using a synthetic corpus"
    )
    evaluation.add_argument("--corpus", type=Path, required=True)
    evaluation.add_argument(
        "--directory", type=Path, help="Override the configured model directory"
    )
    args = parser.parse_args(argv)
    try:
        if args.config != Path("local-config.json") and not args.config.exists():
            raise ValueError("The requested configuration file does not exist")
        settings = load_settings(args.config if args.config.exists() else None)
        if args.action == "download-model":
            directory = args.directory or model_directory(settings)
            with httpx.Client(trust_env=False, follow_redirects=True) as client:
                download_model(directory, client)
            print("Verified embedding model saved to", directory)
            return 0
        if args.action == "evaluate":
            from iris_memory.evaluate import evaluate

            print(
                json.dumps(
                    evaluate(
                        args.directory or model_directory(settings),
                        args.corpus,
                        settings.memory_semantic_min_score,
                    ),
                    indent=2,
                )
            )
            return 0
        memory = SQLiteMemory(database_path(settings), create=False)
        if args.action == "index":
            # The archive must already exist; only now allow its schema upgrade.
            memory = SQLiteMemory(database_path(settings))
            semantic = SemanticMemory(
                memory, MiniLMEncoder(model_directory(settings)), settings.memory_semantic_min_score
            )
            if args.reset:
                semantic.reset(settings.memory_owner)
            total = 0
            while semantic.pending(settings.memory_owner):
                total += semantic.index_batch(settings.memory_owner, 32)
            print(f"Semantic index ready; processed {total} chunks for this model version.")
        elif args.action == "list":
            print(json.dumps(memory.conversations(settings.memory_owner), indent=2))
        elif args.action == "export":
            print(json.dumps(memory.export(settings.memory_owner), indent=2, ensure_ascii=False))
        elif args.action in {"retry", "rebuild"}:
            from iris_memory.index import MemoryIndex

            index = MemoryIndex(memory)
            if args.action == "rebuild":
                index.rebuild(settings.memory_owner)
            else:
                index.retry(settings.memory_owner)
            print("Summary jobs can retry when the server next handles a conversation turn.")
        else:
            if args.conversation is not None:
                scope = memory.deletion_scope(settings.memory_owner, args.conversation)
                if len(scope) > 1 and not args.include_dependent:
                    print(
                        f"This also removes {len(scope) - 1} dependent whole conversations, "
                        "including their other topics. Review your export, then add "
                        "--include-dependent to delete that scope."
                    )
                    return 1
            memory.delete(settings.memory_owner, args.conversation)
            print(
                "Deleted the selected memory and recorded dependent copies. "
                "Independent backups are unchanged."
            )
        return 0
    except (ValueError, OSError, sqlite3.Error, EmbeddingError, httpx.HTTPError) as error:
        # Never print SQLite errors that could contain archived text.
        if isinstance(error, EmbeddingError):
            print(str(error))
            return 1
        print(
            f"Memory maintenance failed ({type(error).__name__}). "
            "Check the config, path and database."
        )
        return 1
