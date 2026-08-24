"""Value normalization for Tier C, and the record of what it did.

Two databases only agree about a value if they agree about how to write it.
`` 0012 `` and ``12`` are the same account number and three different strings,
so C1 normalizes before hashing: trim, upper-case, and strip leading zeros
from all-digit values. The first two happen inside the C1 statement — the
bottom-k selection ranks the *normalized* value, so both sides of any future
comparison keep the same slice of the value universe, and that only works if
normalization precedes selection. The leading-zero rule happens here.

The other half of this module is the record. specs/00 decision 7: the rules
applied are stored per column and step 3 re-applies them in generated SQL, so
"what did we do to these values" has to survive into the bundle. C1 returns
one un-normalized representative per kept value (catalog P13) precisely so
that this module can answer it by observation rather than by assumption: a
rule is recorded when it demonstrably changed a value in the sample, which is
why ``genre.name`` records ``[uppercase]`` and ``customer.city`` records
``[trim, uppercase]`` even though the same statement ran for both.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Rule names, in the order the pipeline applies them. These strings are the
#: OKF vocabulary — they appear in bundles and step 2 edges read them.
TRIM = "trim"
UPPERCASE = "uppercase"
STRIP_LEADING_ZEROS = "strip-leading-zeros"
RULES = (TRIM, UPPERCASE, STRIP_LEADING_ZEROS)


def strip_leading_zeros(value: str) -> str:
    """``"0012"`` -> ``"12"``; anything not all-digits is returned unchanged.

    ``"000"`` becomes ``"0"``, not the empty string: the value is a zero, and
    dropping it entirely would make it collide with a genuinely empty one.
    """
    if not value or not value.isdigit():
        return value
    stripped = value.lstrip("0")
    return stripped or "0"


@dataclass(frozen=True)
class Normalized:
    """One C1 sample, normalized, with the rules that actually applied."""

    #: Normalized values, de-duplicated, in the order C1 returned them
    #: (selection-hash order — so truncating this list is still a bottom-k).
    values: tuple[str, ...]
    #: Applied rules in pipeline order, for the OKF ``normalization:`` line.
    rules: tuple[str, ...]
    #: Distinct normalized values before de-duplication here, for reporting
    #: how much the leading-zero rule merged.
    collapsed: int = 0
    #: Row count per value (the sample's ``freq``), aligned with ``values``;
    #: None per value where the sample carried none. Merged values sum theirs
    #: — a merge is the same rows answering to one spelling.
    freqs: tuple[int | None, ...] = ()

    def head(self, cap: int) -> Normalized:
        """The first ``cap`` values — still a bottom-k, since C1 returns rows
        in selection-hash order and this only shortens that ranking."""
        if cap is None or cap >= len(self.values):
            return self
        return Normalized(
            self.values[:cap], self.rules, self.collapsed, self.freqs[:cap]
        )


def normalize_sample(rows) -> Normalized:
    """Read C1's ``(v, raw)`` rows into normalized values and applied rules.

    ``v`` is what the engine produced — ``UPPER(TRIM(...))``, cast to VARCHAR
    first for non-character columns. ``raw`` is one un-normalized
    representative of the same group. Comparing them is what makes the rule
    record an observation: the engine's own upper-casing is the authority on
    whether upper-casing changed anything, not Python's guess at it.
    """
    applied = {TRIM: False, UPPERCASE: False, STRIP_LEADING_ZEROS: False}
    seen: dict[str, int | None] = {}
    collapsed = 0

    for row in rows:
        value = row[0]
        raw = row[1] if len(row) > 1 else None
        freq = row[2] if len(row) > 2 else None
        if value is None:
            # C1 filters NULLs out; a NULL here means the normalization
            # produced one, which no rule in RULES can do. Skip rather than
            # hash the string "None".
            continue
        value = str(value)
        if raw is not None:
            raw = str(raw)
            trimmed = raw.strip()
            if trimmed != raw:
                applied[TRIM] = True
            if trimmed != value:
                applied[UPPERCASE] = True

        final = strip_leading_zeros(value)
        if final != value:
            applied[STRIP_LEADING_ZEROS] = True
        if final in seen:
            collapsed += 1
            if freq is not None and seen[final] is not None:
                seen[final] += int(freq)
            else:
                seen[final] = None
            continue
        seen[final] = None if freq is None else int(freq)

    return Normalized(
        values=tuple(seen),
        rules=tuple(rule for rule in RULES if applied[rule]),
        collapsed=collapsed,
        freqs=tuple(seen.values()),
    )


__all__ = [
    "TRIM",
    "UPPERCASE",
    "STRIP_LEADING_ZEROS",
    "RULES",
    "Normalized",
    "normalize_sample",
    "strip_leading_zeros",
]
