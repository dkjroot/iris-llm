# iris-llm

Iris is evolving! I started out wanting a Jarvis, with chat based home automation as my goal. For a while, Iris stood as a handy example of how to do voice-to-voice communication with an LLM all in python using local models, back when that was something that no one had really got working yet. There's still some useful code in here, and especially in the prototypes branch that I'll leave up. You could check the history too, but there are plenty of products now that support voice interaction with an LLM. So I'm going back to my original idea, but I don't just want a voice assistant that can turn my lights on and off, I want a droid! I've waited long enough, and if no one wants to fulfill my star wars kid fantasies without invading my privacy, well I'll just have to build one myself! So that's what Iris is now evolving into, the brain for a droid that I still need to build (and that will be able to turn my lights on and off, and hold a friendly conversation) 

**[Support my work on Buy Me a Coffee](https://buymeacoffee.com/dkjroot)**

### Free to use. Please help keep it that way.

I develop this project to be freely available to everyone, supported, if at all,
by voluntary contributions. I do not want other people to profit from my work
by selling copies, putting it behind a paywall, or building a paid application
or hosted service around it. I ask you to respect that intention: please keep
this work freely available, credit its origins, and direct people to
[my official GitHub repository](https://github.com/dkjroot/iris-llm) for the software.

If you want to help fund my development, please contribute **directly to me**
through [Buy Me a Coffee](https://buymeacoffee.com/dkjroot). Paying a distributor
or hosting provider does not fund my work unless I explicitly say otherwise.
There is no obligation to contribute.

**This is a personal request, not an additional license restriction.** The
[GNU General Public License v3.0](LICENSE) permits commercial use and
redistribution, including forks, subject to its terms. Those legal permissions
remain unchanged by this request. See [licensing details](LICENSING.md) and
[third-party credits](THIRD_PARTY_NOTICES.md).

## What Iris does now

I've always wanted a droid, just like Luke. We're starting with the talking part:
Iris listens, asks the language model for a reply, and answers in a proper voice.
The wheels, camera and lights will have to wait until I've built something to
put them in. She can control configured Matter switches and schedules through the optional
home service. Driving around will have to wait for the body.

There is one way to run Iris now, with a server and a client. The server runs on
the PC that has the model horsepower, and the client looks after the microphone
and speaker. That client can be a laptop while you're trying it out, then a
Raspberry Pi when you're ready to build the droid. You can also run both on the
same computer; it's exactly the same setup, using a local connection.
Your phone's browser can be the client too when you're on your home Wi-Fi.
[Talk to Iris from your phone](docs/phone-access.md), or run her from
[an iPhone shortcut](docs/phone-access.md#talk-to-iris-with-siri).

```text
Laptop or Pi                          PC
microphone -> recorded speech ------> Whisper -> chat model -> local TTS
speaker + printed reply       <------ spoken reply + transcript + reply text
```

The client can wait for Enter, listen whenever it's your turn, or use a small
local recogniser to listen for “hey iris”. For now, she finishes speaking before
listening again. In wake mode, “hey iris” opens a conversation: after each reply
you can speak again without repeating her name. Say “stop listening”, or leave
five minutes of silence, to return to waiting for the wake phrase.

## Where to start

If you're coming to Iris fresh, [start here](docs/start-here.md). There are
complete [Windows](docs/setup-windows.md), [macOS](docs/setup-macos.md) and
[Ubuntu](docs/setup-linux.md) walkthroughs, beginning with one local conversation
and adding the Pi, phone and home automation when you want them. Most of those
extras are optional. You don't need to build the whole droid in one sitting.

If you want to understand how it all works, the [technical guide](docs/technical-guide.md)
starts with the basics and follows the data through the system. The
[guide index](docs/index.md) has the shorter references for individual jobs.

## Things I want Iris to do next

Alongside building her a body, I want Iris to help look after the house. A few
things on the list are:

- Monitor the front door lock, so I can ask her whether it's locked.
- Control automatic roller blinds.
- Monitor temperature and humidity around the house.

I'd also like her to help with things that need regular attention, especially in
the kitchen. If I put something on the hob or in the oven, I want to be able to
say, “Iris, don't let me leave this unattended for more than 40 minutes.” She'd
watch for me coming back to tend to it, and start that interval again when I do.
If she hasn't seen me tend to it within the time I've set, she'd alert me by
voice, push notification, or whichever alerting features I've enabled. The idea
could be useful for older people, but also for people like me who are easily
distracted. Filling a bath is another situation where that extra nudge could help.

Further down the line, I'd like to explore controlling things like the cooker
and filling the bath automatically, taking the home automation side of Iris much
further.

These are goals for future work; she can't do them yet.

## Choose where each half runs

Start with the server on the PC that handles the models, then set up the client
on the laptop or Pi with the microphone and speaker. You can run both on one
computer in separate terminals, using `http://127.0.0.1:8765` as the client address.
The phone browser and Siri shortcut connect to that same server.

The detailed instructions have their own pages now, so you don't have to scroll
past every possible setup to find the bit you need. The [guide index](docs/index.md)
links the whole lot together.

<a id="1-install-the-server"></a>
<a id="linux-bash"></a>
<a id="2-configure-the-server-and-chat-connection"></a>
<a id="using-openrouter"></a>
<a id="speech-and-outbound-connections"></a>
<a id="optional-skills-the-clock-local-weather-and-phone-notifications"></a>
<a id="push-notifications-with-pushover"></a>
<a id="using-another-model-host-or-adding-an-adapter"></a>
<a id="does-my-llm-need-a-key-as-well"></a>
<a id="3-download-the-speech-models"></a>
<a id="optional-voice-cloning-with-chatterbox-turbo"></a>
<a id="optional-cpu-voice-cloning-with-pocket-tts"></a>
<a id="4-set-the-client-connection-key"></a>
<a id="5-start-the-server"></a>
<a id="what-opening-the-lan-listener-means"></a>

## How to set up the server

Install and configure the local chat and speech models, choose a connection key,
and start the Iris server.

[Read the guide](docs/server-setup.md).

<a id="enable-the-phone-microphone"></a>
<a id="running-caddy-in-ubuntu-on-wsl2"></a>
<a id="talk-to-iris-with-siri"></a>

## Talk to Iris from your phone

Use web chat, set up trusted HTTPS for the microphone, and build an iPhone
shortcut that you can run with Siri.

[Read the guide](docs/phone-access.md).

<a id="macos-terminal"></a>
<a id="install-the-python-packages-all-platforms"></a>
<a id="how-to-set-up-the-client"></a>
<a id="1-install-the-client"></a>
<a id="linux-and-raspberry-pi-os-bash"></a>
<a id="2-set-the-connection-key"></a>
<a id="3-test-the-connection-with-text"></a>
<a id="4-set-up-listening-and-audio-devices"></a>
<a id="choose-the-microphone-and-speaker"></a>
<a id="save-the-client-settings"></a>
<a id="give-a-client-its-own-personalities"></a>
<a id="control-the-speaker-volume-by-voice"></a>
<a id="raspberry-pi-alsa-card-3-device-0"></a>
<a id="check-playback-then-try-a-voice-turn"></a>
<a id="feedback-sounds-on-the-client"></a>
<a id="reject-kitchen-noise-with-local-speech-detection"></a>
<a id="spoken-formatting-and-missing-opening-words"></a>
<a id="start-with-a-local-microphone-test"></a>
<a id="client-recording-controls"></a>
<a id="hear-what-iris-recorded-before-sending-it"></a>
<a id="recordings-that-stop-halfway-through-a-sentence"></a>
<a id="fast-or-crackly-microphone-recordings"></a>
<a id="linux--raspberry-pi-os-compare-alsa-with-python-capture"></a>
<a id="5-optional-local-wake-detection"></a>
<a id="cancel-something-iris-picked-up-by-mistake"></a>
<a id="6-companion-mode"></a>
<a id="7-start-the-pi-client-at-boot"></a>
<a id="if-raspberry-pi-os-only-shows-your-service-logs-with-sudo"></a>
<a id="optional-face-display"></a>
<a id="optional-home-automation"></a>

## How to set up the client

Install the laptop or Pi client, choose audio devices, configure wake and
cancellation, and set up startup services, the face display and home automation.

[Read the guide](docs/client-setup.md).

<a id="optional-python-package-updates"></a>

## Optional automatic updates

Set up optional updates and readiness checks, or recover an incomplete update
without guessing which code, packages and archive belong together.

[Read the guide](docs/updates.md).



## If something doesn't work

Work through connection, model, audio and memory problems, with links to safe
diagnostics and recovery instructions.

[Read the guide](docs/troubleshooting.md).

<a id="conversation-memory"></a>
<a id="private-conversations-and-forgetting"></a>
<a id="remembering-and-changing-a-detail"></a>
<a id="which-memories-come-to-mind"></a>
<a id="asking-about-an-earlier-discussion"></a>
<a id="optional-companion-follow-ups"></a>
<a id="optional-semantic-recall"></a>
<a id="context-size-and-compaction"></a>
<a id="summary-checkpoints-and-maintenance"></a>

## Configuration, privacy and the code

Understand saved conversations, private mode, explicit remembering and forgetting,
recall, backups and which information stays on the server.

[Read the guide](docs/memory-and-privacy.md).

## Helping with Iris

If you'd like to change the code, start with [contributing](CONTRIBUTING.md),
then the [architecture guide](docs/architecture.md) and [checks](docs/testing.md).
The [compatibility reference](docs/compatibility.md) explains behaviour that needs
to stay steady as the code grows. The [release checklist](docs/releasing.md) covers
packaging and the real-device checks that automated tests can't do for us.

The [prototypes branch](https://github.com/dkjroot/iris-llm/tree/prototypes/examples)
keeps the earlier experiments if you're interested in how this started.
