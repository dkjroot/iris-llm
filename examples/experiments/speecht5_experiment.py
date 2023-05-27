"""
An example of using a local model for text to speech (here, MS SpeechT5, but others are available on huggingface and
might be better).  Although it's a voice that sounds very realistic to my ears, as you will see, it fails all three of
my natural language pronunciation tests.  That and the added delays loading models etc. are the reasons I've tended
to use gTTS online or Windows SAPI offline.
"""
print("Loading imports...")
from transformers import SpeechT5Processor, SpeechT5ForTextToSpeech
from transformers import SpeechT5HifiGan
from datasets import load_dataset
import sounddevice as sd
import torch

def speak(text):
    print(f"Saying '{text}'")
    inputs = tts_processor(text=text, return_tensors="pt")
    speech = tts_model.generate_speech(inputs["input_ids"], tts_speaker_embeddings, vocoder=tts_vocoder)
    sd.play(speech.numpy(), 16000, blocking=True)


if __name__ == "__main__":
    # Load the model
    print("Loading models...")
    voice = 6913
    tts_processor = SpeechT5Processor.from_pretrained("microsoft/speecht5_tts")
    tts_model = SpeechT5ForTextToSpeech.from_pretrained("microsoft/speecht5_tts")
    tts_vocoder = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan")
    tts_embeddings_dataset = load_dataset("Matthijs/cmu-arctic-xvectors", split="validation")
    tts_speaker_embeddings = torch.tensor(tts_embeddings_dataset[voice]["xvector"]).unsqueeze(0)

    speak("St Peters")
    speak("101 Peter St.")
    speak("The answer is 1034")