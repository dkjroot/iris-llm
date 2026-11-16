"""Run the offline regression suite from any directory."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *sys.argv[1:]],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
