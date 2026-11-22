"""Parse and interactively prompt for start-script model/engine cards."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PureWindowsPath

from pydantic import BaseModel, ConfigDict

_URL_RE = re.compile(r"^(https?://\S+)(?:\s*\((.+)\))?\s*$", re.IGNORECASE)
_ALIAS_RE = re.compile(r"\balias\s*:\s*([^,)]+)", re.IGNORECASE)
_CTX_RE = re.compile(r"\bctx\s*:\s*(\d+)", re.IGNORECASE)


class ParsedModelCard(BaseModel):
    """Structured metadata extracted from a start-script model/engine card."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    model: str
    model_file: str | None = None
    benchmark_label: str | None = None
    base_url: str | None = None
    api_model: str | None = None
    model_context_tokens: int | None = None


def _basename_only(raw_path: str) -> str | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    name = PureWindowsPath(cleaned).name.strip()
    return name or None


def _parse_endpoint(raw: str) -> tuple[str | None, str | None, int | None]:
    match = _URL_RE.match(raw.strip())
    if match is None:
        return None, None, None
    base_url = match.group(1).rstrip("/")
    meta = match.group(2) or ""
    alias_match = _ALIAS_RE.search(meta)
    api_model = alias_match.group(1).strip() if alias_match else None
    ctx_match = _CTX_RE.search(meta)
    ctx_tokens: int | None = None
    if ctx_match is not None:
        val = int(ctx_match.group(1))
        if 2048 <= val <= 2_000_000:
            ctx_tokens = val
    return base_url, (api_model or None), ctx_tokens


def parse_model_card(text: str) -> ParsedModelCard:
    """Parse a Key: Value model/engine card (with optional markdown fences)."""
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        norm_key = " ".join(key.strip().lower().split())
        norm_val = value.strip()
        if norm_key and norm_val:
            fields[norm_key] = norm_val

    raw_engine = fields.get("engine", "").strip()
    engine_ver = fields.get("engine version", "").strip()
    engine = f"{raw_engine} {engine_ver}".strip() if raw_engine else ""

    raw_model = fields.get("model", "").strip()
    model_ver = fields.get("model version", "").strip()
    model_file = _basename_only(fields.get("model file", ""))
    if raw_model and model_ver:
        model = f"{raw_model} ({model_ver})"
    else:
        model = raw_model or (model_file or "")

    if not engine or not model:
        raise ValueError(
            "Model card must include at least 'Engine:' and 'Model:' (or 'Model File:')."
        )

    base_url, api_model, ctx_tokens = _parse_endpoint(fields.get("endpoint", ""))
    benchmark_label = fields.get("benchmark label", "").strip() or None

    return ParsedModelCard(
        engine=engine,
        model=model,
        model_file=model_file,
        benchmark_label=benchmark_label,
        base_url=base_url,
        api_model=api_model,
        model_context_tokens=ctx_tokens,
    )


def prompt_for_model_card(
    *,
    input_fn: Callable[[str], str] | None = None,
    print_fn: Callable[[str], None] | None = None,
) -> ParsedModelCard:
    """Prompt the user to paste a model/engine card or enter Engine and Model manually."""
    read_input = input if input_fn is None else input_fn
    emit = print if print_fn is None else print_fn
    emit(
        "\nPaste your model/engine card below and press Enter on an empty line "
        "(or press Enter immediately to type Engine and Model manually):"
    )
    lines: list[str] = []
    while True:
        try:
            line = read_input("")
        except EOFError:
            break
        if not lines and not line.strip():
            engine = read_input("Engine (e.g. 'koboldcpp 1.106'): ").strip()
            model = read_input("Model (e.g. 'qwen3-8b-q4_k_m'): ").strip()
            if not engine or not model:
                raise ValueError("Both Engine and Model are required.")
            return ParsedModelCard(engine=engine, model=model)
        if lines and not line.strip():
            # Allow a leading ``` fence followed by lines, stopping once a closing
            # blank line arrives after card content.
            if any(":" in existing for existing in lines):
                break
            continue
        lines.append(line)

    if not lines:
        raise ValueError("No model/engine card was entered.")
    return parse_model_card("\n".join(lines))
