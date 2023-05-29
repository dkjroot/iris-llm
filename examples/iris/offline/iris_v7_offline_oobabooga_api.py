"""IRIS - Intelligent Residential Interface System
This version uses the Oobabooga API, which is the right way to do it, avoiding all the hacky nonsense that was in the
old Oobabooga client (v6, which is now completely deprecated by this v7)

It works, but I haven't polished it yet - it will become the basis for the actual Iris script, which I can now start
working now that I've got this basic thing working!

You need to have Oobabooga completely working (a character named "Iris" set up, model loaded with model parameters
saved, and running with --api to start the API listening on port 5000.
"""
# breaking the imports up so that we can give a bit of feedback during the long process of loading the models!
import pyttsx3
import sounddevice as sd

# We'll use MS SAPI for speech - it's fast and pronounces text well, although the voice isn't the most realistic.
tts_engine = pyttsx3.init()
tts_engine.setProperty('rate', 200)
tts_engine.setProperty('voice', 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\MSTTS_V110_enAU_CatherineM')

# Say the text out loud (using an offline model now instead of an online service)
def speak(txt):
    sd.stop()
    tts_engine.say('<pitch middle="6">' + txt + '</pitch>')
    tts_engine.runAndWait()

# Clear the terminal
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("Iris: Loading, please wait...")
speak("Loading, please wait...")

from pydub import AudioSegment
from pydub.playback import play
import numpy as np

# And now we can play a waiting noise while we load the rest - reason for using sound device is because it can play
# in the background while we continue to work.  Reason for using numpy is.. well it's how I got this to work... I think
# there are probably better ways :)
working = AudioSegment.from_mp3('workinglongquiet.mp3')
workinga = np.array(working.get_array_of_samples())
sd.play(workinga, working.frame_rate*2)

# Load some more sounds
chirp = AudioSegment.from_mp3('chirp.mp3')
bleep = AudioSegment.from_mp3('bleep.mp3')
longbleep = AudioSegment.from_mp3('longbleep.mp3')

# Now load all the other imports that can take a long time to load (not too long here but when we start using offline
# models the loading takes a while).  I guess the linter would prefer us to do all our imports at the top, but I like
# to get this feedback during startup.
from collections import deque
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
import speech_recognition as sr
import io
import torch
import requests

# We'll use w2v2 again for speech to text inference
w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')

# Plays the 'I'm busy' bleep sequence indefinitely (until sd.stop() is called again)
def play_working_sound():
    sd.stop()
    sd.play(workinga, working.frame_rate*2, loop=True)  # start playing working noise


# Grab a sample of the user's speech.  If they say "exit" exit the script, otherwise print the text and return it.
def listen_for_user_input():
    try:
        with sr.Microphone(sample_rate=16000) as source:
            # listen
            r.adjust_for_ambient_noise(source, duration=0.2)
            print(f"You: ", end='', flush=True)
            play(chirp) # "ready to listen" bleep - more useful when we add a wake word!
            # play(bleep) # "listening" bleep
            print(f"(listening...) ", end='', flush=True)
            audio = r.listen(source)
            print(f"(heard...) ", end='', flush=True)
            play(chirp) # listening finished, parsing text about to start

            # parse and tokenize audio
            # Using w2v2 for stt
            data = io.BytesIO(audio.get_wav_data())
            clip = AudioSegment.from_file(data)
            x = torch.FloatTensor(clip.get_array_of_samples())

            inputs = w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
            logits = w2v2_model(inputs).logits
            tokens = torch.argmax(logits, axis=-1)
            text = str(w2v2_tokenizer.batch_decode(tokens)[0]).lower()

            print(text)
            exit_if_exit(text)
            return text
    except Exception as e:
        sd.stop()
        print("Exception {0}".format(e))
        return None


# If text is "exit", exit the script
def exit_if_exit(text):
    if text == 'exit':
        print("Iris: Shutting down")
        speak("shutting down")
        exit()


# Make Iris say a friendly hello
def first_time_prompt():
    sd.stop()
    print("Iris: Nice to see you again, how can I help?")
    speak("Nice to see you again, how can I help?")


# Build the prompt for sending to the model, by zipping together all the human prompts and ai responses so far
def make_prompt(user_input):
    arr = []
    for h, a in zip(human_prompts, ai_replies):
        arr.append([h,a])
    history = {'internal': arr, 'visible': arr}
    return history


# Call Oobabooga API to get a response
def get_response(history, user_input):
    request = {
        'user_input': user_input,
        'history': history,
        'mode': 'chat',  # Valid options: 'chat', 'chat-instruct', 'instruct'
        'character': 'Iris',
        'instruction_template': 'Vicuna-v1.1',
        'your_name': 'You',

        'regenerate': False,
        '_continue': False,
        'stop_at_newline': False,
        'chat_prompt_size': 2048,
        'chat_generation_attempts': 1,
        'chat-instruct_command': 'Continue the chat dialogue below. Write a single reply for the character "<|character|>".\n\n<|prompt|>',

        'max_new_tokens': 250,
        'do_sample': True,
        'temperature': 0.7,
        'top_p': 0.1,
        'typical_p': 1,
        'epsilon_cutoff': 0,  # In units of 1e-4
        'eta_cutoff': 0,  # In units of 1e-4
        'repetition_penalty': 1.18,
        'top_k': 40,
        'min_length': 0,
        'no_repeat_ngram_size': 0,
        'num_beams': 1,
        'penalty_alpha': 0,
        'length_penalty': 1,
        'early_stopping': False,
        'mirostat_mode': 0,
        'mirostat_tau': 5,
        'mirostat_eta': 0.1,
        'seed': -1,
        'add_bos_token': True,
        'truncation_length': 2048,
        'ban_eos_token': False,
        'skip_special_tokens': True,
        'stopping_strings': []
    }

    response = requests.post(URI, json=request)

    if response.status_code == 200:
        result = response.json()['results'][0]['history']
        return result['visible'][-1][1]


# Print and say the reply from the model
def output_ai_reply(reply):
    print("Iris: ", reply)
    speak(reply)


# This will be used repeatedly in a loop - listen for one user input, send to the model, say the response, append
# to the human_prompts and ai_replies queues
def interact_one_cycle():
    try:
        user_input = listen_for_user_input()
        while user_input is None or user_input == "":
            user_input = listen_for_user_input()
        history = make_prompt(user_input)
        result = get_response(history, user_input)
        output_ai_reply(result)
        human_prompts.append(user_input)
        ai_replies.append(result)
    except Exception as e:
        print("Exception {0}".format(e))


# Main
if __name__ == "__main__":
    # Load the speech recognizer
    r = sr.Recognizer()

    # Host and port for the Oobabooga API
    HOST = 'localhost:5000'
    URI = f'http://{HOST}/api/v1/chat'

    # We have to keep track of our own conversation history -
    # we'll use length-limited queue for human prompts and ai replies.
    # A longer history_length will give the system more memory of previous interactions.
    history_length = 50
    human_prompts = deque(maxlen=history_length)
    ai_replies = deque(maxlen=history_length)

    # And the prompts will be primed with these examples (which will scroll off as we reach history_length)
    human_prompts.append("Hey, how are you?")
    ai_replies.append("I'm great thanks!  What's going on with you?")

    # Say hello
    first_time_prompt()

    # And repeatedly listen and respond until the user chooses to exit
    while True:
        interact_one_cycle()
