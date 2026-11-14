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
    assert json.loads(line) == 'Hello!'
    pcm = base64.b64encode(struct.pack('<f', number / 4)).decode('ascii')
    print(json.dumps({'status': 'audio', 'rate': 24000, 'samples': pcm}))
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
            first, rate = tts.synthesize("**Hello**!")
            second, _ = tts.synthesize("**Hello**!")
    assert rate == 24000
    np.testing.assert_array_equal(first, [0.25])
    np.testing.assert_array_equal(second, [0.5])
