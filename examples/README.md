# IRIS examples

Here are a collection of scripts that represent the stages in my experimentation.  You might find something here that's useful if you're trying to achieve a similar goal.

## Notes

- A few of the scripts use sound samples to give feedback on the state of the script - I found this very useful when I have no screen (when I'm walking around the house chatting to my computer using the teleconferencing puck).  I used samples from https://www.trekcore.com/audio/.  For the 'working' sound, I took one of the long "beep sequences", stretched it out and quieted it down using Audacity so that it's a low digital burble just to give the user the cue that the system is working on something and it can't receive new input right now.

## The scripts

### experiments/gtts_experiment.py

A very simple script that uses gTTS to retrieve speech from Google's "Google Translate text-to-speech API".  This method of text to speech uses online services and sends your text to the internet, so is not private.  However, it is free to use (as far as I can tell!) and does a really good job of pronunciation, e.g. if you give it "St Peters" it'll say "Saint Peters", whereas if you give it "101 Peter St." it'll say "Peter Street".  It can also pronounce numbers reasonably well, e.g. "The answer is 1034" is pronounced as "The answer is ten thirty-four".  It has a few languages, I tend to like the sound of Australian or Irish synthesized voices.  My main problem with it (apart from the privacy issue) is that it speaks really slowly and there's nothing you can do to speed it up.

### experiments/pyttsx3_experiment.py

A very simple script that shows how to use pyttsx3 to use Windows SAPI for text-to-speech.  Like the gTTX example, it's
very good at pronouncing things like "St Peters" and "Peter St." correctly, and it does an even better job with numbers.
It's also local, offline, and fast.  It's not the most human-sounding voice in the world, but it's the one I fall back
on because on balance it is the best overall choice I've found for offline TTS.

If you only have a couple of voices, you can install more.
In order to get access to all the MS voices from pyttsx3, I had to mess with the registry, see:
https://stackoverflow.com/questions/62756194/pyttsx3-module-is-not-showing-all-installed-voices

### experiments/speecht5_experiment.py

An example of using a local model for text to speech (here, MS SpeechT5, but others are available on huggingface and 
might be better).  Although it's a voice that sounds very realistic to my ears, as you will see, it fails all three of
my natural language pronunciation tests.  That and the added delays loading models etc. are the reasons I've tended
to use gTTS online or Windows SAPI offline.



