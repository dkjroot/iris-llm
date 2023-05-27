"""
Uses wav2vec2 with the jonatasgrosman/wav2vec2-large-xlsr-53-english model for inference.
In my testing I think it's faster and more accurate than speecht5
"""
print("Loading imports...")
import torch
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
import speech_recognition as sr
from pydub import AudioSegment
import io
import time

if __name__ == "__main__":
    # Load models
    print("Loading models...")
    w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
    w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')

    # speech_recognition library can detect when you start and stop speaking, and outputs a wav of your speech
    r = sr.Recognizer()
    while True:
        try:
            # Listen for a snip of speech - you need to be in a quiet environment,
            # and end your sentence with silence so it knows when to stop listening
            with sr.Microphone(sample_rate=16000) as source:
                r.adjust_for_ambient_noise(source, duration=0.2)
                print('Say something now (say "exit" to exit):')
                audio = r.listen(source)

            print("Processing...")

            # Grab the start time so we can compare performance vs other models
            start_time = time.time()

            # Convert to inputs for the model..
            # Clip the audio from sr
            data = io.BytesIO(audio.get_wav_data())
            clip = AudioSegment.from_file(data)
            # Convert to tensors for the TTS model
            x = torch.FloatTensor(clip.get_array_of_samples())

            # Convert x to the right format for the model
            w2v2_inputs = w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt', padding='longest').input_values

            # Use the model to predict the spoken text
            w2v2_logits = w2v2_model(w2v2_inputs).logits
            w2v2_tokens = torch.argmax(w2v2_logits, axis=-1)
            w2v2_text = str(w2v2_tokenizer.batch_decode(w2v2_tokens)[0]).lower()

            # And grab the end time
            end_time = time.time()

            # Print the result
            print(f'wav2vec2 heard: "{w2v2_text}", took {"%.2f" % (end_time - start_time)}s')

            # Exit if the user said "exit"
            if w2v2_text == 'exit':
                exit()

        except Exception as e:
            print("Exception {0}".format(e))
