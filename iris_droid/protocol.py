"""Version-one, turn-based droid messages. No recordings are written to disk."""

from __future__ import annotations

import warnings

from iris_audio import (
    read_wav as read_wav,
)
from iris_audio import (
    write_wav as write_wav,
)
from iris_domain.commands import (
    companion_command as companion_command,
)
from iris_domain.commands import (
    is_clear_context as is_clear_context,
)
from iris_domain.commands import (
    is_delete_previous as is_delete_previous,
)
from iris_domain.commands import (
    is_stop_listening as is_stop_listening,
)
from iris_domain.limits import MAX_BODY as MAX_BODY
from iris_domain.replies import (
    TurnCancelled as TurnCancelled,
)
from iris_domain.replies import (
    TurnCompletion as TurnCompletion,
)
from iris_domain.replies import (
    TurnReply as TurnReply,
)
from iris_domain.replies import (
    VoiceStreamStart as VoiceStreamStart,
)
from iris_domain.replies import (
    VoiceTiming as VoiceTiming,
)

VOICE_TIMING_HEADER = "X-Iris-Voice-Timing"
VOICE_STREAM_TYPE = "application/x-iris-voice+ndjson"
VOICE_CANCEL_PATH = "/v1/voice/cancellable"
VOICE_DECISION_PATH = "/v1/voice/decision"
VOICE_TURN_HEADER = "X-Iris-Turn"
COMPLETION_HEADER = "X-Iris-Completion"


def validate_token(token: str) -> None:
    if (
        not token.strip()
        or not token.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        raise ValueError(
            "Set IRIS_DROID_TOKEN to a non-empty ASCII key, the same on server and client"
        )
    if len(token) < 24:
        warnings.warn(
            "Your droid connection key is short. We recommend a longer random key; "
            "see README.md for how to generate one. Your chosen key will still work.",
            UserWarning,
            stacklevel=2,
        )
