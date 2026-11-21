"""The original recogniser, with normalised PCM and no implicit model downloads."""

from __future__ import annotations

from speech_recognition import AudioData

from iris_audio import pcm_samples
from iris_settings import Settings
from iris_turn import check_deadline

_WINDOW_SAMPLES = 16000 * 30


class STT:
    def __init__(self, settings: Settings) -> None:
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        try:
            self.processor = Wav2Vec2Processor.from_pretrained(
                settings.tokenizer_model, local_files_only=True
            )
            self.model = Wav2Vec2ForCTC.from_pretrained(settings.ctc_model, local_files_only=True)
        except (OSError, ValueError) as error:
            raise ValueError(
                "Wav2Vec2 model isn't available locally. Download the configured tokenizer_model "
                "and ctc_model before running offline."
            ) from error
        if settings.device == "cuda" or (
            settings.device == "auto" and hasattr(torch, "cuda") and torch.cuda.is_available()
        ):
            self.device = "cuda"
            self.model.to(self.device)
        else:
            self.device = "cpu"
        self.model.eval()

    def recognize(self, audio: AudioData) -> str:
        import torch

        samples = pcm_samples(audio)
        if not samples.size:
            return ""
        parts: list[str] = []
        for start in range(0, samples.size, _WINDOW_SAMPLES):
            check_deadline()
            chunk = samples[start : start + _WINDOW_SAMPLES]
            inputs = self.processor(chunk, sampling_rate=16000, return_tensors="pt")
            input_values = (
                inputs.input_values.to(self.device) if self.device != "cpu" else inputs.input_values
            )
            with torch.inference_mode():
                tokens = self.model(input_values).logits.argmax(dim=-1)
            decoded = str(self.processor.batch_decode(tokens)[0]).lower().strip()
            if decoded:
                parts.append(decoded)
        return " ".join(parts)

    def is_offline(self) -> bool:
        return True
