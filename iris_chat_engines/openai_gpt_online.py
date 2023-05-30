# This version uses GPT API online
from iris_chat_engines.IChatEngine import IChatEngine
import openai
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

    # This is the preamble that will be used in every interaction
    preamble = "The following is a conversation with an AI assistant.  The assistant is helpful, creative, clever, \
                  and very friendly."

    # And the prompts will be primed with these examples (which will scroll off as we reach history_length)
    human_prompts.append("Hello, how are you?")
    ai_replies.append("I am an AI created by OpenAI.  How can I help you today?")

    # These are the start and restart sequences.  We need them so that we can include them in our prompts, and strip
    # these from the responses when we print the output.
    start_sequence = "\nAI: "
    restart_sequence = "\nHuman: "

    # Constructor that gives us our settings, a speech-to-text and a push-to-talk instance to use
    def __init__(self, settings, stt_inst, ptt_inst, sounds_inst):
        self.settings = settings
        self.stt_inst = stt_inst
        self.ptt_inst = ptt_inst
        self.sounds_inst = sounds_inst
        # Get the user's OpenAI API key from the environment
        openai.api_key = os.environ['OPENAI_API_KEY']

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
    def make_prompt(self, user_text):
        prompt = self.preamble
        for h, a in zip(self.human_prompts, self.ai_replies):
            prompt += self.restart_sequence + h + self.start_sequence + a
        prompt += self.restart_sequence + user_text + "\n'''"
        return prompt

    # Get a response from the GPT API
    def get_response(self, user_text):
        prompt = self.make_prompt(user_text)
        self.human_prompts.append(user_text)
        response = openai.Completion.create(
            model=self.settings['model'],
            prompt=prompt,
            temperature=self.settings['temperature'],
            max_tokens=self.settings['max_tokens'],
            top_p=self.settings['top_p'],
            frequency_penalty=self.settings['frequency_penalty'],
            presence_penalty=self.settings['presence_penalty'],
            stop=["'''"]
        )
        text = response.choices[0].text.replace("\n", "").replace("AI:", "")
        self.ai_replies.append(text)
        return text

