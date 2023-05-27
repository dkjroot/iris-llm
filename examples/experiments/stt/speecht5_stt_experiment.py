"""
Uses MS speecht5 model to predict text from your speech.
"""
print("Loading imports...")
import torch
from transformers import SpeechT5Processor, SpeechT5ForSpeechToText
import speech_recognition as sr
from pydub import AudioSegment
import io
import time

if __name__ == "__main__":
    # Load models
    print("Loading models...")
    t5_tts_processor = SpeechT5Processor.from_pretrained("microsoft/speecht5_asr")
    t5_tts_model = SpeechT5ForSpeechToText.from_pretrained("microsoft/speecht5_asr")

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
            t5_tts_inputs = t5_tts_processor(audio=x, sampling_rate=16000, return_tensors='pt')

            # Use the model to predict the spoken text
            t5_tts_predicted_ids = t5_tts_model.generate(**t5_tts_inputs, max_length=2000)
            t5_tts_text = (t5_tts_processor.batch_decode(t5_tts_predicted_ids, skip_special_tokens=True)[0]).lower()

            # And grab the end time
            end_time = time.time()

            # Print the result
            print(f't5 heard: "{t5_tts_text}", took {"%.2f" % (end_time - start_time)}s')

            # Exit if the user said "exit"
            if t5_tts_text == 'exit':
                exit()

        except Exception as e:
            print("Exception {0}".format(e))
