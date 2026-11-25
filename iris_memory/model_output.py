"""Strict memory JSON parsing and diagnostics that never include model text."""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from iris_domain.expression import strip_thinking_tags

Model = TypeVar("Model", bound=BaseModel)


def parse_memory_json(model: type[Model], text: str) -> Model:
    """Accept one complete JSON fence, never prose, partial JSON or repaired facts."""
    cleaned = strip_thinking_tags(text).strip()
    fenced = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", cleaned, re.DOTALL | re.IGNORECASE)
    return model.model_validate_json(fenced.group(1) if fenced else cleaned)


def validation_reason(error: ValidationError) -> str:
    # Extra-field names can themselves contain private text. Only known schema
    # fields and numeric array positions may appear in a diagnostic.
    allowed = {
        "facts",
        "topics",
        "notes",
        "key",
        "value",
        "kind",
        "scope",
        "lifetime",
        "importance",
        "status",
        "supersedes",
        "evidence",
        "target",
        "label",
        "engaged",
        "text",
        "sources",
        "follow_up_safe",
    }
    reasons = []
    for item in error.errors(include_input=False, include_context=False, include_url=False)[:4]:
        location = (
            ".".join(
                str(part) if isinstance(part, int) or part in allowed else "<field>"
                for part in item["loc"]
            )
            or "response"
        )
        kind = item["type"]
        if not re.fullmatch(r"[a-z_]{1,60}", kind):
            kind = "validation_error"
        reasons.append(f"{location}: {kind}")
    return "; ".join(reasons)
