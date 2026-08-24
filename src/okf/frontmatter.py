"""The YAML-subset frontmatter block at the head of every OKF document.

Deliberately not PyYAML. OKF frontmatter is a fixed, tiny subset — flat
``key: value`` scalars, single-line flow lists, and trailing ``  # ``
comments — and bundles are git-committed per refresh with the diff as the
drift signal. A general YAML round trip reorders keys and drops comments,
which would show up as spurious drift on every write. This parser keeps key
order and comment text verbatim so read/write is byte-exact.

Grammar (one entry per line, no nesting):

    key: scalar
    key: [item, item]
    key: scalar  # trailing comment

Scalars are typed as bool (``true``/``false``), int, ``datetime.date``
(``YYYY-MM-DD``), or str. List items are always str — ``built_from`` and
``flags`` entries carry their own internal syntax, parsed by their callers.

Known limit: a value containing two-or-more spaces followed by ``#`` is read
as a trailing comment. No OKF field is expected to contain that sequence.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from .errors import ParseError

FENCE = "---"

_ENTRY_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?: (?P<value>.*))?$")
_COMMENT_RE = re.compile(r"^(?P<value>.*?)(?P<comment>\s{2,}#.*)$")
_INT_RE = re.compile(r"^-?\d+$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_scalar(text: str) -> Any:
    """Type one frontmatter scalar. Anything unrecognised stays a str."""
    if text == "true":
        return True
    if text == "false":
        return False
    if _INT_RE.match(text):
        return int(text)
    match = _DATE_RE.match(text)
    if match:
        try:
            return _dt.date(*(int(g) for g in match.groups()))
        except ValueError:
            return text
    return text


def format_scalar(value: Any) -> str:
    """Inverse of :func:`parse_scalar`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_scalar(v) for v in value) + "]"
    return str(value)


def _parse_value(text: str) -> Any:
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",")]
    return parse_scalar(text)


@dataclass
class Field:
    """One frontmatter line: its key, typed value, and trailing comment."""

    key: str
    value: Any
    comment: str = ""  # verbatim, including its leading whitespace and '#'

    def render(self) -> str:
        if self.value is None and not self.comment:
            return f"{self.key}:"
        return f"{self.key}: {format_scalar(self.value)}{self.comment}"


@dataclass
class FrontMatter:
    """An ordered, comment-preserving mapping of frontmatter fields."""

    fields: list[Field] = field(default_factory=list)

    # -- mapping-ish access -------------------------------------------------

    def __contains__(self, key: str) -> bool:
        return any(f.key == key for f in self.fields)

    def __getitem__(self, key: str) -> Any:
        for f in self.fields:
            if f.key == key:
                return f.value
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        for f in self.fields:
            if f.key == key:
                f.value = value
                return
        self.fields.append(Field(key, value))

    def __iter__(self) -> Iterator[str]:
        return (f.key for f in self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def as_dict(self) -> dict[str, Any]:
        return {f.key: f.value for f in self.fields}

    def comment(self, key: str) -> str:
        for f in self.fields:
            if f.key == key:
                return f.comment.lstrip().lstrip("#").strip()
        raise KeyError(key)

    # -- serialisation ------------------------------------------------------

    @classmethod
    def parse(cls, lines: list[str], *, path=None, offset: int = 0) -> FrontMatter:
        """Parse the lines *between* the two ``---`` fences."""
        fields: list[Field] = []
        for i, line in enumerate(lines, start=offset + 1):
            if not line.strip():
                raise ParseError("blank line in frontmatter", path=path, line=i)
            match = _ENTRY_RE.match(line)
            if match is None:
                raise ParseError(
                    f"frontmatter entry is not 'key: value': {line!r}",
                    path=path,
                    line=i,
                )
            raw = match.group("value")
            if raw is None:
                fields.append(Field(match.group("key"), None))
                continue
            comment = ""
            comment_match = _COMMENT_RE.match(raw)
            if comment_match:
                raw = comment_match.group("value")
                comment = comment_match.group("comment")
            key = match.group("key")
            if any(f.key == key for f in fields):
                raise ParseError(
                    f"duplicate frontmatter key: {key!r}", path=path, line=i
                )
            fields.append(Field(key, _parse_value(raw), comment))
        return cls(fields)

    def render(self) -> list[str]:
        return [FENCE, *(f.render() for f in self.fields), FENCE]


def split_frontmatter(text: str, *, path=None) -> tuple[FrontMatter, list[str], int]:
    """Split *text* into (frontmatter, remaining body lines, body start line)."""
    lines = text.split("\n")
    if not lines or lines[0] != FENCE:
        raise ParseError("document does not open with a '---' frontmatter fence",
                         path=path, line=1)
    try:
        end = lines.index(FENCE, 1)
    except ValueError:
        raise ParseError("unterminated frontmatter block", path=path, line=1) from None
    return FrontMatter.parse(lines[1:end], path=path, offset=1), lines[end + 1:], end + 2
