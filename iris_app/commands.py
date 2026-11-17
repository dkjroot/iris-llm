"""Pure command classification; order is part of Iris's public voice grammar."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from iris_app.decisions import VoiceDecision
from iris_app.runtime_audio import command_audio
from iris_domain.memory import MemoryPolicyError
from iris_domain.personality import PersonalityContext
from iris_domain.replies import TurnReply

if TYPE_CHECKING:
    from iris_app.runtime import Brain

from iris_domain.commands import (
    companion_command,
    is_clear_context,
    is_companion_status,
    is_delete_previous,
    is_stop_listening,
)
from iris_domain.memory import DetailCommand, MemoryAction, detail_command, memory_command
from iris_domain.personality import personality_command
from iris_domain.volume import VolumeCommand, volume_command


@dataclass(frozen=True)
class ParsedCommands:
    detail: DetailCommand | None
    block_followup: bool
    action: MemoryAction | None
    volume: VolumeCommand | None
    clear_context: bool
    delete_previous: bool
    stop_listening: bool
    finished: bool
    companion: bool | None
    companion_status: bool
    personality: str | None

    @property
    def first(self) -> str | None:
        for name, matched in (
            ("delete", self.delete_previous),
            ("detail", self.detail is not None),
            ("block_followup", self.block_followup),
            ("memory", self.action is not None),
            ("personality", self.personality is not None),
            ("volume", self.volume is not None),
            ("clear", self.clear_context),
            ("stop", self.stop_listening),
            ("exit", self.finished),
            ("companion", self.companion is not None or self.companion_status),
        ):
            if matched:
                return name
        return None


def parse_commands(
    heard: str,
    *,
    companion: bool = False,
    wake_conversation: bool = False,
    text_only: bool = False,
    companion_idle: bool = False,
) -> ParsedCommands:
    return ParsedCommands(
        detail=detail_command(heard),
        block_followup=heard.casefold().strip(" .!?,")
        in {"don't bring that up again", "do not bring that up again"},
        action=memory_command(heard),
        volume=volume_command(heard) if not companion_idle else None,
        clear_context=is_clear_context(heard),
        delete_previous=is_delete_previous(heard),
        stop_listening=(companion or (wake_conversation and not text_only))
        and is_stop_listening(heard),
        finished=heard.casefold().strip(" .!?") == "exit",
        companion=companion_command(heard),
        companion_status=is_companion_status(heard),
        personality=personality_command(heard),
    )


@dataclass(frozen=True)
class CommandRequest:
    session: str
    heard: str
    text_only: bool
    companion_idle: bool
    wake_conversation: bool
    volume_supported: bool
    personality: PersonalityContext | None


class CommandRouter:
    """Dispatch controls before model generation. Built-ins retain precedence.

    Extensions register exact phrases and receive an accepted request; no model
    tool output or historical text is ever routed as a command.
    """

    def __init__(self, app: Brain) -> None:
        self.app = app
        self._exact: dict[str, Callable[[CommandRequest], TurnReply]] = {}

    @staticmethod
    def _phrase(heard: str) -> str:
        return " ".join(heard.casefold().split()).strip(" .!?,")

    def register(self, phrase: str, handler: Callable[[CommandRequest], TurnReply]) -> None:
        normalized = self._phrase(phrase)
        if (
            not normalized
            or normalized in self._exact
            or parse_commands(phrase, companion=True).first
        ):
            raise ValueError("Choose a unique, non-empty exact command")
        self._exact[normalized] = handler

    def dispatch(
        self, request: CommandRequest, parsed: ParsedCommands, decision: VoiceDecision | None
    ) -> TurnReply | None:
        session, heard, text_only = request.session, request.heard, request.text_only
        companion_idle, wake_conversation = request.companion_idle, request.wake_conversation
        volume_supported, personality = request.volume_supported, request.personality
        existing = self.app.sessions.get(session)
        companion = existing is not None and existing.companion_mode
        detail, block_followup, action = parsed.detail, parsed.block_followup, parsed.action
        volume, clear_context = parsed.volume, parsed.clear_context
        delete_previous, stop_listening = parsed.delete_previous, parsed.stop_listening
        requested_personality = parsed.personality
        extension = self._exact.get(self._phrase(heard))
        if decision is not None and (parsed.first is not None or extension is not None):
            decision.require_acceptance()
        if delete_previous:
            return self.app._delete_previous(heard, existing, text_only=text_only)
        if detail is not None:
            return self.app._detail_control(session, heard, detail, text_only=text_only)
        if block_followup:
            chat = self.app._session(session)
            target = None
            if not self.app.private_mode and self.app.companion_memory is not None:
                target = self.app.companion_memory.last_offered(
                    self.app.settings.memory_owner, chat.last_followup
                )
            text = (
                "I don't have a recent memory follow-up to switch off."
                if target is None
                else "I won't use that saved conversation for companion follow-ups again."
            )
            result = self.app._reply(
                heard, text, text_only=text_only, companion=chat.companion_mode
            )
            if (
                target is not None
                and self.app.companion_memory is not None
                and not self.app.private_mode
            ):
                if not self.app.companion_memory.block(
                    self.app.settings.memory_owner, target, self.app.policy_epoch
                ):
                    raise MemoryPolicyError("The follow-up changed; please try again")
                chat.last_followup = None
            chat.pending_detail = None
            chat.pending_forget = None
            return result
        if existing is not None and not companion_idle:
            existing.pending_detail = None
            existing.selected_detail = None
        if action is not None:
            return self.app._memory_control(session, heard, action, text_only=text_only)
        if existing is not None and not companion_idle:
            existing.pending_forget = None
        if requested_personality is not None:
            selected_personality = None
            if personality is None:
                text = "There aren't any personalities configured on this client."
            elif not requested_personality:
                names = ", ".join(personality.names)
                text = f"The personalities here are {names}. I'm using {personality.active}."
            else:
                selected_personality = next(
                    (
                        name
                        for name in personality.names
                        if name.casefold() == requested_personality
                    ),
                    None,
                )
                text = (
                    f"I've switched to {selected_personality}."
                    if selected_personality is not None
                    else "That personality isn't available here. "
                    "Say list personalities to hear the names."
                )
            return self.app._reply(
                heard,
                text,
                text_only=text_only,
                companion=companion,
                selected_personality=selected_personality,
            )
        if volume is not None:
            if volume_supported:
                return TurnReply(heard=heard, text="", volume=volume, companion_mode=companion)
            return self.app._reply(
                heard,
                "Please update the client to use speaker volume commands.",
                text_only=text_only,
                companion=companion,
            )
        if clear_context:
            if self.app.memory is not None and not self.app.private_mode:
                self.app._checkpoint(
                    self.app.memory.active_conversation(self.app.settings.memory_owner)
                )
                self.app.memory.start_conversation(self.app.settings.memory_owner)
            if companion:
                # Reset tool caches too, just as clearing an ordinary session does.
                fresh = self.app._new_chat()
                fresh.companion_mode = True
                self.app.sessions[session] = fresh
            else:
                self.app.sessions.pop(session, None)
            return TurnReply(
                heard=heard,
                text=(
                    "Private context cleared. Saved conversations weren't changed."
                    if self.app.private_mode
                    else "Context cleared. Earlier conversations are still saved."
                    if self.app.memory is not None
                    else "Context cleared. Ready for a fresh conversation."
                ),
                audio=command_audio("context-cleared.wav", text_only=text_only),
                companion_mode=companion,
            )
        if stop_listening:
            # A control utterance isn't a chat turn and doesn't clear history.
            if existing is not None:
                existing.companion_mode = False
            if self.app.memory is not None and not self.app.private_mode:
                self.app._checkpoint(
                    self.app.memory.active_conversation(self.app.settings.memory_owner)
                )
            return TurnReply(
                heard=heard,
                text="Waiting for the wake phrase."
                if wake_conversation and not text_only
                else "Companion mode off.",
                audio=command_audio("stopped-listening.wav", text_only=text_only),
            )
        if parsed.companion_status:
            return self.app._reply(
                heard,
                "Companion mode is on." if companion else "Companion mode is off.",
                text_only=text_only,
                companion=companion,
            )
        if extension is not None:
            return extension(request)
        return None
