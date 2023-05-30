# This version uses the Oobabooga API (offline)
# You need to have Oobabooga completely working (a character named "Iris" set up, model loaded with model parameters
# saved, and running with --api to start the API listening on port 5000).
from iris_chat_engines.IChatEngine import IChatEngine
import requests
import os
from collections import deque
import speech_recognition as sr


class ChatEngine(IChatEngine):
    settings = None
    stt_inst = None
    ptt_inst = None
    sounds_inst = None
    r = sr.Recognizer()

    # We have to keep track of our own conversation history -
    # we'll use length-limited queue for human prompts and ai replies.
    # A longer history_length will give the system more memory of previous interactions.
    history_length = 50
    human_prompts = deque(maxlen=history_length)
    ai_replies = deque(maxlen=history_length)

    # Host and port for the Oobabooga API
    HOST = 'localhost:5000'
    URI = f'http://{HOST}/api/v1/chat'

    # This is the preamble that will be used in every interaction
    preamble = "The following is a conversation with an AI assistant.  The assistant is helpful, creative, clever, \
                  and very friendly."
    # And the prompts will be primed with these examples (which will scroll off as we reach history_length)
    human_prompts.append("Hey, how are you?")
    ai_replies.append("I'm great thanks!  What's going on with you?")

    # Constructor that gives us our settings, a speech-to-text and a push-to-talk instance to use
    def __init__(self, settings, stt_inst, ptt_inst, sounds_inst):
        self.settings = settings
        self.stt_inst = stt_inst
        self.ptt_inst = ptt_inst
        self.sounds_inst = sounds_inst

    # Listen for a piece of audio input, use the STT instance to recognize it
    # The reason for doing the listening here is that some chat engines need to keep track of their own
    # conversation history so this gives us a chance to store it.
    def listen_for_user_input(self):
        try:
            with sr.Microphone(sample_rate=16000) as source:
                # listen
                self.r.adjust_for_ambient_noise(source, duration=0.2)
                print(f"You: ", end='', flush=True)
                self.sounds_inst.play_chirp()
                self.ptt_inst.wait_to_talk()
                self.sounds_inst.play_bleep()
                print(f"(listening...) ", end='', flush=True)
                audio = self.r.listen(source)
                print(f"(heard...) ", end='', flush=True)
                self.sounds_inst.play_working_sound()
                # parse and tokenize audio
                text = self.stt_inst.recognize(audio)
                print(text)
                return text
        except Exception as e:
            print("Exception {0}".format(e))
            return None

    # Make a prompt from our conversation history
    def make_prompt(self):
        arr = []
        for h, a in zip(self.human_prompts, self.ai_replies):
            arr.append([h, a])
        history = {'internal': arr, 'visible': arr}
        return history

    def get_api_response(self, history, user_text):
        request = {
            'user_input': user_text,
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

        response = requests.post(self.URI, json=request)

        if response.status_code == 200:
            result = response.json()['results'][0]['history']
            return result['visible'][-1][1]
        else:
            print("Error: {0}".format(response.status_code))
            return None

    # Get a response from the GPT API
    def get_response(self, user_text):
        history = self.make_prompt()
        result = self.get_api_response(history, user_text)
        self.human_prompts.append(user_text)
        self.ai_replies.append(result)
        return result
