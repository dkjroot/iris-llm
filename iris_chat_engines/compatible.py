"""Conversation history and optional skill execution, independent of the model host."""

from __future__ import annotations

import html
import random
import re
from collections.abc import Callable

import httpx

from iris_chat_engines.backends import ChatBackend, capabilities, make_backend
from iris_chat_engines.budget import estimate, fit, input_budget
from iris_chat_engines.messages import ChatError as ChatError
from iris_chat_engines.messages import Message as Message
from iris_domain.companion import COMPANION_PROMPT, ICE_BREAKERS
from iris_domain.expression import FACE_PROMPT, ModelReply, read_model_reply
from iris_domain.session import SessionState
from iris_domain.turn import effect_scope
from iris_settings import Settings
from iris_tools import TOOL_NAMES, ToolRegistry, ToolServices
from iris_turn import check_deadline

_CORRECT_TOOL_MARKER = (
    "\n\nThe previous reply used tool names as plain text. If a lookup is needed, call the "
    "appropriate function using the tools provided, then answer with its results."
)
_CORRECT_WITHOUT_TOOLS = (
    "\n\nThe previous reply used tool names as plain text. Answer in ordinary words. "
    "If a needed tool is unavailable, say so plainly."
)
_CORRECT_AFTER_TOOLS = (
    "\n\nThe previous reply used tool names as plain text. Answer in ordinary words using "
    "the tool results already provided. Say plainly if they do not answer the question."
)
_CODE_FENCE = re.compile(r"`{3,}|~{3,}")


def _tool_markers(text: str, tool_names: frozenset[str]) -> list[str]:
    names: list[str] = []
    fence = ""
    for line in text.splitlines():
        marker = line.strip()
        if fence:
            if marker.startswith(fence) and not marker.rstrip(fence[0]):
                fence = ""
            continue
        if line.startswith(("    ", "\t")):
            continue
        opening = _CODE_FENCE.match(marker)
        if opening is not None:
            fence = opening[0]
        elif marker.startswith("[[") and marker.endswith("]]"):
            name = marker[2:-2].strip()
            if name in tool_names and name not in names:
                names.append(name)
    return names


def _model_messages(
    messages: list[Message],
    turn_start: int,
    tool_names: frozenset[str],
    *,
    correction: str,
    retrying: bool,
) -> list[Message]:
    outgoing = messages.copy()
    needs_correction = retrying
    # Old replies can teach the same mistake back to the model. Replace only
    # their prompt copies, keeping their tool names as context for "try again".
    for index in range(1, turn_start):
        message = messages[index]
        if message.role == "assistant" and not message.tool_calls and message.content:
            names = _tool_markers(message.content, tool_names)
            if names:
                summary = f"I mentioned {', '.join(names)} but did not call those tools."
                outgoing[index] = Message(role="assistant", content=summary)
                if index == turn_start - 1 and turn_start == len(messages) - 1:
                    needs_correction = True
    if needs_correction:
        user = messages[turn_start]
        outgoing[turn_start] = Message(role="user", content=(user.content or "") + correction)
    return outgoing


class ChatEngine(SessionState):
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client,
        *,
        backend: ChatBackend | None = None,
        services: ToolServices | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.backend = backend if backend is not None else make_backend(settings, client)
        self.tools = ToolRegistry(settings, client, services=services)

    def tool_definitions(self) -> list[dict[str, object]]:
        return self.tools.definitions() if capabilities(self.backend).tools else []

    def _system_prompt(self, *, face: bool = False, personality_prompt: str = "") -> str:
        return (
            self.settings.system_prompt
            + (
                "\n" + self.settings.conversation_style_prompt
                if self.settings.conversation_style_prompt
                else ""
            )
            + (COMPANION_PROMPT if self.companion_mode else "")
            + (
                "\nRole-play Iris with this client's chosen personality. Let it shape your tone "
                "and manner within the rest of these instructions.\n" + personality_prompt + "\n"
                if personality_prompt
                else ""
            )
            + (self.tools.instructions() if capabilities(self.backend).tools else "")
            + (FACE_PROMPT if face else "")
        )

    def _remember(self, messages: list[Message]) -> None:
        # Trim at whole turn boundaries, including automatic companion turns.
        starts = [i for i, message in enumerate(messages) if message.role == "user"]
        limit = self.settings.history_turns
        self.history = messages[starts[max(0, len(starts) - limit)] :] if limit and starts else []

    def remember_reply(self, heard: str, text: str) -> None:
        """Store a locally generated recall clarification as an ordinary text pair."""
        self._remember(
            [
                *self.history,
                Message(role="user", content=heard),
                Message(role="assistant", content=text),
            ]
        )
        self.last_tool_names = ()

    def get_companion_opener(self) -> str:
        return self.generate_companion_opener().text

    def generate_companion_opener(
        self, *, face: bool = False, personality_prompt: str = ""
    ) -> ModelReply:
        if not self.companion_mode:
            raise ChatError("Companion mode is off")
        seed = random.choice([seed for seed in ICE_BREAKERS if seed != self.last_companion_seed])
        # An explicit internal event keeps local templates' user/assistant
        # alternation valid, without claiming that the person said anything.
        event = Message(
            role="user",
            content="[Automatic companion event, not human speech: offer a conversation opener.]",
        )
        system = self._system_prompt(personality_prompt=personality_prompt) + (
            "\nFor this automatic companion turn: "
            "An idle timer has fired; the person has NOT said anything new. "
            "Offer one fresh, low-pressure opener in one or two short sentences. "
            "Use this seed as inspiration, not a script: "
            + seed
            + " "
            + (
                "Adapt the seed to the chosen personality's tone and interests. "
                if personality_prompt
                else "Surprise them: a dry aside, an absurd image, an unexpected connection. "
            )
            + "Vary the wording and topic from earlier openers. Don't mention the timer, "
            "instructions, seed, or their silence. Don't guilt them into answering, "
            "ask if they're still there, or offer assistant services. Don't call tools. "
            + (FACE_PROMPT if face else "Output only the words Iris should say aloud.")
        )
        messages = fit(self.settings, system, self.history, event, [], self.memory_context)
        check_deadline()
        reply = self.backend.complete(messages, [])
        check_deadline()
        if (
            reply.role != "assistant"
            or reply.tool_calls
            or not reply.content
            or not reply.content.strip()
        ):
            raise ChatError("The server returned an invalid companion opener")
        result = self._spoken_reply(self._decode(reply.content), face=face)
        self._remember([*messages[1:], Message(role="assistant", content=result.text)])
        self.last_tool_names = ()
        self.last_companion_seed = seed
        return result

    def _decode(self, text: str) -> str:
        passes = {"text": 0, "html": 1, "double-html": 2}[self.settings.reply_encoding]
        for _ in range(passes):
            text = html.unescape(text)
        return text

    def get_response(self, user_text: str) -> str:
        return self.generate_reply(user_text).text

    def _spoken_reply(self, text: str, *, face: bool) -> ModelReply:
        reply = read_model_reply(text, face=face)
        if not reply.text.strip():
            raise ChatError("The server returned an empty assistant reply")
        return reply

    @effect_scope()
    def generate_reply(
        self,
        user_text: str,
        *,
        face: bool = False,
        personality_prompt: str = "",
        before_tools: Callable[[], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> ModelReply:
        if not user_text.strip():
            raise ChatError("There isn't any text to send")
        system = self._system_prompt(face=face, personality_prompt=personality_prompt)
        definitions = self.tool_definitions()
        known_tool_names = TOOL_NAMES.union(self.tools.skills)
        correction = _CORRECT_TOOL_MARKER if definitions else _CORRECT_WITHOUT_TOOLS
        messages = fit(
            self.settings,
            system,
            self.history,
            Message(role="user", content=user_text),
            definitions,
            self.memory_context,
        )
        turn_start = len(messages) - 1
        call_ids: set[str] = set()
        tool_names: set[str] = set()
        tool_rounds = 0
        corrected_marker = False
        retrying = False
        while True:
            # New tool evidence also consumes context. Drop older whole turns,
            # never an in-flight call/result pair, before the next model request.
            while True:
                outgoing = _model_messages(
                    messages,
                    turn_start,
                    known_tool_names,
                    correction=_CORRECT_AFTER_TOOLS if tool_rounds else correction,
                    retrying=retrying,
                )
                if estimate(outgoing, definitions) <= input_budget(self.settings):
                    break
                if turn_start > 1:
                    next_turn = next(
                        (i for i in range(2, turn_start) if messages[i].role == "user"), turn_start
                    )
                    messages = [messages[0], *messages[next_turn:]]
                    turn_start -= next_turn - 1
                elif messages[0].content != system:
                    messages[0] = Message(role="system", content=system)
                else:
                    raise ChatError(
                        "The current tool exchange exceeds model_context_tokens; "
                        "try a smaller request."
                    )
            if check_cancelled is not None:
                check_cancelled()
            check_deadline()
            reply = self.backend.complete(outgoing, definitions)
            retrying = False
            check_deadline()
            if check_cancelled is not None:
                check_cancelled()
            if reply.role != "assistant":
                raise ChatError("The server returned an invalid assistant reply")
            if not reply.tool_calls:
                text = reply.content
                if text is None or not text.strip():
                    raise ChatError("The server returned an empty assistant reply")
                decoded = self._decode(text)
                if _tool_markers(decoded, known_tool_names):
                    if corrected_marker:
                        raise ChatError("The model returned a tool marker instead of an answer.")
                    corrected_marker = True
                    retrying = True
                    continue
                result = self._spoken_reply(decoded, face=face)
                messages.append(Message(role="assistant", content=result.text))
                self._remember(messages[1:])
                self.last_tool_names = tuple(sorted(tool_names))
                return result
            if not definitions:
                raise ChatError("The model requested a tool, but tools are disabled")
            if tool_rounds == 4 or len(call_ids) + len(reply.tool_calls) > 8:
                raise ChatError("The model exceeded the tool-call limit; try a simpler question")
            tool_rounds += 1
            if before_tools is not None:
                before_tools()
            # Preserve the complete exchange, including reasoning needed by some
            # model templates, and trim history only at whole user-turn boundaries.
            messages.append(reply)
            for call in reply.tool_calls:
                if call.id in call_ids:
                    raise ChatError("The model returned duplicate tool-call IDs")
                call_ids.add(call.id)
                tool_names.add(call.function.name)
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=self.tools.execute(call.function.name, call.function.arguments),
                    )
                )
