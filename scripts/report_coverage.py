"""Group coverage.py's measured statements and branches by Iris subsystem."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def grouped_report(data):
    groups = defaultdict(
        lambda: dict.fromkeys(
            ("covered_lines", "num_statements", "covered_branches", "num_branches"), 0
        )
    )
    for filename, entry in data["files"].items():
        path = Path(filename.replace("\\", "/"))
        group = path.parts[0] if len(path.parts) > 1 else "entrypoints_and_settings"
        for key in groups[group]:
            groups[group][key] += entry["summary"].get(key, 0)
    lines = ["| Subsystem | Statements | Branches |", "| --- | ---: | ---: |"]
    for name, counts in sorted(groups.items()):

        def fraction(covered, total):
            return f"{covered}/{total} ({covered / total:.1%})" if total else "—"

        lines.append(
            f"| {name} | {fraction(counts['covered_lines'], counts['num_statements'])} | "
            f"{fraction(counts['covered_branches'], counts['num_branches'])} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path(".cache/checks/coverage.json"))
    args = parser.parse_args()
    report = grouped_report(json.loads(args.path.read_text(encoding="utf-8")))
    args.path.with_suffix(".md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
