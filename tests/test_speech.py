import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from speech_recognition import AudioData

from iris_audio import pcm_samples
from iris_settings import Settings


def test_audio_is_resampled_and_normalised_from_signed_pcm() -> None:
    audio = AudioData(np.array([-32768, 0, 32767], dtype="<i2").tobytes(), 16000, 2)
    np.testing.assert_allclose(pcm_samples(audio), [-1, 0, 32767 / 32768])
    low_rate = AudioData(np.zeros(8000, dtype="<i2").tobytes(), 8000, 2)
    assert len(pcm_samples(low_rate)) > 15000


def test_whisper_uses_local_files_and_consumes_transcription_generator() -> None:
    from iris_stt_engines.whisper_offline import STT, load_model

    backend = Mock()
    backend.transcribe.return_value = (
        iter([SimpleNamespace(text=" Hello "), SimpleNamespace(text="there.")]),
        object(),
    )
    constructor = Mock(return_value=backend)
    with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
        stt = STT(Settings())
        assert constructor.call_args.kwargs["local_files_only"] is True
        audio = AudioData(bytes(32000), 16000, 2)
        assert stt.recognize(audio) == "Hello there."
        assert stt.recognize(AudioData(b"", 16000, 2)) == ""
        load_model(Settings(), download=True)
        assert constructor.call_args.kwargs["local_files_only"] is False


def test_missing_whisper_model_has_setup_instruction() -> None:
    from iris_stt_engines.whisper_offline import STT

    constructor = Mock(side_effect=OSError("missing"))
    with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=constructor)}):
        with pytest.raises(ValueError, match="--download-models"):
            STT(Settings())
