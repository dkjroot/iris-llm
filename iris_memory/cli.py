"""Offline archive maintenance. Stop the Iris server before deletion or backup."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from iris_memory import SQLiteMemory, database_path
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
    selection = delete.add_mutually_exclusive_group(required=True)
    selection.add_argument("--conversation", help="Conversation ID from list")
    selection.add_argument(
        "--all", action="store_true", help="Delete all conversations for this owner"
    )
    args = parser.parse_args(argv)
    try:
        if args.config != Path("local-config.json") and not args.config.exists():
            raise ValueError("The requested configuration file does not exist")
        settings = load_settings(args.config if args.config.exists() else None)
        memory = SQLiteMemory(database_path(settings), create=False)
        if args.action == "list":
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
            memory.delete(settings.memory_owner, args.conversation)
            print("Deleted the selected conversation history. Independent backups are unchanged.")
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        # Never print SQLite errors that could contain archived text.
        print(
            f"Memory maintenance failed ({type(error).__name__}). "
            "Check the config, path and database."
        )
        return 1
