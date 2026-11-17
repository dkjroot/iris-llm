"""Composition and compatibility entry point for Iris application turns."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable

import httpx
from pydantic import BaseModel

from iris_app.commands import CommandRouter
from iris_app.coordinator import TurnCoordinator
from iris_app.decisions import VoiceDecision
from iris_app.lifetime import ApplicationLifetime
from iris_app.memory import MemoryOrchestrator
from iris_app.replies import ReplyService
from iris_app.turn import TurnInput
from iris_chat_engines.backends import ChatBackend, make_backend
from iris_chat_engines.compatible import ChatEngine
from iris_domain.audio import Synthesizer
from iris_domain.expression import FaceExpression
from iris_domain.personality import (
    PersonalityContext,
)
from iris_domain.replies import (
    TurnReply,
)
from iris_memory import make_memory
from iris_memory.companion import CompanionMemory
from iris_memory.context import MemoryContext
from iris_memory.controls import MemoryAction
from iris_memory.details import DetailCommand, Details
from iris_memory.embeddings import MiniLMEncoder, model_directory
from iris_memory.index import MemoryIndex
from iris_memory.recall import RecallResolver
from iris_memory.search import SearchArguments, search_memory
from iris_memory.semantic import SemanticMemory
from iris_settings import Settings
from iris_stt_engines.ISTT import ISTT
from iris_tools import Skill, ToolServices

LOGGER = logging.getLogger(__name__)


class Brain:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client,
        stt: ISTT,
        tts: Synthesizer,
        *,
        backend_factory: Callable[[Settings, httpx.Client], ChatBackend] | None = None,
    ) -> None:
        self.lifetime = ApplicationLifetime()
        self.commands = CommandRouter(self)
        self.coordinator = TurnCoordinator(self)
        self.memory_service = MemoryOrchestrator(self)
        self.replies = ReplyService(self)
        self.backend_factory = backend_factory or make_backend
        self.settings = settings
        self.client = client
        self.tool_services = ToolServices(settings, client, persistent=True)
        self.lifetime.own(self.tool_services.close)
        self.stt = stt
        self.tts = tts
        self.sessions: OrderedDict[str, ChatEngine] = OrderedDict()
        self.memory = make_memory(settings)
        self.private_mode = False
        self.policy_epoch = 0
        if self.memory is not None:
            self.private_mode, self.policy_epoch = self.memory.policy(settings.memory_owner)
        self.memory_context: MemoryContext | None = None
        self.recall: RecallResolver | None = None
        self.details = Details(self.memory) if self.memory is not None else None
        self.companion_memory = CompanionMemory(self.memory) if self.memory is not None else None
        if self.memory is not None:
            summary_settings = settings.model_copy(
                update={"max_tokens": settings.memory_summary_tokens, "temperature": 0.1}
            )
            index = MemoryIndex(self.memory)
            if settings.memory_semantic_enabled:
                index.semantic = SemanticMemory(
                    self.memory,
                    MiniLMEncoder(model_directory(settings)),
                    settings.memory_semantic_min_score,
                )
            self.memory_context = MemoryContext(
                settings, index, self.backend_factory(summary_settings, client)
            )
            self.recall = RecallResolver(index, settings.local_timezone)

    def _new_chat(self) -> ChatEngine:
        settings = self.settings
        if self.private_mode:
            settings = settings.model_copy(
                update={
                    "trace": False,
                    "system_prompt": settings.system_prompt
                    + "\nThis is a private conversation. Use only its working context. "
                    "Saved memory is unavailable; do not claim to archive or recall it.",
                }
            )
        elif self.memory is not None:
            settings = settings.model_copy(
                update={
                    "system_prompt": settings.system_prompt
                    + "\nCompleted conversations are archived. "
                    "Labelled detail commands use 'remember LABEL as VALUE', "
                    "'correct LABEL to VALUE', "
                    "or 'forget the detail about LABEL', followed by confirmation. Natural human "
                    "corrections can update source-backed facts without erasing old conversations. "
                    "A model reply or read-only search alone cannot change or delete memory."
                }
            )
        chat = ChatEngine(
            settings,
            self.client,
            services=self.tool_services,
            backend=self.backend_factory(settings, self.client),
        )
        if (
            not self.private_mode
            and self.memory_context is not None
            and settings.memory_search_tool
            and settings.tools_enabled
            and settings.history_turns
            and settings.memory_retrieval_chars
        ):
            epoch = self.policy_epoch
            index = self.memory_context.index

            def run_search(args: BaseModel) -> dict[str, object]:
                chat.memory_search_calls += 1
                if chat.memory_search_calls > 2:
                    return {"error": "Memory search limit reached for this reply."}
                return search_memory(
                    index,
                    settings.memory_owner,
                    settings.local_timezone,
                    epoch,
                    chat.memory_sources.add,
                    args,
                )

            chat.tools.skills["search_memory"] = Skill(
                "search_memory",
                "Search saved conversation evidence for a specific topic or date when the "
                "provided memory is insufficient. Read-only; never claim a memory change. "
                "Results can be ambiguous: ask which discussion. Use at most twice per reply.",
                SearchArguments,
                run_search,
            )
        return chat

    def _session(self, session: str) -> ChatEngine:
        if session not in self.sessions:
            if len(self.sessions) >= 16:
                self.sessions.popitem(last=False)
            self.sessions[session] = self._new_chat()
        self.sessions.move_to_end(session)
        return self.sessions[session]

    def _reset_sessions(self) -> None:
        for session, old in list(self.sessions.items()):
            fresh = self._new_chat()
            fresh.companion_mode = old.companion_mode
            self.sessions[session] = fresh

    def _sync_policy(self) -> None:
        if self.memory is not None:
            private, epoch = self.memory.policy(self.settings.memory_owner)
            if (private, epoch) != (self.private_mode, self.policy_epoch):
                self.private_mode, self.policy_epoch = private, epoch
                self._reset_sessions()

    def _set_private(self, enabled: bool) -> None:
        if self.memory is not None:
            self.memory.set_private(self.settings.memory_owner, enabled)
            self._sync_policy()
        elif self.private_mode != enabled:
            self.private_mode = enabled
            self.policy_epoch += 1
            self._reset_sessions()

    def turn(
        self,
        session: str,
        body: bytes,
        *,
        text_only: bool = False,
        wake_conversation: bool = False,
        companion_idle: bool = False,
        volume_supported: bool = False,
        face: bool = False,
        decision: VoiceDecision | None = None,
        personality: PersonalityContext | None = None,
    ) -> TurnReply:
        return self.coordinator.execute(
            TurnInput(
                session,
                body,
                text_only,
                wake_conversation,
                companion_idle,
                volume_supported,
                face,
                personality,
            ),
            decision=decision,
        )

    def _memory_control(
        self, session: str, heard: str, action: MemoryAction, *, text_only: bool
    ) -> TurnReply:
        return self.memory_service._memory_control(session, heard, action, text_only=text_only)

    def _detail_control(
        self, session: str, heard: str, command: DetailCommand, *, text_only: bool
    ) -> TurnReply:
        return self.memory_service._detail_control(session, heard, command, text_only=text_only)

    def _checkpoint(self, conversation: str | None) -> None:
        return self.memory_service._checkpoint(conversation)

    def _delete_previous(
        self, heard: str, chat: ChatEngine | None, *, text_only: bool
    ) -> TurnReply:
        return self.memory_service._delete_previous(heard, chat, text_only=text_only)

    def _restore(self, chat: ChatEngine, *, provisional: bool = False) -> str | None:
        return self.memory_service._restore(chat, provisional=provisional)

    def _reply(
        self,
        heard: str,
        text: str,
        *,
        text_only: bool,
        companion: bool,
        finished: bool = False,
        expression: FaceExpression | None = None,
        selected_personality: str | None = None,
        transactional: bool = False,
    ) -> TurnReply:
        return self.replies.prepare(
            heard,
            text,
            text_only=text_only,
            companion=companion,
            finished=finished,
            expression=expression,
            selected_personality=selected_personality,
            transactional=transactional,
        )

    def _turn(
        self,
        session: str,
        body: bytes,
        *,
        text_only: bool,
        wake_conversation: bool,
        companion_idle: bool,
        volume_supported: bool,
        face: bool,
        decision: VoiceDecision | None,
        personality: PersonalityContext | None,
    ) -> TurnReply:
        return self.coordinator.run(
            session,
            body,
            text_only=text_only,
            wake_conversation=wake_conversation,
            companion_idle=companion_idle,
            volume_supported=volume_supported,
            face=face,
            decision=decision,
            personality=personality,
        )
