"""The model suggests an expression. Only these labels reach the display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args
from unicodedata import category

FaceExpression = Literal[
    "neutral",
    "curious",
    "amused",
    "concerned",
    "focused",
    "delighted",
    "surprised",
    "skeptical",
    "sleepy",
    "sad",
    "playful",
    "proud",
]
FACE_EXPRESSIONS: tuple[FaceExpression, ...] = get_args(FaceExpression)
FACE_HEADER = "X-Iris-Face"
_PREFIX = "[[expression:"
FACE_PROMPT = (
    "\nFor the final spoken reply, put exactly one face cue on the first line by itself, "
    "for example [[expression:curious]]. Use only " + ", ".join(FACE_EXPRESSIONS) + ". "
    "Choose the expression that suits the tone of your reply, or neutral. "
    "After that line, write only the words Iris should say aloud — do not add a second face "
    "cue later in the reply, do not wrap the cue in markdown or quotes, and never say the "
    "expression name as a label. "
    "Double brackets are only for face cues. To use a tool, use the API's function-call "
    "interface. Keep face cues out of tool calls and their arguments."
)

_EXPR_MAP: dict[str, FaceExpression] = {label: label for label in FACE_EXPRESSIONS}
_EXPR_LABELS_RE = "|".join(re.escape(label) for label in FACE_EXPRESSIONS)
_CODE_SPAN_RE = re.compile(r"`+.*?`+", re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<(think|thinking)>", re.IGNORECASE)
_CLOSE_THINK_RE = re.compile(r"</(think|thinking)>", re.IGNORECASE)
_THINK_TAG_NAMES = ("think", "thinking")
_THINK_PREFIXES = (
    "<think>",
    "<thinking>",
    "</think>",
    "</thinking>",
)

_CUE_BODY = (
    r"\[{1,2}\s*(?:"
    r"(?:expression|face)\s*[:=]\s*(?P<val_kv>[a-zA-Z][^\n\]]{0,30}?)"
    rf"|expression\s+(?P<val_sp>{_EXPR_LABELS_RE})"
    rf"|(?P<val_bare>{_EXPR_LABELS_RE})"
    r")\s*\]{1,2}(?!\()"
    r"|"
    r"\[\[\s*expression\s+(?P<val_dsp>[a-zA-Z]+(?:[ \t_-]+[a-zA-Z]+){0,2})\s*\]{1,2}(?!\()"
)
_CUE_RE = re.compile(
    rf"(?<!\\)(?P<wrap>\*{{1,3}}|_{{1,3}})?(?:{_CUE_BODY})(?(wrap)(?P=wrap))",
    re.IGNORECASE,
)
_UNCLOSED_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:\*{1,3}|_{1,3})?\[{1,2}\s*(?:expression|face)\s*[:=][^\n\]]*[ \t]*$"
)
_LEADING_WRAPPED_CUE_RE = re.compile(
    rf"^(?P<wrap>`{{1,3}}|\"|'|(?:\*{{1,3}}|_{{1,3}}))?(?:{_CUE_BODY})(?(wrap)(?P=wrap))",
    re.IGNORECASE,
)
_LEADING_UNCLOSED_LINE_RE = re.compile(
    r"^(?:\*{1,3}|_{1,3}|`|\"|')?\[{1,2}\s*(?:expression|face)\s*[:=][^\n\]]*(?:\r?\n|$)",
    re.IGNORECASE,
)
_LEADING_PLAIN_LINE_RE = re.compile(
    r"^[ \t]*(?:\*{1,3}|_{1,3})?(?:expression|face)[ \t]*:(?:\*{1,3}|_{1,3})?[ \t]*"
    rf"(?:\*{{1,3}}|_{{1,3}})?(?P<label>{_EXPR_LABELS_RE})(?:\*{{1,3}}|_{{1,3}})?"
    r"[.!]?[ \t]*(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_STANDALONE_WRAPPED_CUE_RE = re.compile(
    rf"^[ \t]*(?P<wrapped>(?P<quote>`{{1,3}}|\"|')(?:{_CUE_BODY})(?P=quote))[ \t]*(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_OPEN_EMPHASIS_RE = re.compile(r"(?:^|[\s(\"'\[])(?:\*{1,3}|_{1,3})$")


@dataclass(frozen=True)
class ModelReply:
    text: str
    expression: FaceExpression | None = None


def _valid_expression(raw: str | None) -> FaceExpression | None:
    if not raw:
        return None
    return _EXPR_MAP.get(raw.strip().lower())


def _match_expression(match: re.Match[str]) -> FaceExpression | None:
    for group in ("val_kv", "val_sp", "val_bare", "val_dsp"):
        value = match.group(group)
        if value is not None:
            return _valid_expression(value)
    return None


def _remove_cue_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    buf = text
    removed_at_start = spans[0][0] == 0
    for start, end in reversed(spans):
        line_start = buf.rfind("\n", 0, start) + 1
        line_end = buf.find("\n", end)
        if line_end == -1:
            line_end = len(buf)
        if buf[line_start:start].strip(" \t\r") == "" and buf[end:line_end].strip(" \t\r") == "":
            if line_end < len(buf):
                buf = buf[:line_start] + buf[line_end + 1 :]
            elif line_start > 0 and buf[line_start - 1] == "\n":
                trim = (
                    line_start - 2
                    if line_start >= 2 and buf[line_start - 2] == "\r"
                    else line_start - 1
                )
                buf = buf[:trim]
            else:
                buf = buf[:line_start]
            continue
        left = buf[:start]
        right = buf[end:]
        h_left = left.rstrip(" \t")
        h_right = right.lstrip(" \t")
        if not h_left or h_left.endswith("\n"):
            buf = h_left + h_right
        elif not h_right or h_right[0] in "\r\n":
            buf = h_left + h_right
        else:
            before_ch = h_left[-1]
            after_ch = h_right[0]
            if (
                category(before_ch) in {"Ps", "Pi"}
                or category(after_ch) in {"Pe", "Pf"}
                or after_ch in ",.!?;:%…，。！？；：、"
                or (left == h_left and _OPEN_EMPHASIS_RE.search(h_left) is not None)
            ):
                buf = h_left + h_right
            else:
                buf = h_left + " " + h_right
    if removed_at_start:
        buf = buf.lstrip("\r\n")
    return buf


def _scan_body_cues(text: str) -> tuple[str, FaceExpression | None]:
    code_spans = [(m.start(), m.end()) for m in _CODE_SPAN_RE.finditer(text)]

    def in_code(start: int, end: int) -> bool:
        return any(start < ce and end > cs for cs, ce in code_spans)

    candidates: list[tuple[int, int, FaceExpression | None]] = []
    for match in _CUE_RE.finditer(text):
        if not in_code(match.start(), match.end()):
            candidates.append((match.start(), match.end(), _match_expression(match)))
    for match in _UNCLOSED_LINE_RE.finditer(text):
        if not in_code(match.start(), match.end()):
            candidates.append((match.start(), match.end(), None))
    for match in _LEADING_PLAIN_LINE_RE.finditer(text):
        if not in_code(match.start(), match.end()):
            candidates.append((match.start(), match.end(), _valid_expression(match.group("label"))))
    for match in _STANDALONE_WRAPPED_CUE_RE.finditer(text):
        # A quoted cue on its own line is metadata. Keep cues inside larger code spans.
        start, end = match.span("wrapped")
        if not any(start < ce and end > cs and (start, end) != (cs, ce) for cs, ce in code_spans):
            candidates.append((match.start(), match.end(), _match_expression(match)))
    if not candidates:
        return text, None
    candidates.sort(key=lambda item: (item[0], -item[1]))
    merged: list[tuple[int, int, FaceExpression | None]] = []
    for start, end, expr in candidates:
        if merged and start < merged[-1][1]:
            prev_start, prev_end, prev_expr = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end), prev_expr or expr)
        else:
            merged.append((start, end, expr))
    first_expr = next((expr for _, _, expr in merged if expr is not None), None)
    cleaned = _remove_cue_spans(text, [(start, end) for start, end, _ in merged])
    return cleaned, first_expr


def strip_expression_cues(text: str) -> str:
    """Remove face expression cues outside code spans without changing prose."""
    cleaned, _ = _scan_body_cues(text)
    return cleaned


def extract_expression_cue(text: str) -> tuple[str, FaceExpression | None]:
    """Extract the leading (or first) expression cue and strip all stray cues."""
    working = text.lstrip()
    found: FaceExpression | None = None
    had_leading = False
    while working:
        match = _LEADING_WRAPPED_CUE_RE.match(working)
        if match is not None:
            had_leading = True
            if found is None:
                found = _match_expression(match)
            rest = working[match.end() :].lstrip(" \t")
            if rest.startswith("\r\n"):
                rest = rest[2:].lstrip()
            elif rest.startswith("\n"):
                rest = rest[1:].lstrip()
            working = rest
            continue
        unclosed = _LEADING_UNCLOSED_LINE_RE.match(working)
        if unclosed is not None:
            had_leading = True
            working = working[unclosed.end() :].lstrip()
            continue
        plain = _LEADING_PLAIN_LINE_RE.match(working)
        if plain is not None:
            had_leading = True
            if found is None:
                found = _valid_expression(plain.group("label"))
            working = working[plain.end() :].lstrip()
            continue
        break
    cleaned, body_expr = _scan_body_cues(working)
    if found is None:
        found = body_expr
    if had_leading:
        cleaned = cleaned.lstrip()
    return cleaned, found


def could_be_leading_cue(stripped: str, *, face: bool) -> bool:
    """Return True while a short leading buffer could still complete as a cue or tool marker."""
    if not stripped or "\n" in stripped or len(stripped) >= 64 or "](" in stripped:
        return False
    if stripped == "[" or stripped.startswith("[["):
        return True
    if not face:
        return False
    if stripped.startswith("["):
        return True
    unwrapped = stripped.lstrip("`\"'*_")
    if not unwrapped:
        return True
    if unwrapped.startswith("["):
        return True
    lower = unwrapped.lower()
    if any(prefix.startswith(lower) for prefix in ("expression:", "face:")):
        return True
    return (lower.startswith("expression:") or lower.startswith("face:")) and not any(
        punct in lower for punct in ".!?"
    )


def split_inline_leading_cue(stripped: str) -> tuple[str, FaceExpression | None] | None:
    """If *stripped* starts with a complete cue followed by inline body text, split them."""
    if "\n" in stripped:
        return None
    match = _LEADING_WRAPPED_CUE_RE.match(stripped)
    if match is None:
        return None
    if stripped.startswith("[[") and "]]" not in stripped[: match.end()]:
        if re.match(r"^\[\[[^\n\]]+\][ \t]+\S", stripped) is None:
            return None
    if not stripped[match.end() :].strip():
        return None
    return extract_expression_cue(stripped)


def strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> and <thinking>...</thinking> blocks outside code spans."""
    lowered = text.lower()
    if "<think" not in lowered and "</think" not in lowered:
        return text

    parts: list[str] = []
    i = 0
    n = len(text)
    stripped_any = False
    stripped_at_start = False
    stripped_to_end = False

    while i < n:
        ch = text[i]
        if ch == "`":
            code_match = _CODE_SPAN_RE.match(text, i)
            if code_match is not None:
                parts.append(text[i : code_match.end()])
                i = code_match.end()
                continue
            parts.append(ch)
            i += 1
            continue
        if ch == "<":
            open_match = _OPEN_THINK_RE.match(text, i)
            if open_match is not None:
                tag = open_match.group(1).lower()
                close_re = re.compile(rf"</{tag}>", re.IGNORECASE)
                close_match = close_re.search(text, open_match.end())
                if not any(part.strip() for part in parts):
                    stripped_at_start = True
                stripped_any = True
                if close_match is None:
                    stripped_to_end = True
                    break
                i = close_match.end()
                continue
            close_match = _CLOSE_THINK_RE.match(text, i)
            if close_match is not None:
                if not stripped_any:
                    # Prompt prefilled <think>: drop leading reasoning before </think>.
                    parts.clear()
                    stripped_at_start = True
                stripped_any = True
                i = close_match.end()
                continue
            parts.append(ch)
            i += 1
            continue
        next_tick = text.find("`", i + 1)
        next_lt = text.find("<", i + 1)
        candidates = [pos for pos in (next_tick, next_lt) if pos != -1]
        end = min(candidates) if candidates else n
        parts.append(text[i:end])
        i = end

    if not stripped_any:
        return text
    result = "".join(parts)
    if stripped_at_start:
        result = result.lstrip()
    if stripped_to_end:
        result = result.rstrip()
    return result


class ThinkingTagFilter:
    """Streaming filter that suppresses <think>/<thinking> blocks across chunk boundaries."""

    def __init__(self) -> None:
        self._in_tag: str | None = None
        self._pending = ""
        self.discarded_chars = 0

    def feed(self, chunk: str) -> str:
        if not chunk and not self._pending:
            return ""
        buf = self._pending + chunk
        self._pending = ""
        out: list[str] = []

        while buf:
            if self._in_tag is not None:
                close_re = re.compile(rf"</{self._in_tag}>", re.IGNORECASE)
                match = close_re.search(buf)
                if match is not None:
                    self.discarded_chars += match.start()
                    buf = buf[match.end() :]
                    self._in_tag = None
                    continue
                keep = min(len(buf), len(f"</{self._in_tag}>") - 1)
                self.discarded_chars += len(buf) - keep
                self._pending = buf[len(buf) - keep :] if keep else ""
                buf = ""
                break

            lt = buf.find("<")
            if lt == -1:
                out.append(buf)
                buf = ""
                break
            if lt > 0:
                out.append(buf[:lt])
                buf = buf[lt:]

            lowered = buf.lower()
            matched_tag = False
            for tag in _THINK_TAG_NAMES:
                open_tag = f"<{tag}>"
                close_tag = f"</{tag}>"
                if lowered.startswith(open_tag):
                    self._in_tag = tag
                    buf = buf[len(open_tag) :]
                    matched_tag = True
                    break
                if lowered.startswith(close_tag):
                    # Orphan closing tag: drop it rather than speaking it aloud.
                    buf = buf[len(close_tag) :]
                    matched_tag = True
                    break
            if matched_tag:
                continue

            if any(prefix.startswith(lowered) for prefix in _THINK_PREFIXES):
                self._pending = buf
                buf = ""
                break

            out.append("<")
            buf = buf[1:]

        return "".join(out)

    def finish(self) -> str:
        if self._in_tag is not None:
            self.discarded_chars += len(self._pending)
            self._pending = ""
            self._in_tag = None
            return ""
        tail = self._pending
        self._pending = ""
        if tail.lower() in _THINK_PREFIXES:
            return ""
        return tail


def read_model_reply(text: str, *, face: bool) -> ModelReply:
    cleaned = strip_thinking_tags(text)
    if not face:
        return ModelReply(cleaned)
    body, expression = extract_expression_cue(cleaned)
    return ModelReply(body, expression or "neutral")
