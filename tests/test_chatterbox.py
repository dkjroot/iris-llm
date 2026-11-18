import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import iris
from iris_settings import Settings
from iris_tts_engines import chatterbox, open_tts, voice_cli


def test_speech_download_uses_selected_synthesizer() -> None:
    settings = Settings(stt_engine="google", allow_remote=True, tts_engine="chatterbox")
    with patch.object(chatterbox, "run_setup") as setup:
        iris.download_models(settings)
    setup.assert_called_once_with(settings, "download")


def test_voice_cli_prepares_wav_in_private_directory(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"home_service_url": "https://example.test"}', encoding="utf-8")
    wav = tmp_path / "sample.wav"
    wav.touch()
    with (
        patch.object(voice_cli, "VOICE_DIRECTORY", tmp_path),
        patch.object(voice_cli, "run_setup") as setup,
    ):
        assert iris.main(["voice", "--config", str(config), "prepare", str(wav), "../escape"]) == 1
        setup.assert_not_called()
        assert iris.main(["voice", "--config", str(config), "prepare", str(wav), "test-voice"]) == 0
        assert setup.call_args.args[1:] == (
            "prepare",
            "--reference",
            str(wav),
            "--voice",
            str(tmp_path / "test-voice.pt"),
        )


def test_worker_uses_prepared_voice_and_stays_open_across_replies(tmp_path: Path) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import base64
import json
import struct
import sys
from pathlib import Path

assert Path(sys.argv[-1]).name == 'test-voice.pt'
print(json.dumps({'status': 'ready'}))
for number, line in enumerate(sys.stdin, start=1):
    request = json.loads(line)
    assert request['text'] == 'Hello!'
    pcm = base64.b64encode(struct.pack('<f', number / 4)).decode('ascii')
    print(json.dumps({'status': 'audio', 'rate': 24000, 'samples': pcm,
                      'request_id': request['request_id']}))
    print(json.dumps({'status': 'done', 'request_id': request['request_id']}))
""",
        encoding="utf-8",
    )
    settings = Settings(
        tts_engine="chatterbox",
        chatterbox_python=Path(sys.executable),
        chatterbox_voice="test-voice",
    )
    with (
        patch.object(chatterbox, "VOICE_DIRECTORY", tmp_path),
        patch.object(chatterbox, "WORKER", worker),
    ):
        with pytest.raises(ValueError, match="voice is missing"), open_tts(settings):
            pytest.fail("Missing voice must prevent worker startup")
        (tmp_path / "test-voice.pt").touch()
        with open_tts(settings) as tts:
            first, rate = tts.synthesize("**Hello** 🙂!")
            second, _ = tts.synthesize("👩🏽‍💻 **Hello**!")
    assert rate == 24000
    np.testing.assert_array_equal(first, [0.25])
    np.testing.assert_array_equal(second, [0.5])


@pytest.mark.parametrize(
    "bad_reply", ["'x' * 129", "'not-json\\n'", '\'{\\"status\\":\\"audio\\"}\'']
)
def test_worker_invalid_or_unterminated_frame_fails_and_closes(tmp_path, monkeypatch, bad_reply):
    worker = tmp_path / "broken_worker.py"
    worker.write_text(
        "import sys\n"
        'print(\'{"status":"ready"}\', flush=True)\n'
        "sys.stdin.readline()\n"
        f"sys.stdout.write({bad_reply})\n"
        "sys.stdout.flush()\n"
    )
    monkeypatch.setattr("iris_tts_engines.worker.MAX_WORKER_FRAME", 128)
    with chatterbox.open_worker(
        [sys.executable, "-I", "-u", str(worker)], chatterbox.Chatterbox, "Synthetic", {}
    ) as tts:
        with pytest.raises(RuntimeError):
            tts.synthesize("Hello")
    assert tts.process.poll() is not None
