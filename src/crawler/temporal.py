"""The canonical temporal rendering — one string per instant, on every engine.

A DATE or TIMESTAMP has no one string. PostgreSQL's cast says
``2002-04-01 00:00:00``, SQL Server's says ``Apr  1 2002 12:00AM``, and step 2
compares hashes that came from different engines — so without one agreed
rendering, the same hire date fingerprints differently everywhere and every
date-keyed join is invisible. The catalog's *Temporal rendering* ruling
(adopted P15, fixture-validated digit-for-digit against
``employee.hire_date``) fixes it:

* date part ``YYYY/M/D`` — slash-separated, no zero padding;
* a nonzero time-of-day appends `` H:MM:SS`` (hour unpadded, minutes and
  seconds padded; fractional seconds dropped); midnight appends nothing;
* a TIME-only value renders ``H:MM:SS``.

Rendering happens here, in the crawler, by parsing whatever the engine's CAST
and the driver produced — native objects included. Putting a formatting
expression in the SQL instead would mean one more block per engine per query
to say the same thing, and the blocks are supposed to stay boring.
"""

from __future__ import annotations

from datetime import date, datetime, time

#: String shapes engines actually produce from CAST(temporal AS varchar),
#: tried in order. The ``%b`` forms are SQL Server's legacy varchar rendering
#: (``Apr  1 2002 12:00AM``); its double space is collapsed before matching.
#: The ``%Y/%m/%d`` forms make rendering idempotent — re-parsing an
#: already-rendered value gives the same instant back.
_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%b %d %Y %I:%M%p",
    "%b %d %Y %I:%M:%S%p",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)

_TIME_FORMATS = ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M")


def parse_temporal(value) -> datetime | time | None:
    """The instant behind ``value``, or None when it has no readable one.

    None is an answer, not an error: the caller reports the column rather
    than fingerprinting a slice of it, because a sample where some values
    parsed and some did not is no longer the bottom-k it claims to be.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return value
    if value is None:
        return None
    text = " ".join(str(value).split())  # collapses SQL Server's double space
    if not text:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def render_temporal(value) -> str | None:
    """``value`` in the canonical rendering, or None when it cannot be read."""
    instant = parse_temporal(value)
    if instant is None:
        return None
    if isinstance(instant, time):
        return f"{instant.hour}:{instant.minute:02d}:{instant.second:02d}"
    rendered = f"{instant.year}/{instant.month}/{instant.day}"
    if instant.hour or instant.minute or instant.second or instant.microsecond:
        rendered += f" {instant.hour}:{instant.minute:02d}:{instant.second:02d}"
    return rendered


__all__ = ["parse_temporal", "render_temporal"]
