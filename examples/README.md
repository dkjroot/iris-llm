# IRIS examples

Here are a collection of scripts that represent the stages in my experimentation.  You might find something here that's useful if you're trying to achieve a similar goal.

## Notes

- A few of the scripts use sound samples to give feedback on the state of the script - I found this very useful when I have no screen (when I'm walking around the house chatting to my computer using the teleconferencing puck).  I used samples from https://www.trekcore.com/audio/.  For the 'working' sound, I took one of the long "beep sequences", stretched it out and quieted it down using Audacity so that it's a low digital burble just to give the user the cue that the system is working on something and it can't receive new input right now.
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

### iris/iris_v1_online_no_sounds.py

This version uses google for STT and TTS, and OpenAI for inference, and so it works REALLY well but is not private.
Note that it's using text-davinci-002 (GPT3) - if you have GPT-4 API access you will need to change the script.  I might do that if/when I eventually get GPT-4 access.
I found it useful to have a 'push to talk' key so that it didn't pick up background noise so often, so it waits for
the user to press the space key before listening for input.  Later we will add an (optional) wake-word feature.
Put your OpenAI API key in your environment variables as OPEN_AI_API_KEY.

### iris/iris_v2_online_with_sounds.py

Same as Iris v1 online, but now there are chirps and bleeps to help you know what's going on if you're not able to see
the console output.  This becomes really useful later on when we add a 'wake word' and when you walk away from your
computer to talk where you can't see the screen.  It's a small thing but I think it adds a lot to the usability.

### iris/iris_v3_online_with_voicewake.py (and iris/wakeup/wakelistener.py)

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

### iris/iris_v4_online_no_wait.py

This version still uses online services, but does not wait at all for you to press space or say a wake word.  That
means it's best used with a mic that has a mute button, or a push-to-talk feature.  This is the version I prefer to use
with my teleconferencing puck.

This is the version that I actually use when I want to talk to GPT.  Note that it's using text-davinci-002 (GPT3) - if you have GPT-4 API access you will need to change the script.  I might do that if/when I eventually get GPT-4 access.

