"""Run pytest from any directory, optionally collecting branch coverage.

Pass --browser and/or --artifacts through to pytest for the prepared opt-in checks.
This command does not install dependencies or download browsers/models.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--coverage", action="store_true")
    args, pytest_args = parser.parse_known_args()
    root = Path(__file__).resolve().parents[1]
    prefix = [sys.executable, "-m"]
    command = [*prefix, "coverage", "run", "-m"] if args.coverage else prefix
    result = subprocess.run([*command, "pytest", "-q", *pytest_args], cwd=root, check=False)
    if not args.coverage:
        return result.returncode
    (root / ".cache" / "checks").mkdir(parents=True, exist_ok=True)
    reports = [
        subprocess.run([*prefix, "coverage", "json"], cwd=root, check=False).returncode,
        subprocess.run([*prefix, "coverage", "report"], cwd=root, check=False).returncode,
        subprocess.run(
            [sys.executable, "scripts/report_coverage.py"], cwd=root, check=False
        ).returncode,
    ]
    return result.returncode or next((code for code in reports if code), 0)


if __name__ == "__main__":
    raise SystemExit(main())
