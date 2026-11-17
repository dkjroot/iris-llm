"""Per-turn personality metadata shared by the client and server."""

from __future__ import annotations

import json
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PERSONALITY_HEADER = "X-Iris-Personality"
WEB_PERSONALITY_HEADER = "X-Iris-Web-Personality"
MAX_PERSONALITY_HEADER = 7800

PersonalityName = Annotated[str, StringConstraints(min_length=1, max_length=48)]


class PersonalityContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    names: list[PersonalityName] = Field(min_length=1, max_length=16)
    active: PersonalityName
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        for name in self.names:
            words = name.replace("-", " ").replace("'", " ").replace("’", " ").split(" ")
            if not all(word.isalnum() for word in words):
                raise ValueError(
                    "Use letters, numbers, single spaces, hyphens or apostrophes in names"
                )
        if len({name.casefold() for name in self.names}) != len(self.names):
            raise ValueError("Personality names must be unique, ignoring case")
        if self.active not in self.names:
            raise ValueError("Active personality must be one of the client's names")
        # Keep every prompt valid UTF-8 even when the JSON file used escape sequences.
        self.prompt.encode("utf-8")
        return self

    def to_header(self) -> str:
        value = json.dumps(self.model_dump(), ensure_ascii=True, separators=(",", ":"))
        if len(value) > MAX_PERSONALITY_HEADER:
            raise ValueError("Personality metadata is too large. Shorten the prompt or names.")
        return value


def personality_catalog(prompts: dict[str, object]) -> dict[str, PersonalityContext]:
    names = list(prompts)
    catalog = {
        name: PersonalityContext.model_validate({"names": names, "active": name, "prompt": prompt})
        for name, prompt in prompts.items()
    }
    for context in catalog.values():
        context.to_header()  # Apply the same size limits to both clients.
    return catalog


def personality_command(text: str) -> str | None:
    """Return a requested name, an empty string for listing, or None for chat."""
    phrase = " ".join(text.casefold().split()).strip(" .!?,")
    if phrase == "list personalities":
        return ""
    prefix = "switch to personality "
    return phrase[len(prefix) :] if phrase.startswith(prefix) else None
