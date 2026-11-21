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
from iris_app.sessions import SessionStore
from iris_app.turn import TurnInput
from iris_chat_engines.backends import ChatBackend, make_backend
from iris_chat_engines.compatible import ChatEngine
from iris_domain.audio import Synthesizer
from iris_domain.expression import FaceExpression
from iris_domain.personality import (
    PersonalityContext,
)
from iris_domain.replies import (
    CommandFeedback,
    TurnReply,
)
from iris_domain.volume import VolumeResult
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
from iris_memory.worker import MemoryMaintenanceWorker
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
        self.session_store = SessionStore(
            lambda: self._new_chat(), default_owner=settings.memory_owner
        )
        self.memory = make_memory(settings)
        if self.memory is not None:
            private, epoch = self.memory.policy(settings.memory_owner)
            self.session_store.set_policy(private, epoch)
        self.memory_context: MemoryContext | None = None
        self.memory_worker: MemoryMaintenanceWorker | None = None
        self.recall: RecallResolver | None = None
        self.details = Details(self.memory) if self.memory is not None else None
        self.companion_memory = CompanionMemory(self.memory) if self.memory is not None else None
        if self.memory is not None:
            summary_settings = settings.model_copy(
                update={
                    "model": settings.memory_extraction_model or settings.model,
                    "max_tokens": settings.memory_summary_tokens,
                    "temperature": 0.1,
                }
            )
            index = MemoryIndex(self.memory)
            index.background_indexing = True
            if settings.memory_semantic_enabled:
                index.semantic = SemanticMemory(
                    self.memory,
                    MiniLMEncoder(model_directory(settings)),
                    settings.memory_semantic_min_score,
                )
            self.memory_context = MemoryContext(
                settings, index, self.backend_factory(summary_settings, client)
            )
            self.memory_context.background_maintenance = True
            self.memory_worker = MemoryMaintenanceWorker(self.memory_context)
            self.lifetime.own(self.memory_worker.close)
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

            chat.tools.register(
                Skill(
                    "search_memory",
                    "Search saved conversation evidence for a specific topic or date when the "
                    "provided memory is insufficient. Read-only; never claim a memory change. "
                    "Results can be ambiguous: ask which discussion. Use at most twice per reply.",
                    SearchArguments,
                    run_search,
                )
            )
        return chat

    @property
    def sessions(self) -> OrderedDict[str, ChatEngine]:
        return self.session_store.sessions

    @sessions.setter
    def sessions(self, value: OrderedDict[str, ChatEngine]) -> None:
        self.session_store.restore(value)

    @property
    def private_mode(self) -> bool:
        return self.session_store.policy()[0]

    @private_mode.setter
    def private_mode(self, value: bool) -> None:
        _, epoch = self.session_store.policy()
        self.session_store.set_policy(value, epoch)

    @property
    def policy_epoch(self) -> int:
        return self.session_store.policy()[1]

    @policy_epoch.setter
    def policy_epoch(self, value: int) -> None:
        private, _ = self.session_store.policy()
        self.session_store.set_policy(private, value)

    def _session(self, session: str) -> ChatEngine:
        return self.session_store.session(session)

    def _reset_sessions(self) -> None:
        self.session_store.reset_all()

    def _sync_policy(self) -> None:
        if self.memory is not None:
            private, epoch = self.memory.policy(self.settings.memory_owner)
            if self.session_store.set_policy(private, epoch):
                self._reset_sessions()

    def _set_private(self, enabled: bool) -> None:
        if self.memory is not None:
            self.memory.set_private(self.settings.memory_owner, enabled)
            self._sync_policy()
        elif self.session_store.set_policy(enabled, self.policy_epoch + 1):
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
        audio_sink: Callable[..., None] | None = None,
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
                audio_sink=audio_sink,
            ),
            decision=decision,
        )

    def volume_result(
        self,
        session: str,
        result: VolumeResult,
        *,
        text_only: bool,
        audio_sink: Callable[..., None] | None = None,
    ) -> TurnReply:
        self._sync_policy()
        chat = self.session_store.get(session)
        description = result.description()
        reply = self._reply(
            "",
            description,
            text_only=text_only,
            companion=chat is not None and chat.companion_mode,
            audio_sink=audio_sink,
        )
        reply.command = CommandFeedback(
            title="Speaker volume",
            message=description,
            status="success" if result.state is not None else "error",
        )
        return reply

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
        audio_sink: Callable[..., None] | None = None,
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
            audio_sink=audio_sink,
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
        audio_sink: Callable[..., None] | None = None,
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
            audio_sink=audio_sink,
        )
