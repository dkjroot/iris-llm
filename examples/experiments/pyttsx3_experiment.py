"""
A very simple script that shows how to use pyttsx3 to use Windows SAPI for text-to-speech.  Like the gTTX example, it's
very good at pronouncing things like "St Peters" and "Peter St." correctly, and it does an even better job with numbers.
It's also local, offline, and fast.  It's not the most human-sounding voice in the world, but it's the one I fall back
on because on balance it is the best overall choice right I've found for offline TTS.
In order to get access to all the MS voices, I had to mess with the registry, see:
https://stackoverflow.com/questions/62756194/pyttsx3-module-is-not-showing-all-installed-voices
"""


import pyttsx3


def list_voices():
    # See main for an example of hard-coding one of these IDs as the voice you want to hear.
    for voice in engine.getProperty('voices'):
        print(voice, voice.id)
        engine.setProperty('voice', voice.id)
        engine.say("This is " + voice.name)
        engine.runAndWait()
        engine.stop()


def speak(text):
    engine.say(text)
    engine.runAndWait()
    engine.stop()


if __name__ == "__main__":
    # Speak using the Microsoft Catherine voice.
    engine = pyttsx3.init()
    engine.setProperty('voice', 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\MSTTS_V110_enAU_CatherineM')
    engine.setProperty('rate', 200)
    speak("St Peters")
    speak("101 Peter St.")
    speak("The answer is 1034")

    # Show an example of all the voices installed on the system.  If you only have a couple of voices, you can install more.
    list_voices()


