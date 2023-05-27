"""IRIS - Intelligent Residential Interface System
This version uses google for STT and TTS, and OpenAI for inference, and so it works REALLY well but is not private.
Note that it's using text-davinci-002 (GPT3) - if you have GPT-4 API access you will need to change the script.
I might do that if/when I eventually get GPT-4 access.

I found it useful to have a 'push to talk' key so that it didn't pick up background noise so often, so it waits for
the user to press the space key before listening for input.  Later we will add an (optional) wake-word feature.

Put your OpenAI API key in your environment variables as OPEN_AI_API_KEY
BE CAREFUL NOT TO SHARE YOUR OPENAI API KEY!
"""
# Clear the terminal
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("Iris: Loading, please wait...")

from pydub import AudioSegment
from pydub.playback import play
from gtts import gTTS
from io import BytesIO
import speech_recognition as sr
import openai
from collections import deque
import msvcrt as m
import time
import os


# Speak the text out loud
def speak(text):
    tts = gTTS(text, lang='en', tld='com.au')
    mp3_fp = BytesIO()
    tts.write_to_fp(mp3_fp)
    mp3_fp.seek(0)
    fromfp = AudioSegment.from_file(mp3_fp, format="mp3")
    play(fromfp)


# Return only when the user presses space (Windows specific library, sorry!)
def wait_for_space():
    c = 'a'
    while c != b' ':
        c = m.getch()
        time.sleep(0.1)
    while m.kbhit(): # waste any more input in the buffer before returning
        c= m.getch()


# Wait for the user to press space, then grab a sample of their speech.  If they say "exit" exit the script, otherwise
# print the text and return it.
def listen_for_user_input(wait_for_space_enabled):
    try:
        with sr.Microphone(sample_rate=16000) as source:
            # listen
            r.adjust_for_ambient_noise(source, duration=0.2)
            print(f"You: (push space to talk) ", end='', flush=True)
            if wait_for_space_enabled:
                wait_for_space()
            print(f"(listening...) ", end='', flush=True)
            audio = r.listen(source)
            print(f"(heard...) ", end='', flush=True)

            # parse and tokenize audio
            text = r.recognize_google(audio)
            print(text)
            exit_if_exit(text)
            return text
    except Exception as e:
        print("Exception {0}".format(e))
        return None


# If text is "exit", exit the script
def exit_if_exit(text):
    if( text == 'exit'):
        print("Iris: Shutting down")
        speak("shutting down")
        exit()


# Make Iris say a friendly hello
def first_time_prompt():
    print("Iris: Nice to see you again, how can I help?")
    speak("Nice to see you again, how can I help?")


# Build the prompt for sending to the model, by zipping together all the human prompts and ai responses so far
def make_prompt():
    prompt = preamble
    for h, a in zip(human_prompts, ai_replies):
        prompt += restart_sequence + h + start_sequence + a
    return prompt


# Call GPT to get a response
def get_response(prompt):
    prompt += "\n'''"
    response = openai.Completion.create(
        model="text-davinci-002",
        prompt=prompt,
        temperature=0.9,
        max_tokens=512,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0.6,
        stop=["'''"]
        )
    return response.choices[0].text.replace("\n", "").replace("AI:", "")


# Print and say the reply from the model
def output_ai_reply(reply):
    print("Iris: ", reply)
    speak(reply)


# This will be used repeatedly in a loop - listen for one user input, send to the model, say the response, append
# to the human_prompts and ai_replies queues
def interact_one_cycle():
    try:
        user_input = listen_for_user_input(True)
        while user_input is None:
            user_input = listen_for_user_input(False)
        prompt = make_prompt() + restart_sequence + user_input
        result = get_response(prompt)
        output_ai_reply(result)
        human_prompts.append(user_input)
        ai_replies.append(result)
    except Exception as e:
        print("Exception {0}".format(e))


# Main
if __name__ == "__main__":
    # Load the speech recognizer
    r = sr.Recognizer()

    # Grab the OpenAI API key from the user's environment
    openai.api_key = os.environ['OPENAI_API_KEY']

    # We have to keep track of our own conversation history -
    # we'll use length-limited queue for human prompts and ai replies.
    # A longer history_length will give the system more memory of previous interactions.
    history_length = 10
    human_prompts = deque(maxlen=history_length)
    ai_replies = deque(maxlen=history_length)

    # This is the preamble that will be used in every interaction
    preamble = "The following is a conversation with an AI assistant.  The assistant is helpful, creative, clever, \
               and very friendly."

    # And the prompts will be primed with these examples (which will scroll off as we reach history_length)
    human_prompts.append("Hello, how are you?")
    ai_replies.append("I am an AI created by OpenAI.  How can I help you today?")

    # These are the start and restart sequences.  We need them so that we can include them in our prompts, and strip these
    # from the responses when we print the output.
    start_sequence = "\nAI: "
    restart_sequence = "\nHuman: "

    # Say hello
    first_time_prompt()

    # And repeatedly listen and respond until the user chooses to exit
    while True:
        interact_one_cycle()
