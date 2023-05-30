# This version uses google's online speech to text engine
from iris_stt_engines.ISTT import ISTT
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
from pydub import AudioSegment
import io
import torch


class STT(ISTT):
    settings = None
    w2v2_tokenizer = None
    w2v2_model = None

    def __init__(self, settings):
        self.settings = settings
        self.w2v2_tokenizer = Wav2Vec2Processor.from_pretrained(settings['tokenizer_model'])
        self.w2v2_model = Wav2Vec2ForCTC.from_pretrained(settings['ctc_model'])

    def recognize(self, audio):
        # parse and tokenize audio
        # Using w2v2 for stt
        data = io.BytesIO(audio.get_wav_data())
        clip = AudioSegment.from_file(data)
        x = torch.FloatTensor(clip.get_array_of_samples())

        inputs = self.w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
        logits = self.w2v2_model(inputs).logits
        tokens = torch.argmax(logits, axis=-1)
        return str(self.w2v2_tokenizer.batch_decode(tokens)[0]).lower()

    def is_offline(self):
        return True
