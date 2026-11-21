"""Text rendering for spoken replies."""

import pytest

from iris_app.replies import STREAM_REPLY
from iris_chat_engines.messages import Message
from iris_droid.protocol import write_wav
from iris_tts_engines.text import plain_speech_text


@pytest.mark.parametrize(
    "formatted,spoken",
    [
        ("This is **important**.", "This is important."),
        ("**Hello**, **Dave**.", "Hello, Dave."),
        ("***Very important***", "Very important"),
        ("*A little emphasis*", "A little emphasis"),
        ("__Bold__ and _italic_", "Bold and italic"),
        ("**Outer *inner* words**", "Outer inner words"),
        ("**Café — déjà vu**", "Café — déjà vu"),
        ("2**3 is eight; 4 * 5 is twenty.", "2**3 is eight; 4 * 5 is twenty."),
        ("file_name and __init__ are identifiers", "file_name and init are identifiers"),
        ("`**literal**` and **spoken**", "`**literal**` and spoken"),
        ("\\**literal**", "\\**literal**"),
        ("Unfinished **formatting", "Unfinished **formatting"),
    ],
)
def test_emphasis_is_removed_only_from_spoken_copy(formatted, spoken):
    assert plain_speech_text(formatted) == spoken
    assert plain_speech_text(spoken) == spoken


@pytest.mark.parametrize(
    "original,spoken",
    [
        ("✨ **Café** 🙂, déjà vu! 🌸", "Café, déjà vu!"),
        ("Hello👩🏽‍💻world", "Hello world"),
        ("☕ (🌱 nice) 👍🏿! 🇬🇧 Thanks.", "(nice)! Thanks."),
        ("1️⃣ Tea #️⃣, 2 coffees, and 3 × 4.", "Tea, 2 coffees, and 3 × 4."),
        ("一 🌏。二\n😊 三", "一。二\n三"),
        ("© 2026, IRIS™ and ®. → 2 × 3.", "© 2026, IRIS™ and ®. → 2 × 3."),
        ("©️ → 海賊🏴‍☠️ 👍︎ ❤︎", "→ 海賊"),
        ("cafe\u0301 😊 فارسی\u200cزبان क्\u200dष", "cafe\u0301 فارسی\u200cزبان क्\u200dष"),
        ("🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f 🏳️‍🌈!", ""),
    ],
)
def test_emoji_are_removed_as_complete_graphemes(original, spoken):
    assert plain_speech_text(original) == spoken
    assert plain_speech_text(spoken) == spoken


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("original,spoken", [("😊✨!", ""), ("**Café** 🌸!", "Café!")])
def test_reply_uses_cleaned_text_only_for_speech(brain_factory, streaming, original, spoken):
    harness = brain_factory(replies=[Message(role="assistant", content=original)])
    streamed = []

    def stream(reply, chunks, rate):
        assert reply.text == original
        assert rate == harness.tts.sample_rate
        streamed.extend(chunks)

    token = STREAM_REPLY.set(stream if streaming else None)
    try:
        reply = harness.brain.turn("pi", write_wav(b"\x01\x00" * 1600, 16000))
    finally:
        STREAM_REPLY.reset(token)
    assert reply.text == original
    assert bool(reply.audio) == (bool(spoken) and not streaming)
    assert bool(streamed) == (bool(spoken) and streaming)
    assert harness.tts.calls == ([spoken] if spoken else [])
    assert harness.brain.sessions["pi"].history[-1].content == original


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("[[expression:amused]] Hello!", "Hello!"),
        ("Hello! [expression:curious] How are you?", "Hello! How are you?"),
        ("[[amused]] Nice to see you.", "Nice to see you."),
        ("**[[expression:amused]] world**", "world"),
        ("Use `[[expression:amused]]` in code.", "Use `[[expression:amused]]` in code."),
        ("[[expression:amused]]", ""),
    ],
)
def test_plain_speech_text_strips_stray_expression_cues(raw, spoken):
    assert plain_speech_text(raw) == spoken
