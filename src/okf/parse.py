"""Read and write OKF documents.

The grammar is closed: any body line that is not blank, a heading, a
provenance-tagged content line, or (in an index) a child-index entry is a
:class:`~okf.errors.ParseError`. A lenient reader would drop lines on the
next write, and bundles are rewritten every refresh.
"""

from __future__ import annotations

from pathlib import Path

from .documents import (
    DOCUMENT_TYPES,
    Column,
    ContentLine,
    Document,
    Edge,
    IndexDocument,
    IndexEntry,
    RelationshipDocument,
    TableDocument,
    _CONTENT_RE,
    _EDGE_RE,
    _ENTRY_RE,
    _HEADING_RE,
)
from .errors import ParseError
from .frontmatter import split_frontmatter

COLUMNS_HEADING = "Columns"


def _lex(body: list[str], offset: int, path=None):
    """Yield (lineno, kind, payload) for each body line.

    kind is one of: blank, heading, content, entry.
    """
    for i, raw in enumerate(body):
        lineno = offset + i
        if not raw.strip():
            if raw:
                raise ParseError(
                    f"blank line contains whitespace: {raw!r}", path=path, line=lineno
                )
            yield lineno, "blank", None
            continue
        heading = _HEADING_RE.match(raw)
        if heading:
            yield lineno, "heading", (
                len(heading.group("hashes")),
                heading.group("title"),
            )
            continue
        if _CONTENT_RE.match(raw):
            yield lineno, "content", ContentLine.parse(raw, path=path, lineno=lineno)
            continue
        entry = _ENTRY_RE.match(raw)
        if entry:
            yield lineno, "entry", IndexEntry(entry.group("path"), entry.group("desc"))
            continue
        if raw.startswith("- "):
            raise ParseError(
                f"body line is missing its provenance tag: {raw!r}",
                path=path,
                line=lineno,
            )
        raise ParseError(f"unrecognised body line: {raw!r}", path=path, line=lineno)


def _parse_table(doc: TableDocument, tokens) -> TableDocument:
    seen_columns_heading = False
    current: Column | None = None
    for lineno, kind, payload in tokens:
        if kind == "blank":
            continue
        if kind == "entry":
            raise ParseError(
                "index-style entry line inside a table file", path=doc.path, line=lineno
            )
        if kind == "heading":
            level, title = payload
            if level == 2:
                if title != COLUMNS_HEADING:
                    raise ParseError(
                        f"table file expects '## {COLUMNS_HEADING}', got '## {title}'",
                        path=doc.path,
                        line=lineno,
                    )
                if seen_columns_heading:
                    raise ParseError(
                        f"duplicate '## {COLUMNS_HEADING}' heading",
                        path=doc.path,
                        line=lineno,
                    )
                seen_columns_heading = True
                continue
            if level == 3:
                if not seen_columns_heading:
                    raise ParseError(
                        f"column heading before '## {COLUMNS_HEADING}'",
                        path=doc.path,
                        line=lineno,
                    )
                current = Column(title)
                doc.columns.append(current)
                continue
            raise ParseError(
                f"unexpected heading level {level} in a table file",
                path=doc.path,
                line=lineno,
            )
        # content
        if current is not None:
            current.lines.append(payload)
        elif seen_columns_heading:
            raise ParseError(
                "content line between '## Columns' and the first column heading",
                path=doc.path,
                line=lineno,
            )
        else:
            doc.lines.append(payload)
    if not seen_columns_heading:
        raise ParseError(
            f"table file has no '## {COLUMNS_HEADING}' section", path=doc.path
        )
    return doc


def _parse_index(doc: IndexDocument, tokens) -> IndexDocument:
    seen_section = False
    for lineno, kind, payload in tokens:
        if kind == "blank":
            continue
        if kind == "heading":
            level, title = payload
            if level != 2:
                raise ParseError(
                    f"unexpected heading level {level} in an index file",
                    path=doc.path,
                    line=lineno,
                )
            if seen_section:
                raise ParseError(
                    "index file has more than one child-index section",
                    path=doc.path,
                    line=lineno,
                )
            doc.section = title
            seen_section = True
            continue
        if kind == "entry":
            if not seen_section:
                raise ParseError(
                    "child-index entry before its section heading",
                    path=doc.path,
                    line=lineno,
                )
            doc.entries.append(payload)
            continue
        # content
        if seen_section:
            raise ParseError(
                "provenance-tagged line inside the child-index section",
                path=doc.path,
                line=lineno,
            )
        doc.summary.append(payload)
    if not seen_section:
        raise ParseError("index file has no child-index section", path=doc.path)
    return doc


def _parse_relationship(doc: RelationshipDocument, tokens) -> RelationshipDocument:
    current: Edge | None = None
    for lineno, kind, payload in tokens:
        if kind == "blank":
            continue
        if kind == "entry":
            raise ParseError(
                "index-style entry line inside a relationship file",
                path=doc.path,
                line=lineno,
            )
        if kind == "heading":
            level, title = payload
            if level != 2:
                raise ParseError(
                    f"unexpected heading level {level} in a relationship file",
                    path=doc.path,
                    line=lineno,
                )
            match = _EDGE_RE.match(title)
            if match is None:
                raise ParseError(
                    f"edge heading is not '<left> <-> <right>': '## {title}'",
                    path=doc.path,
                    line=lineno,
                )
            current = Edge(match.group("left"), match.group("right"))
            doc.edges.append(current)
            continue
        if current is None:
            doc.lines.append(payload)
        else:
            current.lines.append(payload)
    return doc


_PARSERS = {
    TableDocument.TYPE: _parse_table,
    IndexDocument.TYPE: _parse_index,
    RelationshipDocument.TYPE: _parse_relationship,
}


def parse_document(text: str, *, path=None, expected_type: str | None = None) -> Document:
    """Parse OKF markdown into the document class named by its ``type`` field."""
    trailing_newline = text.endswith("\n")
    if trailing_newline:
        text = text[:-1]
    frontmatter, body, body_start = split_frontmatter(text, path=path)

    doc_type = frontmatter.get("type")
    if doc_type is None:
        raise ParseError("frontmatter has no 'type' field", path=path, line=2)
    if doc_type not in DOCUMENT_TYPES:
        raise ParseError(
            f"unknown document type: {doc_type!r} "
            f"(expected one of {', '.join(sorted(DOCUMENT_TYPES))})",
            path=path,
            line=2,
        )
    if expected_type is not None and doc_type != expected_type:
        raise ParseError(
            f"expected a {expected_type!r} document, got {doc_type!r}",
            path=path,
            line=2,
        )

    cls = DOCUMENT_TYPES[doc_type]
    doc = cls(frontmatter=frontmatter, path=path, trailing_newline=trailing_newline)
    return _PARSERS[doc_type](doc, _lex(body, body_start, path))


def render_document(doc: Document) -> str:
    """Serialise a document back to OKF markdown."""
    return doc.render()


def read_document(path, *, expected_type: str | None = None) -> Document:
    path = Path(path)
    return parse_document(
        path.read_text(encoding="utf-8"), path=path, expected_type=expected_type
    )


def write_document(doc: Document, path=None) -> Path:
    """Write *doc* to *path* (default: ``doc.path``) as UTF-8."""
    target = Path(path if path is not None else doc.path)
    if target is None:
        raise ValueError("no path given and document has no source path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc.render(), encoding="utf-8")
    return target
