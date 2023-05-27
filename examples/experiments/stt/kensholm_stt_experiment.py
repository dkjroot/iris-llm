"""
Uses KenshoLM with the jonatasgrosman/wav2vec2-large-xlsr-53-english model for inference, following jonatasgrosman's
"Inference (boosted by a language model)" example.  I may not have it quite right and there may be more that can be
done to improve the responses.  I found it a bit unsatisfying that I needed to write the user's speech out to a file,
but there may be a way to avoid that.  It didn't seem better in this use case than directly using wav2vec in my
experiments and has some extra steps (downloading extra files, writing out the wav file, prints a lot of junk to the
console) that wav2vec alone doesn't have.
You'll need lm.binary and unigrams.txt files like those found at
https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english/tree/main/language_model
"""
print("Loading imports...")
from huggingsound import SpeechRecognitionModel, KenshoLMDecoder
import speech_recognition as sr
import time

if __name__ == "__main__":
    # Load models
    print("Loading models...")
    hs_model = SpeechRecognitionModel('jonatasgrosman/wav2vec2-large-xlsr-53-english')
    audio_paths = ["tmp.wav"]
    hs_decoder = KenshoLMDecoder(hs_model.token_set, lm_path='lm.binary', unigrams_path='unigrams.txt')

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

            # Convert to inputs for the model.. (here, writing out a wav file)
            with open(audio_paths[0], 'wb') as f:
                f.write(audio.get_wav_data())
            hs_text = hs_model.transcribe(audio_paths, decoder=hs_decoder)[0]['transcription']

            # And grab the end time
            end_time = time.time()

            # Print the result
            print(f'kensholm heard: "{hs_text}", took {"%.2f" % (end_time - start_time)}s')

            # Exit if the user said "exit"
            if hs_text == 'exit':
                exit()

        except Exception as e:
            print("Exception {0}".format(e))
