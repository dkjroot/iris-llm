"""Keep runnable recipes and shared user-facing controls connected to the code."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iris_app.commands import parse_commands
from iris_droid.wake import CANCEL_PHRASE
from iris_memory.recall import is_recall_request
from scripts.build_reference import render
from scripts.check_docs import anchors, check, prose

ROOT = Path(__file__).resolve().parents[1]


def test_public_documentation_links():
    assert check(ROOT) == []


def test_indented_example_cannot_hide_links_and_headings_after_it():
    document = (
        "1. A step.\n\n   ```sh\n   # not a heading\n   [not a link](absent)\n   ```\n\n"
        "## Real heading\n[real link](target)\n\n```sh\n# another example\n```\n"
    )
    assert anchors(document) == {"real-heading"}
    assert "[real link](target)" in prose(document)
    assert "[not a link]" not in prose(document)


def test_configuration_reference_matches_declared_settings():
    assert (ROOT / "docs/configuration-reference.md").read_text(encoding="utf-8") == render()


def test_verification_map_names_existing_tests():
    references = re.findall(
        r"(tests/[\w/]+\.py)::(test_\w+)",
        (ROOT / "docs/verification.md").read_text(encoding="utf-8"),
    )
    assert len(references) >= 25
    for filename, name in references:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        assert name in {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }, (filename, name)


def test_documented_python_examples_compile_without_running_hardware():
    for path in (ROOT / "docs").glob("*.md"):
        for example in re.findall(r"```python\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S):
            compile(example, str(path.relative_to(ROOT)), "exec")


RECIPES = re.findall(
    r"```python\n(.*?)\n```", (ROOT / "docs/extensions.md").read_text(encoding="utf-8"), re.S
)


@pytest.mark.parametrize("example", RECIPES, ids=["backend", "control", "tool", "speech", "device"])
def test_extension_recipes(example):
    exec(compile(example, "docs/extensions.md", "exec"), {})


def test_menu_commands_match_their_actual_entry_point():
    categories = json.loads((ROOT / "iris_droid/web/commands.json").read_text(encoding="utf-8"))[
        "categories"
    ]
    stateful = {"The first one", "Never mind"}
    for category in categories:
        for phrase, _ in category["commands"]:
            example = (
                phrase.replace("LABEL", "garden").replace("VALUE", "blue").replace("NAME", "iris")
            )
            if phrase in stateful:
                # These are recall continuations, not unconditional application controls.
                continue
            if phrase == "Cancel, cancel":
                assert re.sub(r"[^a-z ]", "", phrase.lower()) == CANCEL_PHRASE
            elif phrase.startswith("Recall "):
                assert is_recall_request(example)
            else:
                assert parse_commands(example, companion=True, wake_conversation=True).first, phrase


@pytest.mark.parametrize("mode", ["client", "server"])
def test_setup_command_flags_exist_in_cli_help(mode):
    result = subprocess.run(
        [sys.executable, str(ROOT / "iris.py"), mode, "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    available = set(re.findall(r"--[a-z][a-z0-9-]*", result.stdout))
    checked = 0
    for path in (ROOT / "docs").glob("*.md"):
        for block in re.findall(
            r"```(?:sh|bash|powershell)?\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S
        ):
            block = block.replace("\\\n", " ").replace("`\n", " ")
            for command in re.findall(
                rf"(?:python(?:\.exe)?)[\"']?\s+(?:\.\\)?iris\.py {mode} (.+)$", block, re.M
            ):
                flags = set(re.findall(r"--[a-z][a-z0-9-]*", command))
                assert flags <= available, (path.name, flags - available)
                checked += 1
    assert checked > 0
