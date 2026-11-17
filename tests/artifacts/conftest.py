"""Build away from the checkout so editable metadata cannot hide missing files."""

import json
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def installed_artifact(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    work = tmp_path_factory.mktemp("wheel")
    source, target = work / "source", work / "installed"
    source.mkdir()
    for path in root.glob("*.py"):
        shutil.copy2(path, source / path.name)
    for name in (
        "pyproject.toml",
        "MANIFEST.in",
        "CONTRIBUTING.md",
        "package.json",
        "package-lock.json",
        ".node-version",
        "uv.lock",
        "README.md",
        "LICENSE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        shutil.copy2(root / name, source / name)
    for path in root.glob("iris_*"):
        if path.is_dir():
            shutil.copytree(path, source / path.name, ignore=shutil.ignore_patterns("__pycache__"))

    for name in ("docs", "scripts", "systemd", "tests"):
        shutil.copytree(root / name, source / name, ignore=shutil.ignore_patterns("__pycache__"))
    # Deliberately plant local material to ensure the distribution excludes it.
    for name in ("AGENTS.md", "DEVELOPMENT.md", "local-config.json", ".env"):
        (source / name).write_text("synthetic-private-sentinel", encoding="utf-8")
    (source / "agent_docs").mkdir()
    (source / "agent_docs" / "notes.md").write_text("synthetic-private-sentinel", encoding="utf-8")

    def run(*args):
        result = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--no-isolation",
        "--outdir",
        str(work),
        str(source),
    )
    (sdist,) = work.glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        names = ["/".join(Path(name).parts[1:]) for name in archive.getnames()]
        assert {
            "CONTRIBUTING.md",
            "docs/updates.md",
            "docs/releasing.md",
            "docs/start-here.md",
            "docs/technical-guide.md",
            "docs/configuration-reference.md",
            "docs/verification.md",
            "docs/setup-windows.md",
            "docs/setup-macos.md",
            "docs/setup-linux.md",
            "docs/setup-devices.md",
            "docs/matter-controller.md",
            "scripts/check_docs.py",
            "scripts/build_reference.py",
            "scripts/iris_autoupdate.py",
            "systemd/iris-autoupdate.service",
            "package-lock.json",
            "tests/test_documentation.py",
            "uv.lock",
        } <= set(names)
        assert not any(
            name in {"AGENTS.md", "DEVELOPMENT.md", "local-config.json", ".env"}
            or name.startswith("agent_docs/")
            for name in names
        )
        archive.extractall(work / "unpacked", filter="data")
    (unpacked,) = (work / "unpacked").iterdir()
    run(sys.executable, str(unpacked / "scripts/check_docs.py"))
    run(sys.executable, str(unpacked / "scripts/build_reference.py"), "--check")
    run(
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--no-isolation",
        "--outdir",
        str(work),
        str(unpacked),
    )
    (wheel,) = work.glob("*.whl")
    run(
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-compile",
        "--no-index",
        "--disable-pip-version-check",
        "--target",
        str(target),
        str(wheel),
    )
    # -I -S disables cwd, PYTHONPATH, user-site and editable .pth initialization.
    # Borrow installed third-party dependencies, but never the editable project.
    dependencies = list(dict.fromkeys(sysconfig.get_paths()[k] for k in ("purelib", "platlib")))
    script = r"""
import contextlib, importlib, importlib.metadata, importlib.util, io, json, sys
from importlib.resources import files
from pathlib import Path
target = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(target), *json.loads(sys.argv[2])]
modules = {}
for name in ("iris", "configure", "configure_client", "iris_paths", "iris_turn", "iris_frames",
             "iris_droid.client", "iris_droid.server", "iris_app.runtime", "iris_domain.session",
             "iris_droid.controller", "iris_tts_engines.worker",
             "iris_home.service", "iris_memory.cli", "iris_tts_engines.pocket_worker"):
    spec = importlib.util.find_spec(name)
    modules[name] = spec is not None
    if spec is not None:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(target), module.__file__
assert not importlib.import_module("iris_paths").runtime_data_root().is_relative_to(target)
entries = []
dist = importlib.metadata.distribution("iris-llm")
for entry in dist.entry_points:
    if entry.group == "console_scripts":
        function = entry.load()
        assert callable(function)
        entries.append(entry.name)
for module_name in ("iris", "configure", "configure_client", "iris_droid.client",
                    "iris_droid.server", "iris_home.service"):
    sys.argv = [module_name, "--help"]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            importlib.import_module(module_name).main()
        except SystemExit as error:
            assert error.code == 0
    assert "usage:" in output.getvalue().lower(), module_name
assets = {}
for package, name in (
    ("iris_sounds", "cancelled.wav"), ("iris_sounds", "context-cleared.wav"),
    ("iris_droid", "web/chat-controller.js"),
    ("iris_droid", "web/chat.js"), ("iris_droid", "web/speech.js"),
    ("iris_droid", "web/index.html"), ("iris_droid", "web/commands.json"),
    ("iris_droid", "face_sprites/sprites.json"), ("iris_droid", "face_sprites/sprout.png"),
    ("iris_tts_engines", "pocket_worker.py"), ("iris_tts_engines", "chatterbox_worker.py"),
    ("iris_tts_engines", "pocket-requirements.txt"),
):
    assets[package + "/" + name] = len(files(package).joinpath(name).read_bytes())
assert all(assets.values())
print(json.dumps({"modules": modules, "entrypoints": entries, "assets": assets}))
"""
    return json.loads(
        run(sys.executable, "-I", "-S", "-c", script, str(target), json.dumps(dependencies))
    )
