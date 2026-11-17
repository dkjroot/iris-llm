# Voice commands and confirmations

A spoken reply from Iris doesn't tell you, by itself, whether she ran a command.
Several built-in controls use her normal voice for their confirmation. For
example, a successful companion command says “Companion mode on. Let's hang out.”
Those are fixed words from the command handler; the chat model didn't write them.

## What happens when you speak

The client records your speech and sends it to the PC. The PC transcribes it,
then checks the transcript for built-in commands before asking the chat model
anything. Text input joins the same path after transcription.

“Companion mode on” and “set speaker volume to 50 percent” start through this
same path. They do different work once recognised:

- Companion mode changes the current conversation session on the PC. The reply
  tells the client that the mode is on, and the voice client starts its quiet-time
  timer after playback. The normal initial wait is 90–240 seconds.
- A speaker volume command goes back to the client that owns the speaker. The
  client changes its mixer, reads the resulting value and asks the PC to speak
  that result. If the mixer lands on 49 percent, Iris reports 49 percent.

If a transcript doesn't match a command, it becomes an ordinary conversation
turn. The model might discuss a setting or even say it changed it, but its words
aren't evidence that a built-in control ran. The new command bubbles come from
structured execution results, independently of the model's wording.

## Phrases you can use

Say a complete request on its own. These all enable companion mode:

- “Companion mode on.”
- “Please turn companion mode on.”
- “Enable companion mode.”
- “Could you switch on companion mode?”

Use off or disable to turn it off. Say “companion mode status” or “is companion
mode on?” to hear its current state without changing anything. Volume and
personality controls also accept polite wrappers such as “could you please”.
The command list in the Pi and web menus has the available controls.

The parser still requires a complete command. “Don't enable companion mode”, a
quoted example, or a sentence discussing the command won't switch it on. It
doesn't guess from roughly similar words. If speech recognition gives Iris the
wrong words, try the short command by itself and then ask for its status.

## What the screen tells you

“Processing…” appears while Iris checks a request. It doesn't yet mean she has
recognised a command. A successful companion change then shows a green bubble
with the mode, and **Companion on** remains visible while the face is awake.
The bubble goes away after about six seconds. Volume confirmations show the
actual mixer result. An unavailable mixer gives an amber error.

Some commands, including memory controls that may ask you to make another choice,
show an informational command response. That means the control handler answered;
it doesn't claim a requested change is complete. Listen to the reply for the
next step. Ordinary chat doesn't produce a command-success bubble.

“No confirmation received” means the request or its reply failed somewhere along
the way. A change might already have happened, particularly on a device, so check
its status before repeating a relative command such as “volume up”.

These displays are available on the native Pi interface, its local browser
preview, and the phone web interface. Update the server and client together;
older servers don't provide the structured results. The new server only sends
command feedback when a client asks for it, so older clients retain their existing
reply format.

## Which conversation is affected

Companion mode belongs to the current client session. Turning it on from a phone
doesn't turn it on for a separate Pi session, and restarting a client starts a
new session with the mode off. Saved conversation history doesn't preserve that
live mode flag.

The automatic quiet-time timer runs in the voice client. The phone web interface
can change and report its session's companion style, but it doesn't run the Pi's
automatic-opener timer. While the Pi's companion mode is on, detected follow-up
speech is sent without another wake phrase; say “companion mode off” when you're
done. See [the client setup guide](client-setup.md#6-companion-mode) for the timing
settings and listening behaviour.
