"""Memory controls, archive restoration and checkpoint orchestration."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from iris_app.decisions import VoiceDecision
from iris_app.runtime_audio import command_audio
from iris_chat_engines.compatible import ChatEngine
from iris_domain.replies import (
    TurnReply,
)
from iris_memory.controls import MemoryAction, MemoryPolicyError, PendingForget
from iris_memory.details import DetailCommand
from iris_memory.hierarchy import Prepared
from iris_memory.records import ForgetTarget

if TYPE_CHECKING:
    from iris_app.runtime import Brain

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedMemory:
    recall_reply: str | None
    detail_labels: tuple[str, ...]
    hierarchy: Prepared | None


class MemoryOrchestrator:
    def __init__(self, app: Brain) -> None:
        self.app = app

    def _memory_control(
        self, session: str, heard: str, action: MemoryAction, *, text_only: bool
    ) -> TurnReply:
        chat = self.app._session(session)
        companion = chat.companion_mode
        if action in {"private_on", "private_off"}:
            enabled = action == "private_on"
            text = (
                "Private mode on. This conversation won't read or save conversation memory."
                if enabled
                else "Private mode off. Private context discarded; saved memory is available again."
                if self.app.memory is not None
                else "Private mode off. Conversation saving is disabled in configuration."
            )
            result = self.app._reply(
                heard, text, text_only=text_only, companion=companion, transactional=True
            )
            self.app._set_private(enabled)
            self.app.sessions[session].pending_forget = None
            return result
        if action == "status":
            text = (
                "Private mode is on. This conversation isn't using or saving conversation memory."
                if self.app.private_mode
                else "Private mode is off. Conversation saving is "
                + ("enabled." if self.app.memory is not None else "disabled in configuration.")
            )
            result = self.app._reply(heard, text, text_only=text_only, companion=companion)
            chat.pending_forget = None
            return result
        if action == "cancel_forget":
            result = self.app._reply(
                heard,
                "Forgetting cancelled. No saved conversation was deleted.",
                text_only=text_only,
                companion=companion,
            )
            chat.pending_forget = None
            return result
        if self.app.private_mode or self.app.memory is None:
            text = (
                "Private conversation discarded. It wasn't saved."
                if self.app.private_mode
                else "Working conversation discarded. Conversation saving is disabled."
            )
            if action in {"confirm_forget", "forget_recalled"}:
                text = (
                    "There is no pending saved-conversation deletion. No saved history was changed."
                )
            result = self.app._reply(heard, text, text_only=text_only, companion=companion)
            if action == "forget":
                fresh = self.app._new_chat()
                fresh.companion_mode = companion
                self.app.sessions[session] = fresh
            else:
                chat.pending_forget = None
            return result
        if action in {"forget", "forget_recalled"}:
            target = self.app.memory.forget_target(self.app.settings.memory_owner)
            recalled = chat.pending_recall
            if action == "forget_recalled":
                if (
                    recalled is None
                    or recalled.selected is None
                    or recalled.expires_at <= datetime.now(UTC)
                ):
                    return self.app._reply(
                        heard,
                        "Recall and select a saved discussion first.",
                        text_only=text_only,
                        companion=companion,
                    )
                selected = recalled.candidates[recalled.selected].conversation
                target = self.app.memory.forget_target(self.app.settings.memory_owner, selected)
            if target is None or target[2] == 0:
                result = self.app._reply(
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
                result = self.app._reply(
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
            scope = self.app.memory.deletion_scope(self.app.settings.memory_owner, conversation)
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
            result = self.app._reply(heard, text, text_only=text_only, companion=companion)
            chat.pending_forget = PendingForget(
                conversation,
                revision,
                count,
                datetime.now(UTC) + timedelta(minutes=2),
                self.app.policy_epoch,
                scope,
                action != "forget_recalled",
            )
            return result
        pending = chat.pending_forget
        target = self.app.memory.forget_target(self.app.settings.memory_owner)
        if pending is not None and not pending.require_active:
            target = ForgetTarget(
                pending.conversation,
                self.app.memory.revision(self.app.settings.memory_owner, pending.conversation),
                pending.count,
            )
        if (
            pending is None
            or pending.expires_at <= datetime.now(UTC)
            or pending.epoch != self.app.policy_epoch
            or target is None
            or self.app.memory.deletion_scope(self.app.settings.memory_owner, pending.conversation)
            != pending.scope
            or (target[0], target[1]) != (pending.conversation, pending.revision)
        ):
            result = self.app._reply(
                heard,
                "There is no current, unchanged deletion request. "
                "Say 'forget this conversation' again to request deleting "
                "the current saved conversation.",
                text_only=text_only,
                companion=companion,
            )
            chat.pending_forget = None
            return result
        result = self.app._reply(
            heard,
            "The selected saved conversation and its recorded dependent copies are deleted. "
            "Independent backups and exports keep their own copies.",
            text_only=text_only,
            companion=companion,
            transactional=True,
        )
        self.app.memory.forget_current(
            self.app.settings.memory_owner,
            pending.conversation,
            pending.revision,
            expected_epoch=pending.epoch,
            scope=pending.scope,
            require_active=pending.require_active,
        )
        self.app._sync_policy()
        return result

    def _detail_control(
        self, session: str, heard: str, command: DetailCommand, *, text_only: bool
    ) -> TurnReply:
        chat = self.app._session(session)
        chat.pending_forget = None
        if command.action.endswith("_selected"):
            if chat.selected_detail is None:
                return self.app._reply(
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
        if self.app.private_mode or self.app.details is None:
            text = (
                "Saved details are unavailable in private mode."
                if self.app.private_mode
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
            rows = self.app.details.items(
                self.app.settings.memory_owner,
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
            rows = self.app.details.items(self.app.settings.memory_owner, command.label)
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
                pending = self.app.details.snapshot(
                    self.app.settings.memory_owner,
                    command,
                    datetime.now(UTC) + timedelta(minutes=2),
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
        result = self.app._reply(
            heard, text, text_only=text_only, companion=chat.companion_mode, transactional=apply
        )
        if apply and pending is not None and self.app.details is not None:
            self.app.details.apply(self.app.settings.memory_owner, pending)
            self.app._sync_policy()
            self.app.sessions[session].selected_detail = (
                pending.command.label if pending.command.action != "forget" else None
            )
        else:
            chat.pending_detail = pending
            chat.selected_detail = selected
        return result

    def _checkpoint(self, conversation: str | None) -> None:
        if (
            not self.app.private_mode
            and self.app.memory_context is not None
            and conversation is not None
        ):
            try:
                self.app.memory_context.checkpoint(
                    self.app.settings.memory_owner, conversation, force=True
                )
            except sqlite3.Error as error:
                LOGGER.warning("Memory checkpoint deferred (%s).", type(error).__name__)

    def _delete_previous(
        self, heard: str, chat: ChatEngine | None, *, text_only: bool
    ) -> TurnReply:
        companion = chat is not None and chat.companion_mode
        deleted: str | None = None
        removed = False
        if self.app.memory is not None and not self.app.private_mode:
            deleted = self.app.memory.delete_previous(
                self.app.settings.memory_owner, expected_epoch=self.app.policy_epoch
            )
            if deleted is not None:
                self.app._sync_policy()
                chat = None
                removed = True
        elif chat is not None:
            for index in range(len(chat.history) - 1, -1, -1):
                if chat.history[index].role == "user":
                    deleted = chat.history[index]._exchange_id
                    del chat.history[index:]
                    removed = True
                    break

        if chat is not None:
            chat.pending_detail = None
            chat.selected_detail = None
            chat.pending_forget = None
            chat.pending_recall = None
            chat.memory_context = ""
            chat.memory_sources = set()
            chat.last_tool_names = ()
            chat.last_followup = None
        result = TurnReply(
            heard=heard,
            text=(
                "Previous exchange deleted."
                if removed
                else "There's no previous exchange to delete."
            ),
            audio=command_audio(
                "previous-deleted.wav" if removed else "nothing-to-delete.wav",
                text_only=text_only,
            ),
            companion_mode=companion,
        )
        result._deleted_exchange_id = deleted
        return result

    def _restore(self, chat: ChatEngine, *, provisional: bool = False) -> str | None:
        if self.app.memory is None or self.app.private_mode:
            return None
        owner = self.app.settings.memory_owner
        snapshot = self.app.memory.session_snapshot(
            owner,
            self.app.settings.history_turns,
            self.app.settings.memory_context_chars,
            provisional=provisional,
            known=chat.memory_revision,
        )
        if snapshot.policy != (False, self.app.policy_epoch):
            raise MemoryPolicyError("Conversation memory policy changed during restoration")
        conversation = snapshot.conversation
        if conversation is None:
            chat.history = []
            chat.memory_revision = None
            return None
        revision = (conversation, snapshot.revision)
        if snapshot.exchanges is None:
            return conversation
        exchanges = snapshot.exchanges
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

    def prepare(
        self,
        chat: ChatEngine,
        conversation: str | None,
        heard: str,
        *,
        finished: bool,
        decision: VoiceDecision | None,
        face: bool,
        personality_prompt: str,
    ) -> PreparedMemory:
        detail_labels: tuple[str, ...] = ()
        hierarchy_plan = None
        recall_reply: str | None = None
        recalled = None
        if finished:
            self._checkpoint(conversation)
        elif (
            self.app.recall is not None
            and conversation is not None
            and self.app.settings.history_turns
            and self.app.settings.memory_retrieval_chars
        ):
            try:
                resolved = self.app.recall.resolve(
                    self.app.settings.memory_owner,
                    conversation,
                    heard,
                    chat.history,
                    chat.pending_recall,
                )
                chat.pending_recall = resolved.pending
                if resolved.pending is not None:
                    chat.memory_sources.update(c.conversation for c in resolved.pending.candidates)
                recall_reply = resolved.response
                if resolved.handled:
                    recalled = resolved.excerpts
            except sqlite3.Error as error:
                LOGGER.warning("Spoken recall unavailable (%s).", type(error).__name__)
                recall_reply = (
                    "I can't search my saved conversations right now. Please try again in a moment."
                )
        core_context = ""
        if decision is not None:
            decision.check()
        if (
            not finished
            and conversation is not None
            and self.app.memory_context is not None
            and self.app.settings.history_turns
        ):
            hierarchy = self.app.memory_context.hierarchy
            if hierarchy is not None and recall_reply is None:
                try:
                    selected_topic = ""
                    pending = chat.pending_recall
                    if pending is not None and pending.selected is not None:
                        selected_topic = pending.candidates[pending.selected].label
                    hierarchy_plan = hierarchy.prepare(
                        self.app.settings.memory_owner, conversation, heard, selected_topic
                    )
                    core_context, fact_sources = hierarchy.context(
                        self.app.settings.memory_owner,
                        hierarchy.query(heard, hierarchy_plan),
                        hierarchy_plan,
                        limit=800,
                    )
                    chat.memory_sources.update(fact_sources)
                    if hierarchy_plan is not None:
                        chat.memory_sources.update(hierarchy_plan.source_conversations)
                except sqlite3.Error as error:
                    LOGGER.warning("Memory hierarchy unavailable (%s).", type(error).__name__)
            excluded = (
                tuple(
                    label
                    for f in (hierarchy_plan.facts if hierarchy_plan else ())
                    if (label := f.get("detail_label"))
                    and any(
                        change.supersedes == f["id"]
                        for change in hierarchy_plan.interpretation.facts
                    )
                )
                if hierarchy_plan is not None
                else ()
            )
            if self.app.details is not None:
                detail_context, detail_labels = self.app.details.context(
                    self.app.settings.memory_owner, heard, limit=800, excluded=excluded
                )
                # Prospective overrides still count as use of their explicit labels.
                detail_labels = tuple(dict.fromkeys((*detail_labels, *excluded)))
                core_context = detail_context + core_context
        if (
            not finished
            and recall_reply is None
            and self.app.memory_context is not None
            and conversation is not None
        ):
            try:
                chat.memory_context = self.app.memory_context.prepare(
                    self.app.settings.memory_owner,
                    conversation,
                    heard,
                    chat.history,
                    chat._system_prompt(face=face, personality_prompt=personality_prompt),
                    chat.tool_definitions(),
                    recalled,
                    core_context,
                    self.app.memory_context.hierarchy.query(heard, hierarchy_plan)
                    if self.app.memory_context.hierarchy is not None
                    else None,
                )
                if recalled:
                    chat.memory_sources.update(r["conversation"] for r in recalled)
                # Automatic search can copy a detail from another conversation too.
                chat.memory_sources.update(self.app.memory_context.source_conversations)
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
        return PreparedMemory(recall_reply, detail_labels, hierarchy_plan)
