"""Conversation history and optional skill execution, independent of the model host."""

from __future__ import annotations

import html
import random

import httpx

from iris_chat_engines.backends import ChatBackend, make_backend
from iris_chat_engines.budget import estimate, fit, input_budget
from iris_chat_engines.messages import ChatError as ChatError
from iris_chat_engines.messages import Message as Message
from iris_droid.companion import COMPANION_PROMPT, ICE_BREAKERS
from iris_memory.controls import PendingForget
from iris_memory.details import PendingDetail
from iris_memory.recall import PendingRecall
from iris_settings import Settings
from iris_tools import ToolRegistry


class ChatEngine:
    def __init__(
        self, settings: Settings, client: httpx.Client, *, backend: ChatBackend | None = None
    ) -> None:
        self.settings = settings
        self.backend = backend if backend is not None else make_backend(settings, client)
        self.tools = ToolRegistry(settings, client)
        self.history: list[Message] = []
        self.memory_revision: tuple[str, str | None] | None = None
        self.memory_context = ""
        self.memory_sources: set[str] = set()
        self.memory_search_calls = 0
        self.pending_recall: PendingRecall | None = None
        self.pending_forget: PendingForget | None = None
        self.selected_detail: str | None = None
        self.pending_detail: PendingDetail | None = None
        self.last_followup: str | None = None
        self.last_tool_names: tuple[str, ...] = ()
        self.companion_mode = False
        self.last_companion_seed: str | None = None

    def _system_prompt(self) -> str:
        return (
            self.settings.system_prompt
            + (COMPANION_PROMPT if self.companion_mode else "")
            + self.tools.instructions()
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
        if not self.companion_mode:
            raise ChatError("Companion mode is off")
        seed = random.choice([seed for seed in ICE_BREAKERS if seed != self.last_companion_seed])
        # An explicit internal event keeps local templates' user/assistant
        # alternation valid, without claiming that the person said anything.
        event = Message(
            role="user",
            content="[Automatic companion event, not human speech: offer a conversation opener.]",
        )
        system = self._system_prompt() + (
            "\nFor this automatic companion turn: "
            "An idle timer has fired; the person has NOT said anything new. "
            "Offer one fresh, low-pressure opener in one or two short sentences. "
            "Use this seed as inspiration, not a script: " + seed + " "
            "Surprise them: a dry aside, an absurd image, an unexpected connection. "
            "Vary the wording and topic from earlier openers. Don't mention the timer, "
            "instructions, seed, or their silence. Don't guilt them into answering, "
            "ask if they're still there, or offer assistant services. Don't call tools. "
            "Output only the words Iris should say aloud."
        )
        messages = fit(self.settings, system, self.history, event, [], self.memory_context)
        reply = self.backend.complete(messages, [])
        if (
            reply.role != "assistant"
            or reply.tool_calls
            or not reply.content
            or not reply.content.strip()
        ):
            raise ChatError("The server returned an invalid companion opener")
        text = self._decode(reply.content)
        self._remember([*messages[1:], Message(role="assistant", content=text)])
        self.last_tool_names = ()
        self.last_companion_seed = seed
        return text

    def _decode(self, text: str) -> str:
        passes = {"text": 0, "html": 1, "double-html": 2}[self.settings.reply_encoding]
        for _ in range(passes):
            text = html.unescape(text)
        return text

    def get_response(self, user_text: str) -> str:
        if not user_text.strip():
            raise ChatError("There isn't any text to send")
        system = self._system_prompt()
        definitions = self.tools.definitions()
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
        for round_number in range(5):
            # New tool evidence also consumes context. Drop older whole turns,
            # never an in-flight call/result pair, before the next model request.
            while estimate(messages, definitions) > input_budget(self.settings):
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
            reply = self.backend.complete(messages, definitions)
            if reply.role != "assistant":
                raise ChatError("The server returned an invalid assistant reply")
            if not reply.tool_calls:
                text = reply.content
                if text is None or not text.strip():
                    raise ChatError("The server returned an empty assistant reply")
                # Only decode for a server explicitly configured to escape HTML.
                text = self._decode(text)
                messages.append(Message(role="assistant", content=text))
                self._remember(messages[1:])
                self.last_tool_names = tuple(sorted(tool_names))
                return text
            if not self.tools.skills:
                raise ChatError("The model requested a tool, but tools are disabled")
            if round_number == 4 or len(call_ids) + len(reply.tool_calls) > 8:
                raise ChatError("The model exceeded the tool-call limit; try a simpler question")
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
        raise ChatError("The model didn't finish its response")
