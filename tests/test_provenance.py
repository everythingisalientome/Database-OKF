"""Unit tests for provenance tags."""

from __future__ import annotations

import pytest

from okf import ParseError, Provenance
from okf.provenance import CONFIRMED, INFERRED, OBSERVED, STALE_CONFIRMED


@pytest.mark.parametrize(
    "text,kind,confidence",
    [
        ("[observed]", OBSERVED, None),
        ("[inferred:high]", INFERRED, "high"),
        ("[inferred:medium]", INFERRED, "medium"),
        ("[inferred:low]", INFERRED, "low"),
        ("[confirmed]", CONFIRMED, None),
        ("[stale-confirmed]", STALE_CONFIRMED, None),
    ],
)
def test_round_trip(text, kind, confidence):
    prov = Provenance.parse(text)
    assert (prov.kind, prov.confidence) == (kind, confidence)
    assert str(prov) == text


@pytest.mark.parametrize(
    "text",
    [
        "[inferred]",            # confidence is mandatory
        "[inferred:certain]",    # not one of high|medium|low
        "[observed:high]",       # observed takes no confidence
        "[guessed]",
        "observed",
        "[]",
    ],
)
def test_invalid_tags_raise(text):
    with pytest.raises(ParseError):
        Provenance.parse(text)


def test_hitl_queue_membership():
    """Review-by-exception: low-confidence and stale-confirmed only."""
    assert Provenance.parse("[inferred:low]").needs_review
    assert Provenance.parse("[stale-confirmed]").needs_review
    assert not Provenance.parse("[inferred:medium]").needs_review
    assert not Provenance.parse("[inferred:high]").needs_review
    assert not Provenance.parse("[observed]").needs_review
    assert not Provenance.parse("[confirmed]").needs_review


def test_confirmed_outranks_and_is_human():
    assert Provenance.parse("[confirmed]").is_human
    assert Provenance.parse("[stale-confirmed]").is_human
    assert not Provenance.parse("[inferred:high]").is_human
