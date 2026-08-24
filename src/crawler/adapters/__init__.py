"""Engine adapters — the only place engine differences live.

``for_engine("postgres")`` is the whole public surface. Adding an engine means
adding catalog blocks and one adapter class here; nothing else in the crawler
knows what a Teradata is.
"""

from __future__ import annotations

from ..errors import AdapterError
from .ansi import AnsiAdapter
from .base import Adapter
from .postgres import PostgresAdapter
from .sqlserver import SqlServerAdapter
from .teradata import TeradataAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    PostgresAdapter.engine: PostgresAdapter,
    SqlServerAdapter.engine: SqlServerAdapter,
    TeradataAdapter.engine: TeradataAdapter,
}


def for_engine(engine: str) -> Adapter:
    """The adapter for ``engine``, or raise :class:`AdapterError`."""
    try:
        return ADAPTERS[engine]()
    except KeyError:
        raise AdapterError(
            f"no adapter for engine {engine!r}; "
            f"have {', '.join(sorted(ADAPTERS))}"
        ) from None


__all__ = [
    "Adapter", "AnsiAdapter", "PostgresAdapter", "SqlServerAdapter",
    "TeradataAdapter", "ADAPTERS", "for_engine",
]
