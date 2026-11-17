"""Install a built wheel and its selected extras into a fresh disposable environment."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

EXTRAS = {"base": "", "server": "whisper,kokoro", "memory": "memory", "vad": "vad"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=EXTRAS)
    parser.add_argument("--wheel-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = list(args.wheel_dir.resolve().glob("*.whl"))
    if len(wheels) != 1:
        parser.error("Use a directory containing exactly one Iris wheel")
    with tempfile.TemporaryDirectory(prefix="iris-clean-install-") as directory:
        work = Path(directory)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        extra = EXTRAS[args.profile]
        requirement = str(wheels[0]) + (f"[{extra}]" if extra else "")

        def run(*command: str) -> None:
            subprocess.run([str(python), *command], cwd=work, check=True, timeout=1200)

        run("-m", "pip", "install", "--disable-pip-version-check", requirement)
        run("-m", "pip", "check")
        run(
            "-I",
            "-c",
            "import iris, iris_droid.server, iris_droid.client, iris_home.service; "
            "from pathlib import Path; "
            "assert Path(iris.__file__).resolve().is_relative_to("
            f"Path({json.dumps(str(environment.resolve()))}))",
        )
        smoke = work / "check_profiles.py"
        shutil.copy2(Path(__file__).with_name("check_profiles.py"), smoke)
        run("-I", str(smoke), args.profile)
        for module in ("iris", "iris_droid.server", "iris_droid.client", "iris_home.service"):
            run("-I", "-m", module, "--help")


if __name__ == "__main__":
    main()
