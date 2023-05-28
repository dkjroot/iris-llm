# IRIS examples

Here are a collection of scripts that represent the stages in my experimentation.  You might find something here that's useful if you're trying to achieve a similar goal.

## Notes

- A few of the scripts use sound samples to give feedback on the state of the script - I found this very useful when I have no screen (when I'm walking around the house chatting to my computer using the teleconferencing puck).  I've included some default un-copyrighted sounds but they were the result of about 10 minutes work and sound terrible so I strongly recommend you get some better ones!  I use samples from https://www.trekcore.com/audio/.  For the 'working' sound, I took one of the long Star Trek "beep sequences", stretched it out and quieted it down using Audacity so that it's a low digital burble just to give the user the cue that the system is working on something and it can't receive new input right now.  Apologies for the rubbish default sounds - if you want to donate some better (copyright free) sounds, feel free to make a PR :)
- If you're new to all this, I recommend running and understanding each of the experiments in turn because they build on each other until we have a complete working system - that way if there's a little trouble with one of the parts you can fix it in isolation rather than just trying to run the full script and getting stuck when something doesn't work.  Also you will then have the knowledge you need to replace my preferred STT / TTS / LLM choice with your own preference in the larger scripts, because you'll know how to use each of them.
- My bluetooth device has a tendency to connect for music only - if you see a repeated exception in the listening phase, that might be the problem.  I have to disconnect and re-pair it to make it act as a microphone again.  "Exception 'NoneType' object has no attribute 'close'" in a tight loop probably means the script can't see your microphone.

# The scripts

## Text to Speech

### experiments/tts/gtts_experiment.py

A very simple script that uses gTTS to retrieve speech from Google's "Google Translate text-to-speech API".  This method of text to speech uses online services and sends your text to the internet, so is not private.  However, it is free to use (as far as I can tell!) and does a really good job of pronunciation, e.g. if you give it "St Peters" it'll say "Saint Peters", whereas if you give it "101 Peter St." it'll say "Peter Street".  It can also pronounce numbers reasonably well, e.g. "The answer is 1034" is pronounced as "The answer is ten thirty-four".  It has a few languages, I tend to like the sound of Australian or Irish synthesized voices.  My main problem with it (apart from the privacy issue) is that it speaks really slowly and there's nothing you can do to speed it up.

### experiments/tts/pyttsx3_experiment.py

A very simple script that shows how to use pyttsx3 to use Windows SAPI for text-to-speech.  Like the gTTX example, it's
very good at pronouncing things like "St Peters" and "Peter St." correctly, and it does an even better job with numbers.
It's also local, offline, and fast.  It's not the most human-sounding voice in the world, but it's the one I fall back
on because on balance it is the best overall choice I've found for offline TTS.

If you only have a couple of voices, you can install more.
In order to get access to all the MS voices from pyttsx3, I had to mess with the registry, see:
https://stackoverflow.com/questions/62756194/pyttsx3-module-is-not-showing-all-installed-voices

### experiments/tts/speecht5_tts_experiment.py

An example of using a local model for text to speech (here, MS SpeechT5, but others are available on huggingface and 
might be better).  Although it's a voice that sounds very realistic to my ears, as you will see, it fails all three of
my natural language pronunciation tests.  That and the added delays loading models etc. are the reasons I've tended
to use gTTS online or Windows SAPI offline.

## Speech to Text

All these experiments use the speech_recognition library to sample your voice, then use a model to translate that into text.  The main purpose of the scripts is so that we can compare different models and see how well they do in terms of speed and accuracy.

### experiments/stt/speecht5_stt_experiment.py

Uses MS speecht5 model to predict text from your speech.

### experiments/stt/wav2vec2_stt_experiment.py

Uses wav2vec2 with the jonatasgrosman/wav2vec2-large-xlsr-53-english model for inference.  In my testing I think it's faster and more accurate than speecht5

### experiments/stt/kensholm_stt_experiment.py

Uses KenshoLM with the jonatasgrosman/wav2vec2-large-xlsr-53-english model for inference, following jonatasgrosman's
"Inference (boosted by a language model)" example.  I may not have it quite right and there may be more that can be
done to improve the responses.  I found it a bit unsatisfying that I needed to write the user's speech out to a file,
but there may be a way to avoid that.  It didn't seem better in this use case than directly using wav2vec in my 
experiments and has some extra steps (downloading extra files, writing out the wav file, prints a lot of junk to the 
console) that wav2vec alone doesn't have.
You'll need lm.binary and unigrams.txt files like those found at
https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english/tree/main/language_model

### experiments/stt/google_online_experiment.py

If you don't mind your voice sample being sent out to the internet, google's recognizer is built into the
speech_recognition library, is easy to use, fast and does a great job.

## IRIS herself

Now we're really getting somewhere.  Let's start building IRIS!

## Online versions

### iris/online/iris_v1_online_no_sounds.py

This version uses google for STT and TTS, and OpenAI for inference, and so it works REALLY well but is not private.
Note that it's using text-davinci-002 (GPT3) - if you have GPT-4 API access you will need to change the script.  I might do that if/when I eventually get GPT-4 access.
I found it useful to have a 'push to talk' key so that it didn't pick up background noise so often, so it waits for
the user to press the space key before listening for input.  Later we will add an (optional) wake-word feature.
Put your OpenAI API key in your environment variables as OPEN_AI_API_KEY.

### iris/online/iris_v2_online_with_sounds.py

Same as Iris v1 online, but now there are chirps and bleeps to help you know what's going on if you're not able to see
the console output.  This becomes really useful later on when we add a 'wake word' and when you walk away from your
computer to talk where you can't see the screen.  It's a small thing but I think it adds a lot to the usability.

### iris/online/iris_v3_online_with_voicewake.py (and iris/wakeup/wakelistener.py)

Same as the previous version (still using online services) but with a "hey siri" style wake word feature.

In this version we replace the 'push to talk' button with a wake word.  Iris is asleep when it starts up and you need to say
"Pay attention Iris" for it to start listening properly.  Once Iris is awake, you can simply say "Iris" in order to
speak.  There's a chirp to let you know that it's understood your wake word.  You can say "Stop paying attention" to
make it stop paying attention.  While it's waiting for a wake word, it does not send your audio to the internet.  Once
it's awake and listening (after your "Iris" wake word), then your audio is sent to online services for processing.
We'll use wav2vec2 for the offline STT step (while listening for the wakeword).

One of the problems with the wakeword system is that it might be interpreting some long sentence it's hear while you're
trying to say the wakeword, and you just have to wait silently until it's ready.  It can be tricky to know when it's
ready, which is why it outputs ':' and '.' during the "waiting for wakeword" phase.  ':' means it's ready to listen.  If
you see a '.', that means it's picked up some audio and you'll have to wait for the next ':' before you can say the
wakeword.  I tried using audio prompts for this, but it was too annoying.  This is one of the reasons I prefer to use
a push-to-talk (really a mute mic) button on my bluetooth speaker in later versions of Iris.

### iris/online/iris_v4_online_no_wait.py

This version still uses online services, but does not wait at all for you to press space or say a wake word.  That
means it's best used with a mic that has a mute button, or a push-to-talk feature.  This is the version I prefer to use
with my teleconferencing puck.

This is the version that I actually use when I want to talk to GPT.  Note that it's using text-davinci-002 (GPT3) - if you have GPT-4 API access you will need to change the script.  I might do that if/when I eventually get GPT-4 access.

## Offline / local versions

Now we get to the part where I've had less success with my own code.  I've tried llamacpp directly but I couldn't get it to stop truncating responses.  I couldn't get gptq-for-llama to work on Windows because it needed dependencies that (it seems) you can't get for Windows, and I couldn't be bothered to install wsl again (yet, I might go down that route later).  Anyway, there are a couple of things that do work well - one is fastai's fastchat API (which I was able to get working with some models but not others, I think only because I wasn't trying hard enough), and Oobabooga, which seems like the path of least resistance!

### iris/offline/iris_v5_offline_fastchat.py

This is a version of IRIS that uses the fastchat API.  I've had it working with StabilityAI/stablelm-tuned-alpha-7b and lmsys/fastchat-t5-3b-v1.0 but I couldn't figure out what flavour of model it likes, and I eventually moved onto the Oobabooga version.  Still, I'll share it in case it's useful!

This version uses fastai's fastchat API to load and interact with models that are compatible with that system.

It handles downloading models for you, but I couldn't get it to work with my GPU so it's quite slow.  I also couldn't
figure out how to load models that aren't in the fastai examples, but I think both problems were really just down to me
lacking the motivation to figure it out once I'd found a better solution in Oobabooga.

Down in the main funciton you'll see a couple of lines you can uncomment/comment to switch between the fastai and the
stability ai models.

It doesn't use push to talk or wake word because I'm happy with my mute button solution - if you want it, you should
be able to port it from one of the online versions easily enough.

### iris/offline/iris_v6_offline_oobabooga.py

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

