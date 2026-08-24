"""Provenance tags — the control that makes an LLM-annotated bundle reviewable.

Every content line in an OKF document carries exactly one tag, so a consumer
can separate what was measured from what a model guessed from what a human
signed off on. See specs/04-okf-conventions.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ParseError

OBSERVED = "observed"
INFERRED = "inferred"
CONFIRMED = "confirmed"
STALE_CONFIRMED = "stale-confirmed"

KINDS = (OBSERVED, INFERRED, CONFIRMED, STALE_CONFIRMED)
CONFIDENCES = ("high", "medium", "low")

#: Confidences that are queued for human attention rather than trusted as-is.
HITL_CONFIDENCES = ("low",)

_TAG_RE = re.compile(r"^\[(?P<kind>[a-z-]+)(?::(?P<conf>[a-z]+))?\]$")


@dataclass(frozen=True)
class Provenance:
    """One parsed provenance tag, e.g. ``[inferred:medium]``."""

    kind: str
    confidence: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown provenance kind: {self.kind!r}")
        if self.kind == INFERRED:
            if self.confidence not in CONFIDENCES:
                raise ValueError(
                    "[inferred] requires a confidence of "
                    f"{'|'.join(CONFIDENCES)}, got {self.confidence!r}"
                )
        elif self.confidence is not None:
            raise ValueError(f"[{self.kind}] does not take a confidence")

    @classmethod
    def parse(cls, text: str) -> Provenance:
        match = _TAG_RE.match(text.strip())
        if match is None:
            raise ParseError(f"not a provenance tag: {text!r}")
        try:
            return cls(match.group("kind"), match.group("conf"))
        except ValueError as exc:
            raise ParseError(str(exc)) from exc

    def __str__(self) -> str:
        if self.confidence is None:
            return f"[{self.kind}]"
        return f"[{self.kind}:{self.confidence}]"

    @property
    def is_observed(self) -> bool:
        return self.kind == OBSERVED

    @property
    def is_inferred(self) -> bool:
        return self.kind == INFERRED

    @property
    def is_human(self) -> bool:
        """True for tags that rest on human judgement (fresh or stale)."""
        return self.kind in (CONFIRMED, STALE_CONFIRMED)

    @property
    def needs_review(self) -> bool:
        """True when the conventions put this line in the HITL queue."""
        if self.kind == STALE_CONFIRMED:
            return True
        return self.kind == INFERRED and self.confidence in HITL_CONFIDENCES


OBSERVED_TAG = Provenance(OBSERVED)
CONFIRMED_TAG = Provenance(CONFIRMED)
