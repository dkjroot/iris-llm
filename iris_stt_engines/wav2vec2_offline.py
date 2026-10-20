"""The original recogniser, with normalised PCM and no implicit model downloads."""

from __future__ import annotations

from speech_recognition import AudioData

from iris_audio import pcm_samples
from iris_settings import Settings


class STT:
    def __init__(self, settings: Settings) -> None:
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        self.processor = Wav2Vec2Processor.from_pretrained(
            settings.tokenizer_model, local_files_only=True
        )
        self.model = Wav2Vec2ForCTC.from_pretrained(settings.ctc_model, local_files_only=True)
        self.model.eval()

    def recognize(self, audio: AudioData) -> str:
        import torch

        samples = pcm_samples(audio)
        if not samples.size:
            return ""
        inputs = self.processor(samples, sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            tokens = self.model(inputs.input_values).logits.argmax(dim=-1)
        return str(self.processor.batch_decode(tokens)[0]).lower()

    def is_offline(self) -> bool:
        return True
