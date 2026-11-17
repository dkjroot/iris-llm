"""Check local public Markdown targets and heading anchors; never fetch URLs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prose(text: str) -> str:
    return re.sub(r"^([ \t]*)```[^\n]*\n.*?^\1```[ \t]*$", "", text, flags=re.M | re.S)


def anchors(text: str) -> set[str]:
    text = prose(text)
    result = set(re.findall(r'<a\s+id=[\'"]([^\'"]+)', text))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", text, re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        number = counts.get(slug, 0)
        result.add(slug if number == 0 else f"{slug}-{number}")
        counts[slug] = number + 1
    return result


def check(root: Path = ROOT) -> list[str]:
    failures = []
    files = [
        root / name
        for name in ("README.md", "CONTRIBUTING.md", "LICENSING.md", "THIRD_PARTY_NOTICES.md")
    ]
    files += sorted((root / "docs").glob("*.md"))
    files += sorted((root / "iris_sounds").glob("*.md"))
    for source in files:
        text = prose(source.read_text(encoding="utf-8"))
        targets = re.findall(r"\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", text)
        targets += re.findall(r"^\[[^\]]+\]:\s*(\S+)", text, re.M)
        for target in targets:
            link = urlsplit(target.strip("<>"))
            if link.scheme or link.netloc:
                continue
            path = (source.parent / unquote(link.path)).resolve() if link.path else source
            problem = None
            if not path.exists():
                problem = "missing target"
            elif (
                link.fragment
                and path.suffix == ".md"
                and unquote(link.fragment) not in anchors(path.read_text(encoding="utf-8"))
            ):
                problem = "missing anchor"
            if problem:
                failures.append(f"{source.relative_to(root)}: {problem}: {target}")
    return failures


if __name__ == "__main__":
    errors = check()
    print("\n".join(errors) if errors else "Public Markdown links and anchors passed.")
    sys.exit(bool(errors))
