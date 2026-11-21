"""Extension contracts for derived memory views and vector stores."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from iris_memory.records import SearchHit


@runtime_checkable
class DerivedView(Protocol):
    """Derived memory projection updated incrementally from the source archive."""

    def index_batch(self, owner: str, limit: int = 8) -> int: ...


@runtime_checkable
class VectorStore(DerivedView, Protocol):
    """Derived vector index supporting batched encoding and filtered search."""

    minimum: float

    def index_batch(self, owner: str, limit: int = 8) -> int: ...

    def search(
        self,
        owner: str,
        query: str,
        *,
        active: str | None = None,
        explicit: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 6,
    ) -> list[SearchHit]: ...
