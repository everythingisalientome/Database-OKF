"""Validate documents and bundles against specs/04-okf-conventions.md.

Producers here are stricter than OKF v0.1 requires; consumers still tolerate
unknown frontmatter fields, so nothing below faults an *extra* field. What is
faulted is a missing required field, an untagged content line, a child file
the index does not list, a fingerprint reference that does not resolve, or a
sensitive-listed column carrying values in any form.

Severity split: ``error`` for a conventions rule, ``warning`` for a guideline
the conventions state with a hedge ("under ~120 chars", "3-6 sentences").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .bundle import (
    PAIR_SEPARATOR,
    Bundle,
    BundleRoot,
    DatabaseBundle,
    RelationshipBundle,
)
from .documents import (
    Document,
    FingerprintRef,
    IndexDocument,
    RelationshipDocument,
    TableDocument,
)
from .errors import ParseError
from .provenance import CONFIDENCES, INFERRED, KINDS

ERROR = "error"
WARNING = "warning"

TABLE_REQUIRED = ("type", "name", "description", "database", "engine", "crawl_date",
                  "row_count")
RELATIONSHIP_REQUIRED = ("type", "tables", "built_from")
INDEX_REQUIRED = ("type", "description", "build_date", "completeness")

COMPLETENESS_VALUES = ("COMPLETE", "INCOMPLETE", "UNVERIFIED")

#: The specs/04 fingerprint suppression vocabulary: why a gated column
#: carries no payload. Anything else on a ``suppressed (...)`` line is an
#: error — step 2 switches on these strings.
SUPPRESSION_REASONS = ("sensitive", "budget", "unparseable-temporal")

#: Conventions: "Keep one-liners under ~120 chars."
ONE_LINER_LIMIT = 120
#: Conventions: a database bundle description is "3-6 sentences".
SUMMARY_SENTENCES = (3, 6)

_QUALIFIED_NAME_RE = re.compile(r"^[^.\s]+\.[^.\s]+$")
_THREE_PART_NAME_RE = re.compile(r"^[^.\s]+\.[^.\s]+\.[^.\s]+$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Issue:
    """One conventions violation."""

    code: str
    message: str
    path: object = None
    line: int | None = None
    severity: str = ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR

    def __str__(self) -> str:
        where = f"{self.path}" if self.path is not None else "<document>"
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{self.severity.upper()} {self.code} {where}: {self.message}"


def errors(issues) -> list[Issue]:
    return [i for i in issues if i.is_error]


def _sentence_count(text: str) -> int:
    return len([s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()])


def _required(doc: Document, keys, out: list[Issue], code: str) -> None:
    for key in keys:
        value = doc.frontmatter.get(key)
        if key not in doc.frontmatter:
            out.append(Issue(code, f"frontmatter is missing required field {key!r}",
                             doc.path))
        elif value is None or (isinstance(value, (str, list)) and len(value) == 0):
            out.append(Issue(code, f"required frontmatter field {key!r} is empty",
                             doc.path))


def _check_provenance(doc: Document, out: list[Issue]) -> None:
    """Every content line carries exactly one, well-formed, tag."""
    for line in doc.content_lines:
        prov = line.provenance
        if prov.kind not in KINDS:
            out.append(Issue("P001", f"unknown provenance kind {prov.kind!r}: "
                                     f"{line.render()!r}", doc.path))
        elif prov.kind == INFERRED and prov.confidence not in CONFIDENCES:
            out.append(Issue("P002", "[inferred] requires a confidence of "
                                     f"{'|'.join(CONFIDENCES)}: {line.render()!r}",
                             doc.path))
        elif prov.kind != INFERRED and prov.confidence is not None:
            out.append(Issue("P003", f"[{prov.kind}] must not carry a confidence: "
                                     f"{line.render()!r}", doc.path))


# --------------------------------------------------------------------------
# per-document rules
# --------------------------------------------------------------------------


def validate_table(doc: TableDocument, *, bundle: DatabaseBundle | None = None) -> list[Issue]:
    out: list[Issue] = []
    _required(doc, TABLE_REQUIRED, out, "T001")
    _check_provenance(doc, out)

    name = doc.name
    if name and not _QUALIFIED_NAME_RE.match(str(name)):
        out.append(Issue("T002", f"name must be '<schema>.<table>', got {name!r}",
                         doc.path))

    description = doc.frontmatter.get("description")
    if isinstance(description, str) and "\n" in description:
        out.append(Issue("T003", "description must be one line", doc.path))
    confirmed = doc.frontmatter.get("description_confirmed")
    if confirmed is not None and not isinstance(confirmed, bool):
        out.append(Issue("T004", "description_confirmed must be true or false, got "
                                 f"{confirmed!r}", doc.path))

    row_count = doc.frontmatter.get("row_count")
    if row_count is not None and (not isinstance(row_count, int) or row_count < 0):
        out.append(Issue("T005", f"row_count must be a non-negative integer, got "
                                 f"{row_count!r}", doc.path))

    flags = doc.frontmatter.get("flags")
    if flags is not None and not isinstance(flags, list):
        out.append(Issue("T006", f"flags must be a list, got {flags!r}", doc.path))

    if not doc.columns:
        out.append(Issue("T007", "table file has no columns", doc.path))
    seen = set()
    for column in doc.columns:
        if column.name in seen:
            out.append(Issue("T008", f"duplicate column heading: {column.name!r}",
                             doc.path))
        seen.add(column.name)
        if not column.lines:
            out.append(Issue("T009", f"column {column.name!r} has no content lines",
                             doc.path))
        # spec 01 step 6: every column gets an annotator description, and an
        # explicit '[inferred:low] insufficient evidence' when none is possible
        # -- never a silent omission.
        if not any(l.provenance.is_inferred or l.provenance.is_human
                   for l in column.lines):
            out.append(Issue("T010", f"column {column.name!r} has no [inferred]/"
                                     "[confirmed] description line", doc.path))
        if column.is_sensitive and column.top_values is not None:
            out.append(Issue("T011", f"sensitive-listed column {column.name!r} "
                                     "carries top_values", doc.path))
        out.extend(_check_fingerprint_line(doc, column))

    if bundle is not None:
        out.extend(_validate_table_in_bundle(doc, bundle))
    return out


def _check_fingerprint_line(doc: TableDocument, column) -> list[Issue]:
    """One column's ``fingerprint:`` line, in any of its three legal states:
    absent, a payload reference, or the specs/04 suppression form."""
    out: list[Issue] = []
    value = column.value("fingerprint")
    if value is None:
        return out

    reason = column.fingerprint_suppression
    if reason is not None:
        if reason not in SUPPRESSION_REASONS:
            out.append(Issue("T020", f"column {column.name!r} suppression reason "
                                     f"{reason!r} is not one of "
                                     f"{'|'.join(SUPPRESSION_REASONS)}", doc.path))
        return out

    try:
        ref = FingerprintRef.parse(value)
    except ParseError:
        out.append(Issue("T021", f"column {column.name!r} fingerprint line is "
                                 "neither a reference nor 'suppressed (<reason>)': "
                                 f"{value!r}", doc.path))
        return out

    if column.is_sensitive:
        out.append(Issue("T012", f"sensitive-listed column {column.name!r} "
                                 "carries a fingerprint", doc.path))
    if doc.schema and doc.table:
        expected = f"fingerprints/{doc.schema}.{doc.table}.{column.name}.json"
        if ref.path != expected:
            out.append(Issue("T019", f"column {column.name!r} payload path must "
                                     f"be {expected!r} (schema segment required), "
                                     f"got {ref.path!r}", doc.path))
    return out


def _validate_table_in_bundle(doc: TableDocument, bundle: DatabaseBundle) -> list[Issue]:
    out: list[Issue] = []
    if doc.database and doc.database != bundle.name:
        out.append(Issue("T013", f"database {doc.database!r} does not match bundle "
                                 f"directory {bundle.name!r}", doc.path))
    if doc.path is not None and doc.name:
        expected = f"{doc.schema}/{doc.table}.md"
        actual = Path(doc.path).relative_to(bundle.root).as_posix()
        if actual != expected:
            out.append(Issue("T014", f"path is {actual!r} but name {doc.name!r} "
                                     f"implies {expected!r}", doc.path))

    for column in doc.columns:
        try:
            ref = column.fingerprint
        except ParseError:
            continue  # already reported as T021 by the document rules
        if ref is None:
            continue
        target = bundle.resolve(ref)
        if not target.is_file():
            out.append(Issue("T015", f"column {column.name!r} references a missing "
                                     f"fingerprint payload: {ref.path}", doc.path))
            continue
        if Path(ref.path).is_absolute():
            out.append(Issue("T016", f"fingerprint reference must be bundle-relative: "
                                     f"{ref.path}", doc.path))
        try:
            payload = bundle.fingerprint(ref)
        except ParseError as exc:
            out.append(Issue("T017", str(exc), doc.path))
            continue
        if payload.normalization != column.normalization:
            out.append(Issue("T018", f"column {column.name!r} declares normalization "
                                     f"{column.normalization} but payload records "
                                     f"{payload.normalization}", doc.path))
    return out


def validate_relationship(doc: RelationshipDocument, *,
                          bundle: RelationshipBundle | None = None) -> list[Issue]:
    out: list[Issue] = []
    _required(doc, RELATIONSHIP_REQUIRED, out, "R001")
    _check_provenance(doc, out)

    tables = doc.tables
    if len(tables) != 2:
        out.append(Issue("R002", f"tables must name exactly 2 tables, got {tables!r}",
                         doc.path))
    for table in tables:
        if not _THREE_PART_NAME_RE.match(table):
            out.append(Issue("R003", "table reference must be "
                                     f"'<db>.<schema>.<table>', got {table!r}", doc.path))

    for raw in doc.frontmatter.get("built_from") or []:
        try:
            from .documents import BuiltFrom
            BuiltFrom.parse(raw)
        except ParseError:
            out.append(Issue("R004", f"built_from pin must be '<bundle>@<date>', got "
                                     f"{raw!r}", doc.path))

    if not doc.edges:
        out.append(Issue("R005", "relationship file declares no edges", doc.path))
    for edge in doc.edges:
        if not edge.lines:
            out.append(Issue("R006", f"edge {edge.heading!r} has no content lines",
                             doc.path))

    if bundle is not None:
        pair = set(bundle.path_databases)
        for table in tables:
            db = table.split(".", 1)[0]
            if pair and db not in pair:
                out.append(Issue("R007", f"table {table!r} names database {db!r}, "
                                         f"outside the pair {sorted(pair)}", doc.path))
    return out


def validate_index(doc: IndexDocument, *, bundle: Bundle | None = None) -> list[Issue]:
    out: list[Issue] = []
    _required(doc, INDEX_REQUIRED, out, "I001")
    _check_provenance(doc, out)

    has_single = "database" in doc.frontmatter
    has_pair = "databases" in doc.frontmatter
    if not (has_single or has_pair):
        out.append(Issue("I002", "index frontmatter must carry 'database' or "
                                 "'databases'", doc.path))
    if has_pair:
        databases = doc.frontmatter.get("databases") or []
        if len(databases) != 2:
            out.append(Issue("I003", "a relationship index names exactly 2 databases, "
                                     f"got {databases!r}", doc.path))
        elif list(databases) != sorted(databases):
            out.append(Issue("I004", f"databases must be sorted lexicographically so "
                                     f"the pair has one canonical path: {databases!r}",
                             doc.path))

    completeness = doc.completeness
    if completeness is not None and completeness not in COMPLETENESS_VALUES:
        out.append(Issue("I005", f"completeness must be one of "
                                 f"{', '.join(COMPLETENESS_VALUES)}, got "
                                 f"{completeness!r}", doc.path))

    # Layer 1: the bundle-level description.
    if not doc.summary:
        out.append(Issue("I006", "index has no bundle-level description "
                                 "(layer 1 of the index.md contract)", doc.path))
    elif has_single:
        text = " ".join(l.text for l in doc.summary)
        count = _sentence_count(text)
        low, high = SUMMARY_SENTENCES
        if not low <= count <= high:
            out.append(Issue("I007", f"database bundle description should be {low}-"
                                     f"{high} sentences, found {count}", doc.path,
                             severity=WARNING))

    # Layer 2: the child index.
    if not doc.entries:
        out.append(Issue("I008", "index has no child index "
                                 "(layer 2 of the index.md contract)", doc.path))
    seen = set()
    for entry in doc.entries:
        if entry.path in seen:
            out.append(Issue("I009", f"duplicate child index entry: {entry.path!r}",
                             doc.path))
        seen.add(entry.path)
        if Path(entry.path).is_absolute():
            out.append(Issue("I010", f"child index path must be bundle-relative: "
                                     f"{entry.path!r}", doc.path))
        if not entry.description.strip():
            out.append(Issue("I011", f"child index entry {entry.path!r} has no "
                                     "one-liner", doc.path))
        if len(entry.description) > ONE_LINER_LIMIT:
            out.append(Issue("I012", f"child index one-liner for {entry.path!r} is "
                                     f"{len(entry.description)} chars "
                                     f"(guideline: under ~{ONE_LINER_LIMIT})",
                             doc.path, severity=WARNING))
    return out


_DOCUMENT_VALIDATORS = {
    TableDocument: validate_table,
    RelationshipDocument: validate_relationship,
    IndexDocument: validate_index,
}


def validate_document(doc: Document, *, bundle: Bundle | None = None) -> list[Issue]:
    """Validate by document class, not by the frontmatter `type` string --
    a document whose `type` is missing or wrong must still be checkable, and
    the mismatch reported rather than raised."""
    validator = _DOCUMENT_VALIDATORS.get(type(doc))
    if validator is None:
        raise TypeError(f"not an OKF document: {type(doc).__name__}")
    out = validator(doc, bundle=bundle)
    declared = doc.frontmatter.get("type")
    if declared is not None and declared != type(doc).TYPE:
        out.append(Issue("D001", f"frontmatter type {declared!r} does not match a "
                                 f"{type(doc).TYPE!r} document", doc.path))
    return out


# --------------------------------------------------------------------------
# bundle rules
# --------------------------------------------------------------------------


def validate_bundle(bundle: Bundle) -> list[Issue]:
    """Validate the index, every child, and the index<->children contract."""
    out: list[Issue] = validate_index(bundle.index, bundle=bundle)
    for child in bundle.children:
        out.extend(validate_document(child, bundle=bundle))
    out.extend(_validate_child_index(bundle))

    if isinstance(bundle, DatabaseBundle):
        if bundle.index.database and bundle.index.database != bundle.root.name:
            out.append(Issue("B001", f"index database {bundle.index.database!r} does "
                                     f"not match directory {bundle.root.name!r}",
                             bundle.index.path))
        for orphan in bundle.orphan_fingerprints():
            out.append(Issue("B002", "fingerprint payload is not referenced by any "
                                     f"table file: {orphan.name}", orphan))
    elif isinstance(bundle, RelationshipBundle):
        path_pair = bundle.path_databases
        if len(path_pair) != 2:
            out.append(Issue("B003", "relationship bundle directory must be named "
                                     f"'<dbA>{PAIR_SEPARATOR}<dbB>', got "
                                     f"{bundle.root.name!r}", bundle.root))
        else:
            if path_pair != sorted(path_pair):
                out.append(Issue("B004", "pair directory names must be sorted "
                                         "lexicographically so the pair has one "
                                         f"canonical path: {bundle.root.name!r}",
                                 bundle.root))
            declared = bundle.index.databases
            if declared and list(declared) != path_pair:
                out.append(Issue("B005", f"index databases {list(declared)} do not "
                                         f"match directory pair {path_pair}",
                                 bundle.index.path))
    return out


def _validate_child_index(bundle: Bundle) -> list[Issue]:
    """The child index must cover every child file, and only real files."""
    out: list[Issue] = []
    on_disk = set(bundle.child_paths())
    listed = {entry.path for entry in bundle.index.entries}

    for missing in sorted(on_disk - listed):
        out.append(Issue("B006", f"child file is not listed in the index: {missing}",
                         bundle.index.path))
    for extra in sorted(listed - on_disk):
        out.append(Issue("B007", f"index lists a child file that does not exist: "
                                 f"{extra}", bundle.index.path))

    # The child index is "the child's frontmatter description, extracted
    # mechanically" -- only meaningful where the child type carries one.
    by_path = {p: c for p, c in zip(bundle.child_paths(), bundle.children)}
    for entry in bundle.index.entries:
        child = by_path.get(entry.path)
        if child is None or not isinstance(child, TableDocument):
            continue
        description = child.description
        if description and not entry.description.startswith(description):
            out.append(Issue("B008", f"index one-liner for {entry.path} does not "
                                     "carry the child's frontmatter description "
                                     f"({description!r})", bundle.index.path))
    return out


# --------------------------------------------------------------------------
# root rules -- cross-bundle
# --------------------------------------------------------------------------


def validate_root(root: BundleRoot) -> list[Issue]:
    """Validate every bundle plus the cross-bundle built_from/staleness rules."""
    out: list[Issue] = []
    for bundle in root.bundles:
        out.extend(validate_bundle(bundle))

    build_dates = {}
    for db in root.databases:
        build_dates[f"db/{db.name}"] = str(db.index.frontmatter.get("build_date"))

    for rel in root.relationships:
        for doc in rel.relationships:
            for pin in doc.built_from:
                if pin.bundle not in build_dates:
                    out.append(Issue("X001", f"built_from pins {pin.bundle!r}, which "
                                             "is not present under this root",
                                     doc.path, severity=WARNING))
                elif build_dates[pin.bundle] != pin.date:
                    out.append(Issue("X002", f"built_from pins {pin.render()} but that "
                                             f"bundle now reports build_date "
                                             f"{build_dates[pin.bundle]} -- edges are "
                                             "stale", doc.path))
        # Nothing consumes a bundle marked INCOMPLETE without surfacing the flag.
        for name in rel.path_databases:
            source = root.database(name)
            if source is None:
                continue
            if not source.is_complete and rel.is_complete:
                out.append(Issue("X003", f"source bundle {name!r} is "
                                         f"{source.completeness} but this bundle "
                                         "reports COMPLETE -- the flag must propagate",
                                 rel.index.path))
    return out
