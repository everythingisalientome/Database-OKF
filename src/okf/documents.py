"""OKF document models: table files, index files, relationship files.

Parsing is strict and lossless. ``render(parse(text)) == text`` for every
conforming document, including the frontmatter comments and the presence or
absence of a final newline, because bundles are diffed between refresh runs
and a reformat would read as drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ParseError
from .frontmatter import FrontMatter
from .provenance import Provenance

#: Separator between an index entry's path and its one-liner (em dash).
ENTRY_SEPARATOR = " — "

_CONTENT_RE = re.compile(r"^- (?P<tag>\[[^\]]+\]) (?P<text>.*)$")
_ENTRY_RE = re.compile(r"^- `(?P<path>[^`]+)`" + ENTRY_SEPARATOR + r"(?P<desc>.*)$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6}) (?P<title>.*)$")
_KEYED_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_-]*): (?P<value>.*)$")
_COMMENT_RE = re.compile(r"^(?P<value>.*?)(?P<comment>\s{2,}#.*)$")
_EDGE_RE = re.compile(r"^(?P<left>\S+) <-> (?P<right>\S+)$")
_FINGERPRINT_RE = re.compile(r"^(?P<algo>\S+) @ (?P<path>\S+)$")
_BUILT_FROM_RE = re.compile(r"^(?P<bundle>.+)@(?P<date>\d{4}-\d{2}-\d{2})$")


# --------------------------------------------------------------------------
# content lines
# --------------------------------------------------------------------------


@dataclass
class ContentLine:
    """A provenance-tagged body line: ``- [observed] distinct_count: 12``."""

    provenance: Provenance
    text: str

    @classmethod
    def parse(cls, line: str, *, path=None, lineno=None) -> ContentLine:
        match = _CONTENT_RE.match(line)
        if match is None:
            raise ParseError(
                f"content line is missing its provenance tag: {line!r}",
                path=path,
                line=lineno,
            )
        try:
            provenance = Provenance.parse(match.group("tag"))
        except ParseError as exc:
            raise ParseError(str(exc), path=path, line=lineno) from None
        return cls(provenance, match.group("text"))

    def render(self) -> str:
        return f"- {self.provenance} {self.text}"

    # -- convenience accessors ---------------------------------------------

    @property
    def key(self) -> str | None:
        """The ``key`` of a ``key: value`` line, or None for prose."""
        match = _KEYED_RE.match(self.text)
        return match.group("key") if match else None

    @property
    def value(self) -> str | None:
        """The ``value`` of a ``key: value`` line, comment stripped."""
        match = _KEYED_RE.match(self.text)
        if match is None:
            return None
        value = match.group("value")
        comment_match = _COMMENT_RE.match(value)
        return comment_match.group("value") if comment_match else value

    @property
    def comment(self) -> str | None:
        """Text of the trailing ``  # ...`` comment, if any."""
        match = _COMMENT_RE.match(self.text)
        if match is None:
            return None
        return match.group("comment").lstrip().lstrip("#").strip()

    def __str__(self) -> str:
        return self.render()


class _LineBag:
    """Mixin for objects that own a list of :class:`ContentLine`."""

    lines: list[ContentLine]

    def find(self, key: str) -> ContentLine | None:
        """First line whose ``key:`` matches, or None."""
        for line in self.lines:
            if line.key == key:
                return line
        return None

    def find_all(self, key: str) -> list[ContentLine]:
        return [line for line in self.lines if line.key == key]

    def value(self, key: str) -> str | None:
        line = self.find(key)
        return line.value if line else None

    @property
    def observed(self) -> list[ContentLine]:
        return [l for l in self.lines if l.provenance.is_observed]

    @property
    def inferred(self) -> list[ContentLine]:
        return [l for l in self.lines if l.provenance.is_inferred]

    @property
    def needs_review(self) -> list[ContentLine]:
        return [l for l in self.lines if l.provenance.needs_review]


# --------------------------------------------------------------------------
# structural pieces
# --------------------------------------------------------------------------


@dataclass
class FingerprintRef:
    """A ``fingerprint: <algo> @ <path>`` reference to an out-of-band payload.

    The path is relative to the bundle root, keeping the markdown diffable.
    """

    algo: str
    path: str

    @classmethod
    def parse(cls, value: str) -> FingerprintRef:
        match = _FINGERPRINT_RE.match(value.strip())
        if match is None:
            raise ParseError(f"malformed fingerprint reference: {value!r}")
        return cls(match.group("algo"), match.group("path"))

    def render(self) -> str:
        return f"{self.algo} @ {self.path}"

    def __str__(self) -> str:
        return self.render()


@dataclass
class Column(_LineBag):
    """One ``### <name>`` block in a table file."""

    name: str
    lines: list[ContentLine] = field(default_factory=list)

    @property
    def is_sensitive(self) -> bool:
        """True when the column is sensitive-listed: no values in any form."""
        return self.find("sensitive-listed") is not None

    @property
    def fingerprint(self) -> FingerprintRef | None:
        value = self.value("fingerprint")
        return FingerprintRef.parse(value) if value else None

    @property
    def normalization(self) -> list[str]:
        """Normalization rules applied before hashing; ``[none]`` -> ``[]``."""
        value = self.value("normalization")
        if value is None:
            return []
        return _parse_bracket_list(value)

    @property
    def top_values(self) -> str | None:
        return self.value("top_values")

    def render(self) -> list[str]:
        return [f"### {self.name}", *(line.render() for line in self.lines)]


@dataclass
class Edge(_LineBag):
    """One ``## <left> <-> <right>`` block in a relationship file."""

    left: str
    right: str
    lines: list[ContentLine] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return f"{self.left} <-> {self.right}"

    @property
    def is_grade_a(self) -> bool:
        """Grade A paths use only [observed]/[confirmed] evidence."""
        return any(
            l.provenance.is_observed or l.provenance.kind == "confirmed"
            for l in self.lines
        )

    def render(self) -> list[str]:
        return [f"## {self.heading}", *(line.render() for line in self.lines)]


@dataclass
class IndexEntry:
    """One child-index line: ``- `path` — one-liner``."""

    path: str
    description: str

    def render(self) -> str:
        return f"- `{self.path}`{ENTRY_SEPARATOR}{self.description}"


def _parse_bracket_list(value: str) -> list[str]:
    text = value.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ParseError(f"expected a bracketed list, got {value!r}")
    inner = text[1:-1].strip()
    if not inner or inner == "none":
        return []
    return [item.strip() for item in inner.split(",")]


@dataclass
class BuiltFrom:
    """One ``built_from`` pin: ``db/<name>@<date>``."""

    bundle: str
    date: str

    @classmethod
    def parse(cls, value: str) -> BuiltFrom:
        match = _BUILT_FROM_RE.match(value.strip())
        if match is None:
            raise ParseError(f"malformed built_from pin: {value!r}")
        return cls(match.group("bundle"), match.group("date"))

    def render(self) -> str:
        return f"{self.bundle}@{self.date}"


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------


@dataclass
class Document:
    """Common shape: frontmatter plus a typed body."""

    frontmatter: FrontMatter = field(default_factory=FrontMatter)
    #: Source path, when the document was read from disk.
    path: Any = None
    #: Whether the file ended with a newline. Preserved so writes are byte-exact.
    trailing_newline: bool = True

    TYPE: str = field(default="", init=False, repr=False)

    @property
    def type(self) -> str | None:
        return self.frontmatter.get("type")

    def _body(self) -> list[str]:
        raise NotImplementedError

    def render(self) -> str:
        lines = [*self.frontmatter.render(), *self._body()]
        text = "\n".join(lines)
        return text + "\n" if self.trailing_newline else text

    @property
    def content_lines(self) -> list[ContentLine]:
        """Every provenance-tagged line in the document, in order."""
        raise NotImplementedError

    @property
    def needs_review(self) -> list[ContentLine]:
        return [l for l in self.content_lines if l.provenance.needs_review]


@dataclass
class TableDocument(Document, _LineBag):
    """``/okf/db/<db>/<schema>/<table>.md`` — one concept file per table."""

    #: Body lines above ``## Columns`` (the ``Purpose:`` line and friends).
    lines: list[ContentLine] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)

    TYPE = "table"

    @property
    def name(self) -> str | None:
        return self.frontmatter.get("name")

    @property
    def schema(self) -> str | None:
        name = self.name
        return name.split(".", 1)[0] if name and "." in name else None

    @property
    def table(self) -> str | None:
        name = self.name
        return name.split(".", 1)[1] if name and "." in name else name

    @property
    def database(self) -> str | None:
        return self.frontmatter.get("database")

    @property
    def description(self) -> str | None:
        return self.frontmatter.get("description")

    @property
    def description_confirmed(self) -> bool:
        return bool(self.frontmatter.get("description_confirmed", False))

    @property
    def flags(self) -> list[str]:
        return list(self.frontmatter.get("flags") or [])

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None

    @property
    def fingerprint_refs(self) -> list[tuple[Column, FingerprintRef]]:
        out = []
        for col in self.columns:
            ref = col.fingerprint
            if ref is not None:
                out.append((col, ref))
        return out

    @property
    def content_lines(self) -> list[ContentLine]:
        out = list(self.lines)
        for col in self.columns:
            out.extend(col.lines)
        return out

    def _body(self) -> list[str]:
        body: list[str] = [""]
        if self.lines:
            body.extend(line.render() for line in self.lines)
            body.append("")
        body.append("## Columns")
        for col in self.columns:
            body.append("")
            body.extend(col.render())
        return body


@dataclass
class IndexDocument(Document):
    """``index.md`` — the progressive-disclosure surface of a bundle."""

    #: Bundle-level description lines (layer 1).
    summary: list[ContentLine] = field(default_factory=list)
    #: Heading of the child-index section: "Tables" or "Table pairs".
    section: str = "Tables"
    #: Child index (layer 2), one entry per child file.
    entries: list[IndexEntry] = field(default_factory=list)

    TYPE = "index"

    @property
    def database(self) -> str | None:
        return self.frontmatter.get("database")

    @property
    def databases(self) -> list[str]:
        value = self.frontmatter.get("databases")
        if value:
            return list(value)
        single = self.frontmatter.get("database")
        return [single] if single else []

    @property
    def description(self) -> str | None:
        return self.frontmatter.get("description")

    @property
    def completeness(self) -> str | None:
        return self.frontmatter.get("completeness")

    @property
    def is_complete(self) -> bool:
        return self.completeness == "COMPLETE"

    def entry_for(self, path: str) -> IndexEntry | None:
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None

    @property
    def content_lines(self) -> list[ContentLine]:
        return list(self.summary)

    def _body(self) -> list[str]:
        body: list[str] = [""]
        if self.summary:
            body.extend(line.render() for line in self.summary)
            body.append("")
        body.append(f"## {self.section}")
        body.append("")
        body.extend(entry.render() for entry in self.entries)
        return body


@dataclass
class RelationshipDocument(Document, _LineBag):
    """``/okf/rel/<dbA>--<dbB>/<tableA>--<tableB>.md`` — scored edges."""

    #: Body lines above the first edge heading.
    lines: list[ContentLine] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    TYPE = "relationship"

    @property
    def tables(self) -> list[str]:
        return list(self.frontmatter.get("tables") or [])

    @property
    def built_from(self) -> list[BuiltFrom]:
        return [BuiltFrom.parse(v) for v in (self.frontmatter.get("built_from") or [])]

    @property
    def content_lines(self) -> list[ContentLine]:
        out = list(self.lines)
        for edge in self.edges:
            out.extend(edge.lines)
        return out

    def _body(self) -> list[str]:
        body: list[str] = [""]
        if self.lines:
            body.extend(line.render() for line in self.lines)
            body.append("")
        for i, edge in enumerate(self.edges):
            if i:
                body.append("")
            body.extend(edge.render())
        return body


DOCUMENT_TYPES = {
    TableDocument.TYPE: TableDocument,
    IndexDocument.TYPE: IndexDocument,
    RelationshipDocument.TYPE: RelationshipDocument,
}
