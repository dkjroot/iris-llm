# iris-llm
IRIS: Intelligent Residential Integration System - a mind for your home!

This is a personal project, but maybe there'll be something that helps someone.  It's very early in its development at the moment, but if you know a bit about python you should be able to get it running, and it'll present a voice interface to your local language models (or even GPT if you choose that option).  

If you want to understand how the voice-to-voice stuff works in detail, check out the prototypes branch.

There are a few options for which chat engine, which text-to-speech model etc. it'll use.  Run 'python configure.py' to configure it.  In default configuration it'll use all offline models.



## LLM client ('chat engine') options:

### Oobabooga API mode

IRIS can use Oobabooga (https://github.com/oobabooga/text-generation-webui) via its API.  This is a convenient way to access local LLM models because it has a nice interface for setting up your model and your character, has wide compatibility with different model types, and works really well.  

You need to have Oobabooga fully set up and working with your favourite model, plus a character named "Iris" that works in Oobabooga in chat mode.

I set my CMD_FLAGS (in Oobabooga's webui.py) to  '--auto-device --api --chat --xformers --model-menu --model_type gptj'  (for gptq models - I also make sure to save working settings in the Oobabooga web ui for the model before I try to use it via IRIS).  Most important is to enable the API with --api.  Basically make sure Oobabooga is working with your model, and running with the API listening on port 5000


### GPT mode

Alternatively, IRIS can use GPT as its chat engine.  For this you'll need an OpenAI API key of your own, and beware that this version uses online services and so is not private.

Put your OpenAI API key in your environment variables as OPEN_AI_API_KEY

By default the GPT mode uses GPT-3 (text-davinci-002)



## TTS (text-to-speech) engine options:

### pyttsx3 (offline)

This voice engine doesn't sound quite as good as some others but it's all local, is fast, and is really good at figuring out how to pronounce the sentence in a naural way (for example, it knows the difference between "St Peters" and "Peter St.", and it can pronounce numbers).  I've only tested it on Windows - if you're on Linux you may need to tweak some of the settings in the module.

### gTTS (online)

Google's voice is a bit slow but it sounds good.  If you don't mind sharing your conversation with the internet!



## STT (speech-to-text) engine options:

### wav2vec2 (offline)

A local model that works reasonably well and protects your privacy by not sending your audio sample to the internet.

### Google (online)

Google's STT is pretty good, but it's not private.



## Feedback sounds options:

### Beeps and bleeps

IRIS will give you some audio cues so that you know what's going on - especially useful if you're not near your screen.

### No sounds

Choose this option to disable the bleeps and bloops.



## Push to Talk / Wake Word options:

### Push space to talk

IRIS won't listen to the mic until you press space each time.  The library I used in this module is Windows specific, sorry!

### Wake word

IRIS has a 'hey siri' type feature.  Say "Iris, pay attention" to make IRIS wake up from idle.  Then you only need to say "Iris" or "Hey Iris" in order for it to start listening.
To put IRIS back into idle mode, say "Iris, stop paying attention".

One of the problems with the wakeword system is that it might be interpreting some long sentence it's heard while you're
trying to say the wakeword, and you just have to wait silently until it's ready.  It can be tricky to know when it's
ready, which is why it outputs ':' and '.' during the "waiting for wakeword" phase.  ':' means it's ready to listen.  If
you see a '.', that means it's picked up some audio and you'll have to wait for the next ':' before you can say the
wakeword.  I tried using audio prompts for this, but it was too annoying.  This is one of the reasons I prefer to use
a push-to-talk (really a mute mic) button on my bluetooth speaker.

Regardless of whether you're using other online options, IRIS always uses offline models to listen for the wake word, so it's not sending all your audio to the internet all the time!

### Do not wait

IRIS won't wait at all, and will always listen when it's your turn to talk.  This works well if your microphone has a mute or push-to-talk button on it.

