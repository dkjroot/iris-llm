"""
If you don't mind your voice sample being sent out to the internet, google's recognizer is built into the
speech_recognition library, is easy to use, fast and does a great job.
"""
print("Loading imports...")
import speech_recognition as sr
import time

if __name__ == "__main__":
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

            google_text = r.recognize_google(audio).lower()

            # And grab the end time
            end_time = time.time()

            # Print the result
            print(f'google heard: "{google_text}", took {"%.2f" % (end_time - start_time)}s')

            # Exit if the user said "exit"
            if google_text == 'exit':
                exit()

        except Exception as e:
            print("Exception {0}".format(e))
