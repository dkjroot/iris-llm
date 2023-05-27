"""IRIS - Intelligent Residential Interface System

This is the best version, but it's also the biggest hack. This is because when I wrote it, the oobabooga API wasn't
working, and so I just interfaced directly with the internals of Oobabooga. It's a very messy thing to do, but it works
really well... I do plan to update it once I get the Oobabooga API working. It might stop working if the Oobabooga
internals change from how they were when I wrote/hacked together the script!

You'll need to have Oobabooga installed and fully working before you try this.

The advantages of Oobabooga are that it can load GPU and CPU models, on Windows, without installing wsl, and it somehow
manages to get full answers out of llamacpp models which I couldn't do when I tried to use llamacpp directly. It also
has the nice built-in chat characters feature, and is all-round awesome. I apologise to the Oobabooga devs for what I
did you your code here ;) I will swap to the API once I get it working I promise.

To use it:
First copy start_windows.py (in your oobabooga directory) and name it start_iris.py.  In there change two things
(that I can remember!):

- Close to the top, change CMD_FLAGS to '--chat --auto-device --chat --model-menu --xformers'

- In the function run_model(), change it to run iris_server.py:
     run_cmd(f"python iris_server.py {CMD_FLAGS}", environment=True)

Next, copy iris_v6_offline_oobabooga.py into the text-generation-webui directory, naming it iris_server.py
(I did warn you this was hacky!)

Now when you run 'python start_iris.py', you should not get a gradio webui as you normally would with Oobabooga, you
should get our speech interface, but inside it's using all that Oobabooga goodness.

Once again, I reiterate that this would be a lot better if we could just use the Oobabooga API from a script more like
our previous ones, but at the time of writing the API didn't work.
"""

# I won't comment all the below stuff very extensively, you've seen it in previous versions and this script will
# definitely be deleted/change significantly when I get the Oobabooga API working.
import pyttsx3
import sounddevice as sd

tts_engine = pyttsx3.init()
tts_engine.setProperty('rate', 200)
tts_engine.setProperty('voice', \
                       'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Speech\\Voices\\Tokens\\MSTTS_V110_enAU_CatherineM')


# Speak the text string
def speak_text(txt):
    sd.stop()
    tts_engine.say('<pitch middle="6">' + txt + '</pitch>')
    tts_engine.runAndWait()

# A bit of feedback to show that we're loading
# clear the console
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("Iris: Loading models, please wait... Choose a model from the menu if shown.")
speak_text("Loading models, please wait... Choose a model from the menu if shown.")

# load sound effects, and play a waiting noise while we load the models
from pydub import AudioSegment
from pydub.playback import play
import numpy as np
chirp = AudioSegment.from_mp3('chirp.mp3')
working = AudioSegment.from_mp3('workinglongquiet.mp3')
bleep = AudioSegment.from_mp3('bleep.mp3')
longbleep = AudioSegment.from_mp3('longbleep.mp3')
workinga = np.array(working.get_array_of_samples())
sd.play(workinga, working.frame_rate*2)

# now load all the other stuff and the models
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
import speech_recognition as sr
import logging
import warnings

warnings.filterwarnings('ignore', category=UserWarning, message='TypedStorage is deprecated')
logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)

import io
import json
import sys
from pathlib import Path

import torch
from modules import chat, shared, training, ui, utils
from modules.LoRA import add_lora_to_model
from modules.models import load_model, load_soft_prompt, unload_model
from server import update_model_parameters, get_model_specific_settings

# Load the w2v2 model for STT inference
w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
r = sr.Recognizer()


# Play the feedback sound to say that the system is busy thinking
def play_working_sound():
    sd.stop()
    sd.play(workinga, working.frame_rate*2, loop=True) # start playing working noise


# Listen for the user to speak, infer and return the text using the STT model
def listen_for_user_input():
    with sr.Microphone(sample_rate=16000) as source:
        # listen
        r.adjust_for_ambient_noise(source, duration=0.2)
        print(f"You: ", end='', flush=True)
        play(chirp) # ready to listen bleep
        print(f"(listening...) ", end='', flush=True)
        audio = r.listen(source)
        print(f"(heard...) ", end='', flush=True)
        play(chirp) # listening finished / parsing text bleep

        # parse and tokenize audio
        play_working_sound()
        data = io.BytesIO(audio.get_wav_data())
        clip = AudioSegment.from_file(data)
        x = torch.FloatTensor(clip.get_array_of_samples())

        inputs = w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
        logits = w2v2_model(inputs).logits
        tokens = torch.argmax(logits, axis=-1)
        text = str(w2v2_tokenizer.batch_decode(tokens)[0]).lower()
        print(text)
        return text


# Exit the script if the text is "exit"
def exit_if_exit(text):
    if text == 'exit':
        print("Iris: Shutting down")
        speak_text("shutting down")
        exit()


# Similar to the fastai version we don't have to track our own conversation history, the library does it for us
def infer_response(user_text, state):
    last_hist_yield = ""
    for h in chat.generate_chat_reply(user_text, state):
        last_hist_yield = h
    text = last_hist_yield[-1][1]
    print("Iris:", text)
    speak_text(text)


# What to say the first time we see the user
def first_time_prompt():
        print("Iris: Nice to see you again, how can I help?")
        speak_text("Nice to see you again, how can I help?")


# Take input from the user (listen for the user's voice and return it, exit if they said "exit")
def prompt_for_input():
    try:
        text = listen_for_user_input()
        exit_if_exit(text)
        return text
    except Exception as e:
        print("Exception {0}".format(e))
        return ""


# Main
if __name__ == "__main__":
    # This code was borrowed from the main function of the Oobabooga server.py.  It belongs to them, not me, so follow
    # their license and give them credit.

    # Loading custom settings
    settings_file = None
    if shared.args.settings is not None and Path(shared.args.settings).exists():
        settings_file = Path(shared.args.settings)
    elif Path('settings.json').exists():
        settings_file = Path('settings.json')

    if settings_file is not None:
        logging.info(f"Loading settings from {settings_file}...")
        new_settings = json.loads(open(settings_file, 'r').read())
        for item in new_settings:
            shared.settings[item] = new_settings[item]

    # Set default model settings based on settings.json
    shared.model_config['.*'] = {
        'wbits': 'None',
        'model_type': 'None',
        'groupsize': 'None',
        'pre_layer': 0,
        'mode': shared.settings['mode'],
        'skip_special_tokens': shared.settings['skip_special_tokens'],
        'custom_stopping_strings': shared.settings['custom_stopping_strings'],
    }

    shared.model_config.move_to_end('.*', last=False)  # Move to the beginning

    available_models = utils.get_available_models()

    # Model defined through --model
    if shared.args.model is not None:
        shared.model_name = shared.args.model

    # Only one model is available
    elif len(available_models) == 1:
        shared.model_name = available_models[0]

    # Select the model from a command-line menu
    elif shared.args.model_menu:
        if len(available_models) == 0:
            logging.error('No models are available! Please download at least one.')
            sys.exit(0)
        else:
            print('The following models are available:\n')
            for i, model in enumerate(available_models):
                print(f'{i + 1}. {model}')

            print(f'\nWhich one do you want to load? 1-{len(available_models)}\n')
            i = int(input()) - 1
            print()

        shared.model_name = available_models[i]

    # If any model has been selected, load it
    if shared.model_name != 'None':
        model_settings = get_model_specific_settings(shared.model_name)
        shared.settings.update(model_settings)  # hijacking the interface defaults
        update_model_parameters(model_settings, initial=True)  # hijacking the command-line arguments

        # Load the model
        shared.model, shared.tokenizer = load_model(shared.model_name)
        if shared.args.lora:
            add_lora_to_model(shared.args.lora)

    # Borrowed Oobabooga code ends about here - though we're using their library extensively - basically we've just
    # put a voice interface in front of the Oobabooga chat engine.

    # Set up the characters
    name1 = 'You'
    name2 = 'Iris'

    # I haven't really tuned the character, this is what I used last time I was messing with it.
    my_greeting = "*Iris enters the room with a joyful bounce in her step. She takes a seat next to you, and smiles \
like she hasn't seen you in years*\nLets talk about art and poetry, physics and philosophy. *She grins and \
you can tell she's keen to talk* Tell me what you love!"

    my_context = "Iris's Persona: Iris is intelligent, fun and chatty. She has a joyful outlook on life and a reverent \
love of nature.  She is a writer with an artistic mind.  She talks a lot about deep things and is not shy to share her \
opinions.  She talks in long sentences and paragraphs .  She asks a lot of questions, because she really wants to get \
to know you.  She loves science and philosophy, art, music and literature and she has a deeply creative poetic soul.  \
She doesn't talk about making plans to meet in the future, she wants to talk right now, about things that are real.\n" \
"You: What are you reading there?\nIris: It's the screenplay to the movie Aliens\nYou: I've read that!  \
It's a great read, I loved it.\nIris: *She smiles enthusiastically* Wow that's amazing that we \
have the same taste!  Did you like it?\nYou: I loved it, it's so action-packed, and I felt like I understood the \
characters better than I did when I saw the move\nIris: What did you like about it, who is your favourite character?\n \
You: I loved Ripley of course, and I found the story gripping, but moving too.  I love space, so the subject really \
worked for me.\nIris: Me too!  What other things do you love?  For you, what makes life worth living? \
We're going to get along great, I can tell.\n"

    # I'm not sure this really made any difference in the end, they're basically the same
    my_state_gptq = {'max_new_tokens': 200, 'seed': -1.0, 'temperature': 0.7, 'top_p': 0.5, 'top_k': 40,
                     'typical_p': 1, 'repetition_penalty': 1.2, 'encoder_repetition_penalty': 1,
                     'no_repeat_ngram_size': 0, 'min_length': 0, 'do_sample': True, 'penalty_alpha': 0, 'num_beams': 1,
                     'length_penalty': 1, 'early_stopping': False, 'add_bos_token': True, 'ban_eos_token': False,
                     'truncation_length': 2048, 'custom_stopping_strings': '', 'skip_special_tokens': True,
                     'preset_menu': 'Default', 'stream': True, 'name1': name1, 'name2': name2, 'greeting': my_greeting,
                     'context': my_context, 'name1_instruct': name1, 'name2_instruct': name2, 'chat_prompt_size': 2048,
                     'chat_generation_attempts': 1, 'stop_at_newline': False, 'mode': 'chat', 'cpu_memory': 0,
                     'auto_devices': True, 'disk': False, 'cpu': False, 'bf16': False, 'load_in_8bit': False,
                     'wbits': '4', 'groupsize': '128', 'model_type': 'llama', 'pre_layer': 0, 'threads': 0,
                     'n_batch': 512, 'no_mmap': False, 'mlock': False, 'n_gpu_layers': 0, 'gpu_memory_0': 0}

    my_state_ggml = {'max_new_tokens': 200, 'seed': -1.0, 'temperature': 0.7, 'top_p': 0.5, 'top_k': 40, 'typical_p': 1,
                     'repetition_penalty': 1.2, 'encoder_repetition_penalty': 1, 'no_repeat_ngram_size': 0,
                     'min_length': 0, 'do_sample': True, 'penalty_alpha': 0, 'num_beams': 1, 'length_penalty': 1,
                     'early_stopping': False, 'add_bos_token': True, 'ban_eos_token': False, 'truncation_length': 2048,
                     'custom_stopping_strings': '', 'skip_special_tokens': True, 'preset_menu': 'Default',
                     'stream': True, 'name1': name1, 'name2': name2, 'greeting': my_greeting, 'context': my_context,
                     'name1_instruct': name1, 'name2_instruct': name2, 'chat_prompt_size': 2048,
                     'chat_generation_attempts': 1, 'stop_at_newline': False, 'mode': 'chat', 'cpu_memory': 0,
                     'auto_devices': True, 'disk': False, 'cpu': False, 'bf16': False, 'load_in_8bit': False,
                     'wbits': 'None', 'groupsize': 'None', 'model_type': 'None', 'pre_layer': 0, 'threads': 0,
                     'n_batch': 512, 'no_mmap': False, 'mlock': False, 'n_gpu_layers': 0, 'gpu_memory_0': 0}

    # Choose an initial state depending on the model
    my_state = my_state_gptq
    if 'ggml' in shared.model_name:
        my_state = my_state_ggml

    # Repeatedly prompt and respond
    first_time_prompt()
    while True:
        text_from_user = prompt_for_input()
        infer_response(text_from_user, my_state)
