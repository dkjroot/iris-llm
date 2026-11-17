"""Ordered input, command, model, reply and commit stages for one turn."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

import speech_recognition as sr

from iris_app.turn import CURRENT_EFFECTS, TurnEffects, TurnInput
from iris_audio import read_wav
from iris_domain.expression import FaceExpression
from iris_domain.personality import (
    PersonalityContext,
)
from iris_domain.replies import (
    TurnReply,
)
from iris_domain.turn import TurnExecution
from iris_memory import AUTOMATIC_EVENT, Exchange
from iris_turn import (
    TurnExpired,
    check_deadline,
    diagnostic_turn,
    remaining_timeout,
    stage,
    turn_budget,
)

if TYPE_CHECKING:
    from iris_app.runtime import Brain
from iris_app.commands import CommandRequest, parse_commands
from iris_app.decisions import VoiceDecision

LOGGER = logging.getLogger(__name__)


class TurnCoordinator:
    def __init__(self, app: Brain) -> None:
        self.app = app
        self._serial = Lock()

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with stage("application_queue"):
            if not self._serial.acquire(timeout=remaining_timeout(self.app.settings.queue_timeout)):
                raise TurnExpired("The application turn queue timed out")
        try:
            check_deadline()
            yield
        finally:
            self._serial.release()

    def execute(
        self,
        request: TurnInput,
        *,
        decision: VoiceDecision | None = None,
        effects: TurnEffects | None = None,
        execution: TurnExecution | None = None,
    ) -> TurnReply:
        """Run with one deadline and restore unaccepted provisional session state.

        Durable writes and external effects have their own acceptance gates and
        never participate in this in-memory rollback. Policy changes supersede it.
        """
        with (
            diagnostic_turn(),
            stage("turn"),
            self.app.lifetime.lease(),
            turn_budget(self.app.settings.turn_timeout),
            self._exclusive(),
        ):
            self.app._sync_policy()
            epoch = self.app.policy_epoch
            sessions = self.app.sessions.copy()
            chat = sessions.get(request.session)
            previous = chat.checkpoint() if chat is not None and decision is not None else None
            execution = execution if execution is not None else TurnExecution()
            if effects is not None:
                execution.effects = effects
            execution.phase = "prepared"
            token = CURRENT_EFFECTS.set(execution.effects)

            def accepted() -> None:
                execution.phase = "accepted"

            previous_callback = decision.on_accept if decision is not None else None
            if decision is not None:
                decision.on_accept = accepted
            else:
                execution.phase = "accepted"
            try:
                result = self.run(
                    request.session,
                    request.body,
                    text_only=request.text_only,
                    wake_conversation=request.wake_conversation,
                    companion_idle=request.companion_idle,
                    volume_supported=request.volume_supported,
                    face=request.face,
                    decision=decision,
                    personality=request.personality,
                )
                execution.phase = "completed"
                return result
            except BaseException as error:
                execution.phase = (
                    "discarded" if decision is not None and not decision.accepted else "failed"
                )
                # Callers can inspect attempted effects even when no reply exists.
                error.iris_execution = execution  # type: ignore[attr-defined]
                if (
                    decision is not None
                    and not decision.accepted
                    and self.app.policy_epoch == epoch
                ):
                    if chat is not None and previous is not None:
                        chat.restore(previous)
                    self.app.sessions = sessions
                raise
            finally:
                if decision is not None:
                    decision.on_accept = previous_callback
                current = CURRENT_EFFECTS.get()
                if current is not None:
                    current.attempted.clear()
                    current.blocked.clear()
                CURRENT_EFFECTS.reset(token)

    def run(
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
        self.app._sync_policy()
        if companion_idle:
            chat = self.app.sessions.get(session)
            if chat is None or not chat.companion_mode:
                return TurnReply(heard="", text="Companion mode is off.")
            heard = ""
        elif text_only:
            heard = body.decode("utf-8").strip()
            if len(heard) > 8000:
                raise ValueError("Text turn exceeds 8000 characters")
        else:
            pcm, rate = read_wav(body, input_audio=True)
            if len(pcm) > rate * 2 * self.app.settings.phrase_time_limit:
                raise ValueError("Recording exceeds the configured phrase_time_limit")
            if decision is not None:
                decision.check()
            with stage("recognition"):
                heard = self.app.stt.recognize(sr.AudioData(pcm, rate, 2)).strip()
        check_deadline()
        if decision is not None:
            decision.check()
        if not heard and not companion_idle:
            raise sr.UnknownValueError()
        existing = self.app.sessions.get(session)
        companion = existing is not None and existing.companion_mode
        parsed = parse_commands(
            heard,
            companion=companion,
            wake_conversation=wake_conversation,
            text_only=text_only,
            companion_idle=companion_idle,
        )
        finished, command = parsed.finished, parsed.companion
        control = self.app.commands.dispatch(
            CommandRequest(
                session,
                heard,
                text_only,
                companion_idle,
                wake_conversation,
                volume_supported,
                personality,
            ),
            parsed,
            decision,
        )
        if control is not None:
            return control
        chat = self.app._session(session)
        # All clients for an owner deliberately share one active conversation.
        # Refresh each turn so another client's clear/append cannot leave stale context.
        conversation = self.app._restore(chat, provisional=decision is not None)
        new_conversation = (
            decision is not None
            and conversation is None
            and self.app.memory is not None
            and not self.app.private_mode
            and not finished
        )
        if new_conversation:
            # Memory lookups can treat a provisional conversation as empty without
            # leaving behind an archive entry if the first voice turn is discarded.
            conversation = str(uuid4())
        previous = chat.checkpoint()
        chat.memory_sources = set()
        chat.memory_search_calls = 0
        detail_labels: tuple[str, ...] = ()
        followup_conversation: str | None = None
        hierarchy_plan = None
        expression: FaceExpression | None = None
        personality_prompt = personality.prompt if personality is not None else ""
        try:
            if command is not None:
                if not command:
                    self.app._checkpoint(conversation)
                chat.companion_mode = command
                text = "Companion mode on. Let's hang out." if command else "Companion mode off."
            elif companion_idle:
                chat.memory_context = ""
                if (
                    not self.app.private_mode
                    and self.app.settings.memory_companion_followups
                    and self.app.settings.history_turns
                    and self.app.companion_memory is not None
                ):
                    try:
                        followup = self.app.companion_memory.choose(self.app.settings.memory_owner)
                        if followup is not None:
                            followup_conversation, chat.memory_context = followup
                            chat.memory_sources.add(followup_conversation)
                    except (sqlite3.Error, ValueError, KeyError, IndexError) as error:
                        LOGGER.warning("Companion memory unavailable (%s).", type(error).__name__)
                opener = chat.generate_companion_opener(
                    face=face, personality_prompt=personality_prompt
                )
                text, expression = opener.text, opener.expression
            else:
                prepared = self.app.memory_service.prepare(
                    chat,
                    conversation,
                    heard,
                    finished=finished,
                    decision=decision,
                    face=face,
                    personality_prompt=personality_prompt,
                )
                recall_reply = prepared.recall_reply
                detail_labels = prepared.detail_labels
                hierarchy_plan = prepared.hierarchy
                if recall_reply is not None:
                    text = recall_reply
                    chat.remember_reply(heard, text)
                elif finished:
                    text = "Shutting down"
                else:
                    generated = chat.generate_reply(
                        heard,
                        face=face,
                        personality_prompt=personality_prompt,
                        before_tools=decision.require_acceptance if decision else None,
                        check_cancelled=decision.check if decision else None,
                    )
                    text, expression = generated.text, generated.expression
            if decision is not None:
                decision.check()
            check_deadline()
            result = self.app._reply(
                heard,
                text,
                text_only=text_only,
                companion=chat.companion_mode,
                finished=finished,
                expression=expression,
            )
            if decision is not None:
                decision.require_acceptance()
                if new_conversation and self.app.memory is not None and command is None:
                    conversation = self.app.memory.active_conversation(
                        self.app.settings.memory_owner
                    )
            if command is None and not finished:
                check_deadline()
                exchange = Exchange(
                    str(uuid4()),
                    "automatic" if companion_idle else "human",
                    AUTOMATIC_EVENT if companion_idle else heard,
                    text,
                    chat.last_tool_names,
                )
                if self.app.memory is not None and conversation is not None:
                    self.app.memory.append(
                        self.app.settings.memory_owner,
                        conversation,
                        exchange,
                        expected_epoch=self.app.policy_epoch,
                        detail_labels=detail_labels,
                        source_conversations=tuple(chat.memory_sources),
                        followup_conversation=followup_conversation,
                        hierarchy=hierarchy_plan,
                    )
                    chat.memory_revision = (conversation, exchange.id)
                if followup_conversation is not None:
                    chat.last_followup = followup_conversation
                result._exchange_id = exchange.id
                if chat.history:
                    for message in reversed(chat.history):
                        if message.role == "user":
                            message._exchange_id = exchange.id
                            break
        except BaseException:
            chat.restore(previous)
            raise
        if finished:
            del self.app.sessions[session]
        return result
