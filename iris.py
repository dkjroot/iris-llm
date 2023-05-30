"""IRIS - Intelligent Residential Interface System

IRIS is a voice interface to your chosen chat system (at the moment either Oobabooga via its API (offline) or OpenAI
GPT via its online API).  Run configure.py to set up the configuration and choose which modules you want to use.

Right now you can just talk to IRIS.  The goal is to give IRIS control of home automation systems, but that's a way
off yet.  For now, IRIS can just talk to you.

"""

# To reduce the amount of things we need to load, as some of the models are very big, and because we don't want to
# even load modules that can go online if we're in offline mode, we'll load the config first and then load the rest of
# the dependencies dynamically based on the config.
import os
import json

if os.path.isfile('local-config.json'):
    config_file = 'local-config.json'
else:
    print(
        "Using the default (offline) configuration - consider running configure.py if you want to choose your options")
    config_file = 'local-config-default.json'
try:
    with open(config_file) as config_file_fh:
        settings = json.load(config_file_fh)
except Exception as e:
    print("Error loading config:", config_file, e)
    exit(1)

# Now we can load the various modules depending on what the user has configured...
import importlib
# Let's load the TTS engine first so that we can say that we're loading
tts_engine = importlib.import_module(settings['text to speech engine'])
tts_inst = tts_engine.TTS(settings)
tts_inst.speak("Loading, please wait...")

# Next load the sounds module so that we can play the working sound while we load the rest
sounds_module = importlib.import_module(settings['feedback sounds'])
sounds_inst = sounds_module.Sounds(settings)
# Start the 'working' sound while we load potentially slow stuff
sounds_inst.play_working_sound()

# Now we can load the other stuff that can be a bit slower to load because we might be loading big model files
# depending on which module the user has chosen to use.

# Load the chosen STT engine
stt_engine = importlib.import_module(settings['speech to text engine'])
stt_inst = stt_engine.STT(settings)

# Load the chosen 'push to talk' module
ptt_module = importlib.import_module(settings['push-to-talk / wake-word'])
ptt_inst = ptt_module.PTT(settings, stt_inst)


# Load the chosen chat engine
chat_engine = importlib.import_module(settings['chat engine'])
chat_inst = chat_engine.ChatEngine(settings, stt_inst, ptt_inst, sounds_inst)


# If text is "exit", exit the script
def exit_if_exit(text):
    if text == 'exit':
        print("Iris: Shutting down")
        tts_inst.speak("shutting down")
        exit()


# Make Iris say a friendly hello
def first_time_prompt():
    print("Iris: Nice to see you again, what's up?")
    tts_inst.speak("Nice to see you again, what's up?")


# Print and say the reply from the model
def output_ai_reply(reply):
    print("Iris:", reply)
    tts_inst.speak(reply)


# This will be used repeatedly in a loop - listen for one user input, send to the model, say the response, append
# to the human_prompts and ai_replies queues
def interact_one_cycle():
    try:
        # Get the user's input - each chat engine is responsible for getting the audio and converting it to text
        user_input = chat_inst.listen_for_user_input()
        while user_input is None or user_input == "":
            user_input = chat_inst.listen_for_user_input()

        # If the user said "exit", exit the script
        exit_if_exit(user_input)

        # Play the working sound
        sounds_inst.play_working_sound()

        # Send the user input to the model and get the response
        result = chat_inst.get_response(user_input)

        # Print and say the response
        output_ai_reply(result)
    except Exception as ex:
        print("Exception {0}".format(ex))


# Main
if __name__ == "__main__":
    # Say hello
    first_time_prompt()

    # And repeatedly listen and respond until the user chooses to exit
    while True:
        interact_one_cycle()
