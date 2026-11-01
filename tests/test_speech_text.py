"""Emphasis rendering for spoken replies, without changing literal operators."""

import pytest

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
