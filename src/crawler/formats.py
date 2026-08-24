"""Format-pattern classification — the ``format:`` line, computed not guessed.

The vocabulary is the fixture bundles': ``all-digits``, ``alpha``, ``mixed``,
``email``, ``phone-like``. A column's format is a plurality judgement over a
sample of its distinct values (catalog B4, adopted P14) — a postal-code
column whose min and max are ``00-358`` and ``X1A 1N6`` is still
``all-digits``, because most of its values are, which is exactly why min and
max could never be the source.

Per-value rules, checked in order:

* ``email`` — one ``@`` with a dotted domain behind it.
* ``all-digits`` — digits and nothing else.
* ``phone-like`` — no letters, at least seven digits, and everything else
  drawn from phone punctuation (``+ - ( )`` and spaces). The digit floor and
  the punctuation whitelist are both load-bearing: ``00-358`` has five digits
  and stays ``mixed``; ``123456789.99`` has a dot and stays ``mixed``.
* ``alpha`` — letters plus the punctuation names carry (space, dot, dash,
  apostrophe). ``Apple Inc.`` and ``O'Brien`` are alpha; a comma is not
  name punctuation, so a multi-composer list like ``A. F. Iommi, W. Ward``
  is ``mixed`` — the fixtures draw the line there.
* ``mixed`` — everything else, including every rendered temporal.

The sample values are read transiently and dropped; only the category
persists. Per the adopted P14 ruling that includes sensitive-listed columns —
a category is not a value — unless config withdraws it.
"""

from __future__ import annotations

import re
from collections import Counter

ALL_DIGITS = "all-digits"
ALPHA = "alpha"
MIXED = "mixed"
EMAIL = "email"
PHONE_LIKE = "phone-like"

#: Every category the classifier can produce — the OKF ``format:`` vocabulary.
FORMATS = (ALL_DIGITS, ALPHA, MIXED, EMAIL, PHONE_LIKE)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PUNCTUATION = set("+-() ")
_NAME_PUNCTUATION = set(" .-'’")
_PHONE_DIGIT_FLOOR = 7


def classify_value(value) -> str:
    """The category of one value. Values arrive normalized (trimmed,
    upper-cased) from the B4/C1 samples; nothing here depends on case."""
    text = str(value).strip()
    if not text:
        return MIXED
    if _EMAIL.match(text):
        return EMAIL
    if text.isdigit():
        return ALL_DIGITS
    if not any(ch.isalpha() for ch in text):
        digits = sum(ch.isdigit() for ch in text)
        rest = {ch for ch in text if not ch.isdigit()}
        if digits >= _PHONE_DIGIT_FLOOR and rest <= _PHONE_PUNCTUATION:
            return PHONE_LIKE
        return MIXED
    if all(ch.isalpha() or ch in _NAME_PUNCTUATION for ch in text):
        return ALPHA
    return MIXED


def classify_column(values) -> str | None:
    """The plurality category of a value sample, or None for an empty one.

    Plurality, not majority: a title column where 40% of values carry a
    digit is still an alpha column if alpha is the biggest bucket. A tie is
    ``mixed`` — two equally common shapes is what mixed means.
    """
    counts = Counter(classify_value(value) for value in values)
    if not counts:
        return None
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return MIXED
    return ranked[0][0]


__all__ = [
    "ALL_DIGITS",
    "ALPHA",
    "EMAIL",
    "FORMATS",
    "MIXED",
    "PHONE_LIKE",
    "classify_column",
    "classify_value",
]
