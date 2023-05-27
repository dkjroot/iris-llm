# iris-llm
IRIS: Intelligent Residential Integration System - a mind for your home!

This is a personal project that isn't really in a state for sharing yet, but I was talking about it to some folks on Reddit and they wanted to see if they could crib anything useful from my code so I promised to share it.  Don't expect a lot!

## Licensing

Although I've marked the repo as GPL 3.0, and I'm happy for you to do whatever you like with my code, I have used snippets of code from other projects, samples from huggingface etc. and so some of the code might be covered by other licenses.  If you use my code you may be using theirs, so take care to check licenses for yourself.

## Limitations of the prototypes

- Don't hope for updates any time soon - I have a demanding job, a home, family, and lots of other hobbies, and I get distracted by shiny things.  Once it works well enough for my own use, I'm likely to move on to a different project.  What you see here might be all it ever is!  That said, I will push updates if I do anything useful... just don't hold your breath!
- I only have one PC with a GPU and it runs Windows.  Maybe some day I'll get around to adding another hard disk so that I can dual-boot but right now these scripts are only tested on Windows, and they may use some Windows-specific libraries.  Feel free to make a PR if you make versions that work on Linux or Apple or whatever, but don't hold out any hope that I might keep them updated!
- My PC is already set up with CUDA etc. and I remember it being a bit of a pain.  I'm not going to reinstall everything just so that I can write up instructions - you will have to figure out how to install whatever dependencies these scripts have for yourself.
- Python isn't my first (second or third) language, so don't be surprised to see bad python here - I don't recommend this repo as a tutorial on how to write good python!
- My PC is an i5 8600K with 64gb of RAM and an 8gb RTX 2080 Super running Windows 10.  This is the only rig I have tested these scripts on.  I don't think you should need quite that much RAM (unless you want to run a 30b parameter CPU-only model!), but you probably do need 8gb of VRAM unless you want to use CPU-only models which (although more accurate if you get a big one) are too slow to have a flowing conversation with.
- By the nature of this field, all this work will be out of date about 24 hours after I push it.  I wish you good luck keeping up! :)


## What can it do?

Using these scripts, if you can patch together your own version that works on your box, you will be able to have a voice-only conversation with your computer using either private offline models, or using online services like GPT.  It does speech-to-text, large language model inference, and text-to-speech to read out the responses.  I use a long-range bluetooth dongle on my PC and a cheap teleconferencing device that has a bluetooth speaker, mic and mute button, and with it I can converse with my PC from anywhere in my house.  It's surprisingly fast and natural (using a GPU model<sup>1</sup>), and quite a lot of fun!

<sup>1</sup> My current favourite model is TheBloke/Wizard-Vicuna-7B-Uncensored-GPTQ

## Goals

- Tidy it up, maybe supply ease of use libraries for abstracting the details of loading all the different types of model, or maybe just use the Oobabooga API for that!
- Give it control of my home automation devices.
- Develop a wearable 'Star Trek communicator' to replace the teleconferencing puck.
- Embody IRIS in a robot that wanders round my house, keeps me company, and eventually can bring me a beer from the fridge!

## Where to go from here

Check out the examples directory, there's another README in there.

