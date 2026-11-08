"""PC-side recognition, conversation and synthesis, with no local audio devices."""

from __future__ import annotations

import argparse
import base64
import hmac
import logging
import os
import sqlite3
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

import httpx
import numpy as np
import numpy.typing as npt
import speech_recognition as sr
from pydantic import BaseModel

from iris_chat_engines.backends import make_backend
from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_droid.protocol import (
    MAX_BODY,
    TurnReply,
    companion_command,
    is_clear_context,
    is_stop_listening,
    read_wav,
    validate_token,
    write_wav,
)
from iris_droid.volume import VolumeResult, volume_command
from iris_memory import AUTOMATIC_EVENT, Exchange, make_memory
from iris_memory.companion import CompanionMemory
from iris_memory.context import MemoryContext
from iris_memory.controls import MemoryAction, MemoryPolicyError, PendingForget, memory_command
from iris_memory.details import DetailCommand, Details, detail_command
from iris_memory.embeddings import EmbeddingError, MiniLMEncoder, model_directory
from iris_memory.index import MemoryIndex
from iris_memory.recall import RecallResolver
from iris_memory.search import SearchArguments, search_memory
from iris_memory.semantic import SemanticMemory
from iris_settings import Settings, load_settings
from iris_stt_engines import make_stt
from iris_stt_engines.ISTT import ISTT
from iris_tools import Skill

LOGGER = logging.getLogger(__name__)

WEB_ASSETS = {
    "/chat": ("index.html", "text/html"),
    "/chat.css": ("chat.css", "text/css"),
    "/chat.js": ("chat.js", "text/javascript"),
    "/mic.js": ("mic.js", "text/javascript"),
    "/speech.js": ("speech.js", "text/javascript"),
}


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]: ...


class Brain:
    def __init__(
        self, settings: Settings, client: httpx.Client, stt: ISTT, tts: Synthesizer
    ) -> None:
        self.settings = settings
        self.client = client
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
                settings, index, make_backend(summary_settings, client)
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
        chat = ChatEngine(settings, self.client)
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

    def _reply(
        self, heard: str, text: str, *, text_only: bool, companion: bool, finished: bool = False
    ) -> TurnReply:
        audio = ""
        if not text_only:
            samples, rate = self.tts.synthesize(text)
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
            audio = base64.b64encode(write_wav(pcm, rate)).decode("ascii")
        result = TurnReply(
            heard=heard,
            text=text,
            audio=audio,
            finished=finished,
            companion_mode=companion and not finished,
        )
        if len(result.model_dump_json().encode()) > MAX_BODY:
            raise ValueError("Synthesized reply exceeds the transport size limit")
        return result

    def _memory_control(
        self, session: str, heard: str, action: MemoryAction, *, text_only: bool
    ) -> TurnReply:
        chat = self._session(session)
        companion = chat.companion_mode
        if action in {"private_on", "private_off"}:
            enabled = action == "private_on"
            text = (
                "Private mode on. This conversation won't read or save conversation memory."
                if enabled
                else "Private mode off. Private context discarded; saved memory is available again."
                if self.memory is not None
                else "Private mode off. Conversation saving is disabled in configuration."
            )
            result = self._reply(heard, text, text_only=text_only, companion=companion)
            self._set_private(enabled)
            self.sessions[session].pending_forget = None
            return result
        if action == "status":
            text = (
                "Private mode is on. This conversation isn't using or saving conversation memory."
                if self.private_mode
                else "Private mode is off. Conversation saving is "
                + ("enabled." if self.memory is not None else "disabled in configuration.")
            )
            result = self._reply(heard, text, text_only=text_only, companion=companion)
            chat.pending_forget = None
            return result
        if action == "cancel_forget":
            result = self._reply(
                heard,
                "Forgetting cancelled. No saved conversation was deleted.",
                text_only=text_only,
                companion=companion,
            )
            chat.pending_forget = None
            return result
        if self.private_mode or self.memory is None:
            text = (
                "Private conversation discarded. It wasn't saved."
                if self.private_mode
                else "Working conversation discarded. Conversation saving is disabled."
            )
            if action in {"confirm_forget", "forget_recalled"}:
                text = (
                    "There is no pending saved-conversation deletion. No saved history was changed."
                )
            result = self._reply(heard, text, text_only=text_only, companion=companion)
            if action == "forget":
                fresh = self._new_chat()
                fresh.companion_mode = companion
                self.sessions[session] = fresh
            else:
                chat.pending_forget = None
            return result
        if action in {"forget", "forget_recalled"}:
            target = self.memory.forget_target(self.settings.memory_owner)
            recalled = chat.pending_recall
            if action == "forget_recalled":
                if (
                    recalled is None
                    or recalled.selected is None
                    or recalled.expires_at <= datetime.now(UTC)
                ):
                    return self._reply(
                        heard,
                        "Recall and select a saved discussion first.",
                        text_only=text_only,
                        companion=companion,
                    )
                selected = recalled.candidates[recalled.selected].conversation
                target = self.memory.forget_target(self.settings.memory_owner, selected)
            if target is None or target[2] == 0:
                result = self._reply(
                    heard,
                    "There are no saved exchanges in the current conversation to delete.",
                    text_only=text_only,
                    companion=companion,
                )
                chat.pending_forget = None
                return result
            conversation, revision, count = target
            recalled = chat.pending_recall
            phrase = " ".join(heard.casefold().split()).strip(" .!?,")
            if (
                recalled is not None
                and recalled.expires_at > datetime.now(UTC)
                and recalled.selected is not None
                and recalled.candidates[recalled.selected].conversation != conversation
                and phrase != "forget the current conversation"
                and action != "forget_recalled"
            ):
                result = self._reply(
                    heard,
                    "The discussion we recalled is in an older saved conversation. "
                    "This control deletes the current saved conversation. "
                    "Say 'forget the current conversation' if that is what you mean.",
                    text_only=text_only,
                    companion=companion,
                )
                chat.pending_forget = None
                return result
            noun = "exchange" if count == 1 else "exchanges"
            scope = self.memory.deletion_scope(self.settings.memory_owner, conversation)
            description = "selected" if action == "forget_recalled" else "current"
            text = (
                f"The {description} saved conversation has {count} {noun}. "
                + (
                    f"Deleting it also deletes {len(scope) - 1} saved conversations that used it, "
                    "including their other topics. "
                    if len(scope) > 1
                    else ""
                )
                + "Say 'confirm forget this conversation' to delete it, or 'cancel forgetting'."
            )
            result = self._reply(heard, text, text_only=text_only, companion=companion)
            chat.pending_forget = PendingForget(
                conversation,
                revision,
                count,
                datetime.now(UTC) + timedelta(minutes=2),
                self.policy_epoch,
                scope,
                action != "forget_recalled",
            )
            return result
        pending = chat.pending_forget
        target = self.memory.forget_target(self.settings.memory_owner)
        if pending is not None and not pending.require_active:
            target = (
                pending.conversation,
                self.memory.revision(self.settings.memory_owner, pending.conversation),
                pending.count,
            )
        if (
            pending is None
            or pending.expires_at <= datetime.now(UTC)
            or pending.epoch != self.policy_epoch
            or target is None
            or self.memory.deletion_scope(self.settings.memory_owner, pending.conversation)
            != pending.scope
            or (target[0], target[1]) != (pending.conversation, pending.revision)
        ):
            result = self._reply(
                heard,
                "There is no current, unchanged deletion request. "
                "Say 'forget this conversation' again to request deleting "
                "the current saved conversation.",
                text_only=text_only,
                companion=companion,
            )
            chat.pending_forget = None
            return result
        result = self._reply(
            heard,
            "The selected saved conversation and its recorded dependent copies are deleted. "
            "Independent backups and exports keep their own copies.",
            text_only=text_only,
            companion=companion,
        )
        self.memory.forget_current(
            self.settings.memory_owner,
            pending.conversation,
            pending.revision,
            expected_epoch=pending.epoch,
            scope=pending.scope,
            require_active=pending.require_active,
        )
        self._sync_policy()
        return result

    def _detail_control(
        self, session: str, heard: str, command: DetailCommand, *, text_only: bool
    ) -> TurnReply:
        chat = self._session(session)
        chat.pending_forget = None
        if command.action.endswith("_selected"):
            if chat.selected_detail is None:
                return self._reply(
                    heard,
                    "Show a remembered detail by its label first.",
                    text_only=text_only,
                    companion=chat.companion_mode,
                )
            command = DetailCommand(
                command.action.removesuffix("_selected"), chat.selected_detail, command.text
            )
        text = ""
        pending = None
        selected = chat.selected_detail
        apply = False
        if self.private_mode or self.details is None:
            text = (
                "Saved details are unavailable in private mode."
                if self.private_mode
                else ("Conversation memory is disabled in configuration.")
            )
        elif command.action == "cancel":
            text = "Memory change cancelled."
        elif command.action == "confirm":
            pending = chat.pending_detail
            if pending is None or pending.expires_at <= datetime.now(UTC):
                text = "There is no current memory change to confirm. Please request it again."
                pending = None
            else:
                text = (
                    "Memory updated." if pending.command.action != "forget" else "Detail forgotten."
                )
                if pending.conversations:
                    text += (
                        " The saved conversations that used its previous value were deleted too."
                    )
                apply = True
        elif command.action in {"list", "show"}:
            rows = self.details.items(
                self.settings.memory_owner,
                command.label if command.action == "show" else None,
                limit=9,
            )
            text = (
                " ".join(f"{r['label']}: {r['text']}." for r in rows[:8])
                if rows
                else (
                    "There are no matching explicitly remembered details. "
                    "Saved conversations are separate."
                )
            )
            selected = rows[0]["label"] if len(rows) == 1 else None
            if len(rows) > 8:
                text += " There are more; ask to show a detail by its label, or export memory."
        else:
            rows = self.details.items(self.settings.memory_owner, command.label)
            if command.action == "remember" and rows:
                text = (
                    f"I already have a detail called {command.label}. "
                    f"Say 'correct {command.label} to' and its new value."
                )
            elif command.action != "remember" and not rows:
                text = (
                    "I couldn't find that exact detail label. "
                    "Say 'list remembered details' to check."
                )
            else:
                pending = self.details.snapshot(
                    self.settings.memory_owner, command, datetime.now(UTC) + timedelta(minutes=2)
                )
                text = (
                    f"Forget the detail called {command.label}?"
                    if command.action == "forget"
                    else f"Remember {command.label} as {command.text}?"
                )
                if pending.conversations:
                    text += (
                        f" This will also delete {len(pending.conversations)} whole saved "
                        "conversations that used its old value, including their other topics."
                    )
                text += " Say 'confirm memory change' or 'cancel memory change'."
        result = self._reply(heard, text, text_only=text_only, companion=chat.companion_mode)
        if apply and pending is not None and self.details is not None:
            self.details.apply(self.settings.memory_owner, pending)
            self._sync_policy()
            self.sessions[session].selected_detail = (
                pending.command.label if pending.command.action != "forget" else None
            )
        else:
            chat.pending_detail = pending
            chat.selected_detail = selected
        return result

    def _checkpoint(self, conversation: str | None) -> None:
        if not self.private_mode and self.memory_context is not None and conversation is not None:
            try:
                self.memory_context.checkpoint(self.settings.memory_owner, conversation, force=True)
            except sqlite3.Error as error:
                LOGGER.warning("Memory checkpoint deferred (%s).", type(error).__name__)

    def _restore(self, chat: ChatEngine) -> str | None:
        if self.memory is None or self.private_mode:
            return None
        conversation = self.memory.active_conversation(self.settings.memory_owner)
        revision = (conversation, self.memory.revision(self.settings.memory_owner, conversation))
        if chat.memory_revision == revision:
            return conversation
        exchanges = self.memory.recent(
            self.settings.memory_owner,
            conversation,
            self.settings.history_turns,
            self.settings.memory_context_chars,
        )
        # Restore only final text pairs. Historical tool calls are never dispatched,
        # and reasoning/arguments/results never leave the live session.
        from iris_chat_engines.messages import Message

        chat.history = [
            message
            for exchange in exchanges
            for message in (
                Message(role="user", content=exchange.user_text),
                Message(role="assistant", content=exchange.assistant_text),
            )
        ]
        chat.memory_revision = revision
        return conversation

    def turn(
        self,
        session: str,
        body: bytes,
        *,
        text_only: bool = False,
        wake_conversation: bool = False,
        companion_idle: bool = False,
        volume_supported: bool = False,
    ) -> TurnReply:
        self._sync_policy()
        if companion_idle:
            chat = self.sessions.get(session)
            if chat is None or not chat.companion_mode:
                return TurnReply(heard="", text="Companion mode is off.")
            heard = ""
        elif text_only:
            heard = body.decode("utf-8").strip()
            if len(heard) > 8000:
                raise ValueError("Text turn exceeds 8000 characters")
        else:
            pcm, rate = read_wav(body, input_audio=True)
            if len(pcm) > rate * 2 * self.settings.phrase_time_limit:
                raise ValueError("Recording exceeds the configured phrase_time_limit")
            heard = self.stt.recognize(sr.AudioData(pcm, rate, 2)).strip()
        if not heard and not companion_idle:
            raise sr.UnknownValueError()
        detail = detail_command(heard)
        if detail is not None:
            return self._detail_control(session, heard, detail, text_only=text_only)
        if heard.casefold().strip(" .!?,") in {
            "don't bring that up again",
            "do not bring that up again",
        }:
            chat = self._session(session)
            target = None
            if not self.private_mode and self.companion_memory is not None:
                target = self.companion_memory.last_offered(
                    self.settings.memory_owner, chat.last_followup
                )
            text = (
                "I don't have a recent memory follow-up to switch off."
                if target is None
                else "I won't use that saved conversation for companion follow-ups again."
            )
            result = self._reply(heard, text, text_only=text_only, companion=chat.companion_mode)
            if target is not None and self.companion_memory is not None and not self.private_mode:
                if not self.companion_memory.block(
                    self.settings.memory_owner, target, self.policy_epoch
                ):
                    raise MemoryPolicyError("The follow-up changed; please try again")
                chat.last_followup = None
            chat.pending_detail = None
            chat.pending_forget = None
            return result
        existing = self.sessions.get(session)
        if existing is not None and not companion_idle:
            existing.pending_detail = None
            existing.selected_detail = None
        action = memory_command(heard)
        if action is not None:
            return self._memory_control(session, heard, action, text_only=text_only)
        existing = self.sessions.get(session)
        if existing is not None and not companion_idle:
            existing.pending_forget = None
        volume = volume_command(heard) if not companion_idle else None
        if volume is not None:
            companion = existing is not None and existing.companion_mode
            if volume_supported:
                return TurnReply(heard=heard, text="", volume=volume, companion_mode=companion)
            return self._reply(
                heard,
                "Please update the client to use speaker volume commands.",
                text_only=text_only,
                companion=companion,
            )
        if is_clear_context(heard):
            if self.memory is not None and not self.private_mode:
                self._checkpoint(self.memory.active_conversation(self.settings.memory_owner))
                self.memory.start_conversation(self.settings.memory_owner)
            existing = self.sessions.get(session)
            companion = existing is not None and existing.companion_mode
            if companion:
                # Reset tool caches too, just as clearing an ordinary session does.
                fresh = self._new_chat()
                fresh.companion_mode = True
                self.sessions[session] = fresh
            else:
                self.sessions.pop(session, None)
            return TurnReply(
                heard=heard,
                text=(
                    "Private context cleared. Saved conversations weren't changed."
                    if self.private_mode
                    else "Context cleared. Earlier conversations are still saved."
                    if self.memory is not None
                    else "Context cleared. Ready for a fresh conversation."
                ),
                companion_mode=companion,
            )
        existing = self.sessions.get(session)
        companion = existing is not None and existing.companion_mode
        if (companion or (wake_conversation and not text_only)) and is_stop_listening(heard):
            # A control utterance isn't a chat turn and doesn't clear history.
            if existing is not None:
                existing.companion_mode = False
            if self.memory is not None and not self.private_mode:
                self._checkpoint(self.memory.active_conversation(self.settings.memory_owner))
            return TurnReply(
                heard=heard,
                text="Waiting for the wake phrase."
                if wake_conversation and not text_only
                else "Companion mode off.",
            )
        finished = heard.casefold().strip(" .!?") == "exit"
        chat = self._session(session)
        # All clients for an owner deliberately share one active conversation.
        # Refresh each turn so another client's clear/append cannot leave stale context.
        conversation = self._restore(chat)
        previous = chat.history.copy()
        previous_mode = chat.companion_mode
        previous_seed = chat.last_companion_seed
        previous_tools = chat.last_tool_names
        previous_memory = chat.memory_context
        previous_recall = chat.pending_recall
        previous_followup = chat.last_followup
        chat.memory_sources = set()
        chat.memory_search_calls = 0
        detail_labels: tuple[str, ...] = ()
        followup_conversation: str | None = None
        hierarchy_plan = None
        try:
            command = companion_command(heard)
            if command is not None:
                if not command:
                    self._checkpoint(conversation)
                chat.companion_mode = command
                text = "Companion mode on. Let's hang out." if command else "Companion mode off."
            elif companion_idle:
                chat.memory_context = ""
                if (
                    not self.private_mode
                    and self.settings.memory_companion_followups
                    and self.settings.history_turns
                    and self.companion_memory is not None
                ):
                    try:
                        followup = self.companion_memory.choose(self.settings.memory_owner)
                        if followup is not None:
                            followup_conversation, chat.memory_context = followup
                            chat.memory_sources.add(followup_conversation)
                    except (sqlite3.Error, ValueError, KeyError, IndexError) as error:
                        LOGGER.warning("Companion memory unavailable (%s).", type(error).__name__)
                text = chat.get_companion_opener()
            else:
                recall_reply: str | None = None
                recalled = None
                if finished:
                    self._checkpoint(conversation)
                elif (
                    self.recall is not None
                    and conversation is not None
                    and self.settings.history_turns
                    and self.settings.memory_retrieval_chars
                ):
                    try:
                        resolved = self.recall.resolve(
                            self.settings.memory_owner,
                            conversation,
                            heard,
                            chat.history,
                            chat.pending_recall,
                        )
                        chat.pending_recall = resolved.pending
                        if resolved.pending is not None:
                            chat.memory_sources.update(
                                c.conversation for c in resolved.pending.candidates
                            )
                        recall_reply = resolved.response
                        if resolved.handled:
                            recalled = resolved.excerpts
                    except sqlite3.Error as error:
                        LOGGER.warning("Spoken recall unavailable (%s).", type(error).__name__)
                        recall_reply = (
                            "I can't search my saved conversations right now. "
                            "Please try again in a moment."
                        )
                core_context = ""
                if (
                    not finished
                    and conversation is not None
                    and self.memory_context is not None
                    and self.settings.history_turns
                ):
                    hierarchy = self.memory_context.hierarchy
                    if hierarchy is not None and recall_reply is None:
                        try:
                            selected_topic = ""
                            pending = chat.pending_recall
                            if pending is not None and pending.selected is not None:
                                selected_topic = pending.candidates[pending.selected].label
                            hierarchy_plan = hierarchy.prepare(
                                self.settings.memory_owner, conversation, heard, selected_topic
                            )
                            core_context, fact_sources = hierarchy.context(
                                self.settings.memory_owner,
                                hierarchy.query(heard, hierarchy_plan),
                                hierarchy_plan,
                                limit=800,
                            )
                            chat.memory_sources.update(fact_sources)
                            if hierarchy_plan is not None:
                                chat.memory_sources.update(hierarchy_plan.source_conversations)
                        except sqlite3.Error as error:
                            LOGGER.warning(
                                "Memory hierarchy unavailable (%s).", type(error).__name__
                            )
                    excluded = (
                        tuple(
                            f["detail_label"]
                            for f in (hierarchy_plan.facts if hierarchy_plan else ())
                            if f.get("detail_label")
                            and any(
                                change.supersedes == f["id"]
                                for change in hierarchy_plan.interpretation.facts
                            )
                        )
                        if hierarchy_plan is not None
                        else ()
                    )
                    if self.details is not None:
                        detail_context, detail_labels = self.details.context(
                            self.settings.memory_owner, heard, limit=800, excluded=excluded
                        )
                        # Prospective overrides still count as use of their explicit labels.
                        detail_labels = tuple(dict.fromkeys((*detail_labels, *excluded)))
                        core_context = detail_context + core_context
                if (
                    not finished
                    and recall_reply is None
                    and self.memory_context is not None
                    and conversation is not None
                ):
                    try:
                        chat.memory_context = self.memory_context.prepare(
                            self.settings.memory_owner,
                            conversation,
                            heard,
                            chat.history,
                            chat._system_prompt(),
                            chat.tools.definitions(),
                            recalled,
                            core_context,
                            self.memory_context.hierarchy.query(heard, hierarchy_plan)
                            if self.memory_context.hierarchy is not None
                            else None,
                        )
                        if recalled:
                            chat.memory_sources.update(r["conversation"] for r in recalled)
                        # Automatic search can copy a detail from another conversation too.
                        chat.memory_sources.update(self.memory_context.source_conversations)
                        if recalled and not chat.memory_context:
                            recall_reply = (
                                "I found a matching discussion, but couldn't load enough detail "
                                "to continue. Try a shorter question."
                            )
                    except sqlite3.Error as error:
                        LOGGER.warning("Derived memory unavailable (%s).", type(error).__name__)
                        chat.memory_context = ""
                        if recalled:
                            recall_reply = (
                                "I found a matching discussion, "
                                "but can't load its details right now. "
                                "Please try again in a moment."
                            )
                if recall_reply is not None:
                    text = recall_reply
                    chat.remember_reply(heard, text)
                else:
                    text = "Shutting down" if finished else chat.get_response(heard)
            result = self._reply(
                heard, text, text_only=text_only, companion=chat.companion_mode, finished=finished
            )
            if (
                self.memory is not None
                and conversation is not None
                and command is None
                and not finished
            ):
                exchange = Exchange(
                    str(uuid4()),
                    "automatic" if companion_idle else "human",
                    AUTOMATIC_EVENT if companion_idle else heard,
                    text,
                    chat.last_tool_names,
                )
                self.memory.append(
                    self.settings.memory_owner,
                    conversation,
                    exchange,
                    expected_epoch=self.policy_epoch,
                    detail_labels=detail_labels,
                    source_conversations=tuple(chat.memory_sources),
                    followup_conversation=followup_conversation,
                    hierarchy=hierarchy_plan,
                )
                if followup_conversation is not None:
                    chat.last_followup = followup_conversation
                chat.memory_revision = (conversation, exchange.id)
        except Exception:
            chat.history = previous
            chat.companion_mode = previous_mode
            chat.last_companion_seed = previous_seed
            chat.last_tool_names = previous_tools
            chat.memory_context = previous_memory
            chat.pending_recall = previous_recall
            chat.last_followup = previous_followup
            raise
        if finished:
            del self.sessions[session]
        return result


def handler_for(
    brain: Brain, token: str, *, web_chat: bool = False
) -> type[BaseHTTPRequestHandler]:
    turn_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 deliberately closes each connection.
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format: str, *args: object) -> None:
            pass  # Don't log conversation bodies, credentials or request headers.

        def respond(
            self,
            status: int,
            body: bytes,
            *,
            private: bool | None = None,
            content_type: str = "application/json",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src data:; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                )
                self.send_header("Permissions-Policy", "microphone=(self)")
            if private is not None:
                self.send_header("X-Iris-Private", "1" if private else "0")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            asset = WEB_ASSETS.get(self.path) if web_chat else None
            if asset is None:
                self.respond(404, b'{"error":"Unknown route"}')
                return
            name, content_type = asset
            body = Path(__file__).with_name("web").joinpath(name).read_bytes()
            if name == "index.html":
                body = body.replace(
                    b"__IRIS_PHRASE_LIMIT__", str(brain.settings.phrase_time_limit).encode()
                )
            self.respond(200, body, content_type=f"{content_type}; charset=utf-8")

        def do_POST(self) -> None:
            if not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
            ):
                self.respond(401, b'{"error":"Authentication required"}')
                return
            if self.path not in {"/v1/voice", "/v1/text", "/v1/volume-result"}:
                self.respond(404, b'{"error":"Unknown route"}')
                return
            # Browser connections can overlap, but the brain and its sessions are shared.
            with turn_lock:
                self.handle_turn()

        def handle_turn(self) -> None:
            text_only = self.path == "/v1/text"
            companion_idle = self.headers.get("X-Iris-Companion-Idle") == "1"
            expected = (
                "application/json"
                if self.path == "/v1/volume-result"
                else "text/plain"
                if text_only or companion_idle
                else "audio/wav"
            )
            if self.headers.get_content_type() != expected:
                self.respond(415, b'{"error":"Unexpected content type"}')
                return
            try:
                session = str(UUID(self.headers.get("X-Iris-Session", "")))
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                    self.respond(413, b'{"error":"Missing or oversized body"}')
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete request")
                if self.path == "/v1/volume-result":
                    brain._sync_policy()
                    result = VolumeResult.model_validate_json(body)
                    chat = brain.sessions.get(session)
                    reply = brain._reply(
                        "",
                        result.description(),
                        text_only=self.headers.get("X-Iris-Text-Only") == "1",
                        companion=chat is not None and chat.companion_mode,
                    )
                else:
                    reply = brain.turn(
                        session,
                        body,
                        text_only=text_only,
                        wake_conversation=self.headers.get("X-Iris-Wake-Conversation") == "1",
                        companion_idle=companion_idle,
                        volume_supported=self.headers.get("X-Iris-Volume") == "1",
                    )
            except (ValueError, EOFError):
                self.respond(400, b'{"error":"Invalid session, text or WAV recording"}')
                return
            except sr.UnknownValueError:
                self.respond(422, b'{"error":"No speech recognized; please try again"}')
                return
            except sqlite3.Error as error:
                LOGGER.error("Conversation memory failed (%s)", type(error).__name__)
                self.respond(
                    503, b'{"error":"Conversation memory unavailable; check the PC archive"}'
                )
                return
            except MemoryPolicyError:
                self.respond(
                    503, b'{"error":"Conversation memory policy changed; please try again"}'
                )
                return
            except ChatError as error:
                if brain.private_mode:
                    LOGGER.warning("Private chat failed (%s)", type(error).__name__)
                else:
                    LOGGER.warning("Chat failed: %s", error)
                self.respond(502, b'{"error":"Chat failed; check the PC model server"}')
                return
            except Exception as error:
                LOGGER.error("Droid turn failed (%s)", type(error).__name__)
                self.respond(503, b'{"error":"Speech service failed; check the PC console"}')
                return
            self.respond(
                200, reply.model_dump_json(exclude_none=True).encode(), private=brain.private_mode
            )

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local-config.json"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web-chat", action="store_true", help="Serve the browser chat at /chat")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        token = os.environ.get("IRIS_DROID_TOKEN", "")
        validate_token(token)
        settings = load_settings(args.config if args.config.exists() else None)
        if not args.config.exists() and args.config != Path("local-config.json"):
            raise ValueError("The requested configuration file does not exist")
        from iris_tts_engines.kokoro_offline import TTS

        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            brain = Brain(settings, client, make_stt(settings), TTS(settings))
            handler = handler_for(brain, token, web_chat=args.web_chat)
            server_class = ThreadingHTTPServer if args.web_chat else HTTPServer
            with server_class((args.host, args.port), handler) as server:
                print(f"Iris droid server listening on {args.host}:{args.port}")
                server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, ImportError, sqlite3.Error, EmbeddingError) as error:
        print(f"Droid server setup: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
