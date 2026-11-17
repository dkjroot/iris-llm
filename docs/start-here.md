# Start small, then add the bits you want

You don't need to build the whole droid before you can talk to Iris. Most of the
things in this repository are optional. Get one conversation working on your
computer first. Add another part when you actually want it, and check that part
before carrying on.

The host is the computer doing the heavy work. Choose its guide:

- [Windows 11](setup-windows.md).
- [macOS on Apple Silicon](setup-macos.md).
- [Ubuntu 24.04 LTS](setup-linux.md).

Each route gets the host running, then takes you through the same
[Pi, phone and home chapters](setup-devices.md), and back to platform-specific
startup and backup instructions. Those chapters are shared because the Pi and
iPhone steps do not change when you change the host. The return links tell you
exactly where to continue.

## What is required, and what can wait?

| Milestone | What you need | Can you stop here? |
| --- | --- | --- |
| A typed conversation on one computer | Local chat model/runtime, Iris and its configured speech dependencies/models, a connection key | Yes. The current server loads its speech engines at startup even for a text client. |
| A spoken conversation | A microphone and speaker, on that computer or the Pi | Yes. No screen, smart plug or phone is needed. |
| Your preferred voice | Pocket TTS and a short WAV voice reference, or Kokoro's supplied voices | Choose one; you don't need every speech engine. |
| A separate Pi client | Pi, power supply, storage, network and audio hardware | Optional. It moves capture/playback off the host. |
| Hands-free wake and cancellation | A local Vosk model; optional Silero VAD | Optional. Enter-to-talk works without them. |
| Face and touch menu | Compatible display and, for touch, its input connection | Optional. The client works without a display. |
| Phone browser speech | HTTPS proxy and trusted certificate | Optional. A trusted-LAN text test can use HTTP. |
| Siri shortcut | Working phone HTTPS route and Shortcuts | Optional. Siri reads a text reply using the phone's voice. |
| Memory, details and recall | Enable the local archive; embeddings are another opt-in | Optional. Saved originals are retained until explicitly forgotten. |
| Companion mode and personalities | Client settings/commands; saved follow-ups need memory | Optional. Neither is needed for direct questions. |
| Matter devices and schedules | Supported Wi-Fi Matter device, controller pairing and home service | Optional. No Home Assistant or cloud model is required for this route. |
| Weather and notifications | Internet; Pushover additionally needs its account/app setup | Optional online features, separate from local conversation. |
| Automatic startup and updates | OS service/task setup | Optional initially; finish it when the manual setup works. |

## What these guides have actually established

The reference is the existing Windows/KoboldCPP/Pocket setup, described in
[the reference setup](reference-setup.md). macOS and Ubuntu are equivalent local
routes, not claims that the same GPU backend exists on every platform.
Automated Python, browser and package tests exercise much of Iris, but fresh
end-to-end installation runs on all three host platforms, a clean Pi and an
actual iPhone still need recorded results. The guides say so where it matters.

If you only want to understand the design, start with
[Understanding Iris](technical-guide.md). It explains the terminology as it goes.
The older focused setup pages remain useful references when you want alternatives
or detailed tuning; you don't need to read all of them before starting.
