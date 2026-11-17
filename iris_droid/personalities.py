"""Compatibility imports for personality metadata."""

from pathlib import Path

from iris_domain.personality import (
    MAX_PERSONALITY_HEADER as MAX_PERSONALITY_HEADER,
)
from iris_domain.personality import (
    PERSONALITY_HEADER as PERSONALITY_HEADER,
)
from iris_domain.personality import (
    WEB_PERSONALITY_HEADER as WEB_PERSONALITY_HEADER,
)
from iris_domain.personality import (
    PersonalityContext as PersonalityContext,
)
from iris_domain.personality import (
    PersonalityName as PersonalityName,
)
from iris_domain.personality import (
    personality_catalog as personality_catalog,
)
from iris_domain.personality import (
    personality_command as personality_command,
)
from iris_settings import read_config


def load_personalities(path: Path | None) -> dict[str, PersonalityContext]:
    if path is None:
        return {}
    try:
        return personality_catalog(read_config(path))
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
