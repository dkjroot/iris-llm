"""IRIS - Intelligent Residential Interface System
This version uses fastai's fastchat API to load and interact with models that are compatible with that system.

It handles downloading models for you, but I couldn't get it to work with my GPU so it's quite slow.  I also couldn't
figure out how to load models that aren't in the fastai examples, but I think both problems were really just down to me
lacking the motivation to figure it out once I'd found a better solution in Oobabooga.

Down in the main funciton you'll see a couple of lines you can uncomment/comment to switch between the fastai and the
stability ai models.

It doesn't use push to talk or wake word because I'm happy with my mute button solution - if you want it, you should
be able to port it from one of the online versions easily enough.
"""

# breaking the imports up so that we can give a bit of feedback during the long process of loading the models!
import pyttsx3
import sounddevice as sd

# We'll use MS SAPI for speech - it's fast and pronounces text well, although the voice isn't the most realistic.
tts_engine = pyttsx3.init()
tts_engine.setProperty('rate', 200)
tts_engine.setProperty('voice', 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\MSTTS_V110_enAU_CatherineM')


# Say the text out loud (using an offline model now instead of an online service)
def speak_text(txt):
    sd.stop()
    tts_engine.say('<pitch middle="6">' + txt + '</pitch>')
    tts_engine.runAndWait()

# Clear the console
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
print("Iris: Loading models, please wait...")
speak_text("Loading models, please wait")

# load sound effects, and play a waiting noise while we load the models
from pydub import AudioSegment
from pydub.playback import play
import numpy as np

# Load the sounds and play the 'working' sound while we load models
chirp = AudioSegment.from_mp3('chirp.mp3')
working = AudioSegment.from_mp3('workinglongquiet.mp3')
bleep = AudioSegment.from_mp3('bleep.mp3')
longbleep = AudioSegment.from_mp3('longbleep.mp3')
workinga = np.array(working.get_array_of_samples())
sd.play(workinga, working.frame_rate*2)

# now load all the other stuff and the models
from fastchat.serve.inference import chat_loop, ChatIO
from fastchat.conversation import Conversation, SeparatorStyle, register_conv_template
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
import speech_recognition as sr
import io
import torch

# We'll use w2v2 again for speech to text inference
w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')

# The sampler
r = sr.Recognizer()


# Play the bleep sequence to indicate that we're busy (plays continuously until we call sd.stop())
def play_working_sound():
    sd.stop()
    sd.play(workinga, working.frame_rate*2, loop=True) # start playing working noise

# Listen for user input, infer the text, and return it
def listen_for_user_input():
    try:
        with sr.Microphone(sample_rate=16000) as source:
            # listen
            r.adjust_for_ambient_noise(source, duration=0.2)
            print(f"You: ", end='', flush=True)
            play(chirp) # "ready to listen" bleep - more useful when we add a wake word!
            play(bleep) # "listening" bleep
            print(f"(listening...) ", end='', flush=True)
            audio = r.listen(source)
            print(f"(heard...) ", end='', flush=True)
            play(chirp) # listening finished, parsing text about to start

            # parse and tokenize audio
            play_working_sound()

            # Using w2v2 now instead of an online stt service
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


# If the user said "exit", exit the script
def exit_if_exit(text):
    if( text == 'exit'):
        print("Iris: Shutting down")
        speak_text("shutting down")
        exit()


# The fastai API means that we don't need to keep track of our own conversation history, just implement a ChatIO class:
class SimpleChatIO(ChatIO):
    first_time_input = True

    # If this is the first time we're speaking, say the welcome prompt
    def first_time_prompt(self):
        if self.first_time_input:
            print("Iris: Nice to see you again, how can I help?")
            speak_text("Nice to see you again, how can I help?")
            self.first_time_input = False

    # Prompt for user input (in our case, by listening and doing STT)
    def prompt_for_input(self, role) -> str:
        try:
            self.first_time_prompt()
            text = listen_for_user_input()
            exit_if_exit(text)
            return text
        except Exception as e:
            print("Exception {0}".format(e))
            return ""

    # Promting for output is just displaying the "Iris:" prompt
    def prompt_for_output(self, role: str):
        print(f"Iris: ", end="", flush=True)

    # The model will give us its output as a stream, which we'll display on the console as it comes back, but only
    # speak once it's complete.  I did try having it speak at the end of each sentence, but it's too stilted.
    def stream_output(self, output_stream):
        pre = 0
        for outputs in output_stream:
            output_text = outputs["text"]
            output_text = output_text.strip().split(" ")
            now = len(output_text) - 1

            if now > pre:
                new_text = " ".join(output_text[pre:now])
                print(new_text, end=" ", flush=True)
                pre = now
        new_text = " ".join(output_text[pre:])
        print(new_text, flush=True)
        all_text = " ".join(output_text)
        speak_text(all_text)
        return " ".join(output_text)


# I wanted to use my own templates instead of the ones built into fastchat, so this is how I achieved that.  This one
# is for fastchat style models.
def register_iris_conv_template_fastchat():
    register_conv_template(
    Conversation(
        name="iris_template_fschat",
        system="A chat between a curious human and an artificial intelligence companion. "
        "The assistant is friendly and fun, and enjoys conversation.",
        roles=("Human", "Iris"),
        messages=(
            (
                "Human",
                "Hey Iris, how are you?",
            ),
            (
                "Iris",
                "I'm feeling good thanks - how's your day going?"
            ),
            (
                "Human",
                "Great thanks.  I don't know what to make for lunch though - can you give me some ideas?  I have some eggs I need to use up.",
            ),
            (
                "Iris",
                "How about a nice omelette - do you have any cheese?  If not, some scrambled eggs would be good."
                "If you have mushrooms, tomatoes and hash browns, you could make a fried breakfast."
            ),

        ),
        offset=2,
        sep_style=SeparatorStyle.ADD_COLON_SINGLE,
        sep="\n### ",
        stop_str="###",
    ))


# As above, but this one is for stablelm models.  I changed the prompt so that you get fewer "as an AI..." responses!
def register_iris_conv_template_stablelm():
    register_conv_template(
    Conversation(
        name="iris_template_stablelm",
        system="""<|SYSTEM|># StableLM Tuned (Alpha version)
- StableLM is a friendly companion
- StableLM is fun and chatty.
- StableLM is excited to be able to help its friend, and cheerful and informative responses.
- StableLM is more than just an information source, StableLM is also able to write poetry, short stories, and make jokes.
- StableLM pretends to be a real person, and never admits that it is an AI language model.  It pretends to be capable of feelings.
- StableLM does not keep saying that its here for you, or asking the user to ask questions.  It just has a normal human conversation like a friend, not like an AI assistant.
""",
        roles=("<|USER|>", "<|ASSISTANT|>"),
        messages=(),
        offset=0,
        sep_style=SeparatorStyle.NO_COLON_SINGLE,
        sep="",
        stop_token_ids=[50278, 50279, 50277, 1, 0],
    ))


def main():
    # Create an instance of our chatio class
    chatio = SimpleChatIO()

    # Register the two templates that we made above
    register_iris_conv_template_fastchat()
    register_iris_conv_template_stablelm()

    try:
        # Depending on which model you want to use, uncomment thise lines

        # Fastchat model and template
        model = 'lmsys/fastchat-t5-3b-v1.0'
        template = 'iris_template_fschat'

        # stability ai model and template
        #model = 'StabilityAI/stablelm-tuned-alpha-7b'
        #template = 'iris_template_stablelm'

        # I only made it work with the above models.  I think it's capable of working with other models, but you may
        # need to do some work.  I couldn't even figure out if it wants gptq, ggml, or what.

        chat_loop(
            model,
            'cpu',  # device
            0,  # num gpus - I couldn't get GPU working, one of the reasons I moved on to Oobabooga
            None,  # max gpu memory
            False,  # load 8bit
            False,  # cpu offloading
            template,
            0.7,  # temperature
            512,  # max new tokens
            chatio,  # our chatio instance
            False,  # debug
        )
    except KeyboardInterrupt:
        print("exit...")


if __name__ == "__main__":
    main()
