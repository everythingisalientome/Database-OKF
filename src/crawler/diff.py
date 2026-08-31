"""The refresh diff — one bundle compared with its previous self.

Each scheduled run re-emits a database's bundle in place; this module is what
turns "the files changed" into a drift signal the rest of the platform can
act on (specs/01, refresh & drift). It produces two artifacts per refresh:

* a **change report** (markdown, for people): tables added and dropped,
  columns changed, profiles shifted beyond tolerance;
* a **stale manifest** (JSON, for machines): the artifacts a change
  invalidated. Step 2 does not exist yet — the manifest is its future input,
  the contract being that every step 2 edge referencing a listed table is
  stale and must be re-scored, and every step 3 output depending on such a
  table or edge is stale in turn.

Three kinds of change are kept apart because they invalidate differently:

* **schema changes** (column added/dropped, type or nullability changed,
  constraints or indexes changed) always invalidate, and additionally demote
  any ``[confirmed]`` content on the table to stale-confirmed — the manifest
  carries those lines verbatim so nothing is dropped silently (specs/04);
* **profile shifts** (row counts, distinct counts, rates, ranges, top-N,
  fingerprints) invalidate only beyond the configured tolerance — a live
  database's numbers breathe, and re-scoring every edge because a row count
  moved 0.3% would turn the drift signal into noise. Sub-tolerance shifts
  are counted and reported, never silently swallowed;
* **annotation changes** (descriptions, inferred lines) are informational
  only: the model re-worded a guess, the data did not move.

Comparison happens over *snapshots* — everything the diff needs, read out of
a bundle up front — because a refresh overwrites the previous bundle in
place: the caller snapshots before emitting, emits, then diffs. Profile
comparisons strip the ``# source:`` comments, so a stats-vs-live provenance
stamp is not drift; fingerprint payloads are compared by content hash.

Reconciliation propagates: both bundles' completeness flags travel in the
manifest and the report header, and a diff against an INCOMPLETE crawl warns
that absences may be grant gaps rather than real drops.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from okf import DatabaseBundle, read_database_bundle
from okf.documents import Column as OkfColumn
from okf.documents import TableDocument

from .errors import ConfigError, CrawlerError

#: The stale manifest's format tag — bump on any incompatible shape change.
MANIFEST_FORMAT = "okf-stale-manifest/1"

#: What a consumer owes the manifest. Carried in every manifest so the file
#: is self-describing long after this docstring is forgotten.
MANIFEST_CONTRACT = (
    "every step 2 edge referencing a listed table is stale and must be "
    "re-scored; every step 3 output depending on such a table or edge is "
    "stale in turn; [confirmed] lines carried on an entry demote to "
    "stale-confirmed and are re-queued, never dropped"
)

#: Column-line keys that are schema surface: a change here is a change to
#: what the table *is*, not to what its data currently looks like.
_TYPE_KEY = "type"
_CONSTRAINT_KEY = "constraint"
_INDEX_KEY = "index"
#: The combined counts line starts with whichever of these was measured.
_COUNT_KEYS = ("distinct_count", "distinct_ratio", "null_rate")


@dataclass(frozen=True)
class DiffTolerance:
    """How far a profile number may move before it counts as drift.

    ``row_count`` and ``distinct_count`` are relative (a fraction of the
    previous value); ``rate`` is absolute, because null_rate and
    distinct_ratio already live on [0, 1] and a relative tolerance on a
    near-zero rate would flag noise forever.
    """

    row_count: float = 0.10
    distinct_count: float = 0.10
    rate: float = 0.05

    def __post_init__(self):
        for name in ("row_count", "distinct_count", "rate"):
            if getattr(self, name) < 0:
                raise ConfigError(f"refresh tolerance {name} cannot be negative")

    def to_obj(self) -> dict:
        return {
            "row_count": self.row_count,
            "distinct_count": self.distinct_count,
            "rate": self.rate,
        }

    @classmethod
    def from_obj(cls, obj: dict | None) -> DiffTolerance:
        obj = obj or {}
        defaults = cls()
        unknown = set(obj) - set(defaults.to_obj())
        if unknown:
            raise ConfigError(
                f"unknown refresh keys: {', '.join(sorted(unknown))}. "
                "The refresh block holds row_count, distinct_count and rate "
                "tolerances."
            )
        return cls(**{k: obj.get(k, v) for k, v in defaults.to_obj().items()})


# ---------------------------------------------------------------------------
# snapshots — everything the diff needs, read before the refresh overwrites it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnSnapshot:
    """One column's comparable surface, comments stripped."""

    name: str
    type: str | None
    constraints: tuple[str, ...]
    indexes: tuple[str, ...]
    distinct_count: int | None
    distinct_ratio: float | None
    null_rate: float | None
    #: Remaining observed ``key: value`` surface (length, format/range,
    #: top_values, dense_sequence, sensitive-listed, normalization).
    profile: tuple[tuple[str, str], ...]
    #: ``algo @ path #payload-sha256`` — or ``suppressed (<reason>)``.
    fingerprint: str | None
    annotations: tuple[str, ...]
    confirmed: tuple[str, ...]


@dataclass(frozen=True)
class TableSnapshot:
    """One table file's comparable surface."""

    name: str  # schema.table
    path: str  # bundle-relative, e.g. main/album.md
    description: str | None
    flags: tuple[str, ...]
    row_count: int | None
    columns: dict[str, ColumnSnapshot]
    annotations: tuple[str, ...]
    confirmed: tuple[str, ...]

    @property
    def all_confirmed(self) -> tuple[str, ...]:
        out = list(self.confirmed)
        for column in self.columns.values():
            out.extend(column.confirmed)
        return tuple(out)


@dataclass(frozen=True)
class BundleSnapshot:
    """One bundle's comparable surface, taken before it is overwritten."""

    database: str
    engine: str | None
    build_date: date | None
    completeness: str | None
    tables: dict[str, TableSnapshot]


def snapshot(bundle: DatabaseBundle) -> BundleSnapshot:
    """Read everything the diff will need out of ``bundle`` now."""
    tables = {}
    for doc in bundle.tables:
        snap = _table_snapshot(bundle, doc)
        tables[snap.name] = snap
    index = bundle.index
    build_date = index.frontmatter.get("build_date")
    return BundleSnapshot(
        database=bundle.database,
        engine=index.frontmatter.get("engine"),
        build_date=build_date if isinstance(build_date, date) else None,
        completeness=bundle.completeness,
        tables=tables,
    )


def _table_snapshot(bundle: DatabaseBundle, doc: TableDocument) -> TableSnapshot:
    row_count = doc.frontmatter.get("row_count")
    return TableSnapshot(
        name=doc.name,
        path=Path(doc.path).relative_to(bundle.root).as_posix(),
        description=doc.description,
        flags=tuple(doc.flags),
        row_count=row_count if isinstance(row_count, int) else None,
        columns={
            col.name: _column_snapshot(bundle, col) for col in doc.columns
        },
        annotations=tuple(l.render() for l in doc.lines if l.provenance.is_inferred),
        confirmed=tuple(
            l.render() for l in doc.lines if l.provenance.kind == "confirmed"
        ),
    )


def _column_snapshot(bundle: DatabaseBundle, col: OkfColumn) -> ColumnSnapshot:
    constraints, indexes, profile = [], [], []
    counts: dict[str, float | int | None] = {}
    fingerprint = None
    annotations, confirmed = [], []

    for line in col.lines:
        if line.provenance.kind == "confirmed":
            confirmed.append(line.render())
            continue
        if line.provenance.is_inferred:
            annotations.append(line.render())
            continue
        key, value = line.key, line.value
        if key is None:
            profile.append(("_prose", line.text))
        elif key == _TYPE_KEY:
            profile.append((_TYPE_KEY, value))
        elif key == _CONSTRAINT_KEY:
            constraints.append(value)
        elif key == _INDEX_KEY:
            indexes.append(value)
        elif key in _COUNT_KEYS:
            counts.update(_parse_counts(key, value))
        elif key == "format" and "; range: " in value:
            # One emitted line, two facts: a moved range should not read as
            # a changed format in the report.
            format_part, range_part = value.split("; range: ", 1)
            profile.append(("format", format_part))
            profile.append(("range", range_part))
        elif key == "fingerprint":
            fingerprint = _fingerprint_signature(bundle, col)
        elif key == "normalization":
            profile.append((key, value))
        else:
            profile.append((key, value))

    profile_map = dict(profile)
    type_value = profile_map.pop(_TYPE_KEY, None)
    distinct_count = counts.get("distinct_count")
    return ColumnSnapshot(
        name=col.name,
        type=type_value,
        constraints=tuple(sorted(constraints)),
        indexes=tuple(sorted(indexes)),
        distinct_count=int(distinct_count) if distinct_count is not None else None,
        distinct_ratio=_as_float(counts.get("distinct_ratio")),
        null_rate=_as_float(counts.get("null_rate")),
        profile=tuple(sorted(profile_map.items())),
        fingerprint=fingerprint,
        annotations=tuple(annotations),
        confirmed=tuple(confirmed),
    )


def _parse_counts(first_key: str, value: str) -> dict:
    """Parse the combined counts line: ``25; distinct_ratio: 1.0; ...``."""
    counts: dict = {}
    parts = [p.strip() for p in value.split(";")]
    counts[first_key] = _number(parts[0])
    for part in parts[1:]:
        key, sep, rest = part.partition(":")
        if sep:
            counts[key.strip()] = _number(rest.strip())
    return counts


def _number(text: str):
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _as_float(value) -> float | None:
    return None if value is None else float(value)


def _fingerprint_signature(bundle: DatabaseBundle, col: OkfColumn) -> str | None:
    """The comparable form of a fingerprint line.

    A payload reference is compared by *content*: the markdown line is stable
    across refreshes even when every hash inside the payload changed, and a
    changed hash set is exactly the drift step 2 cares about.
    """
    reason = col.fingerprint_suppression
    if reason is not None:
        return f"suppressed ({reason})"
    ref = col.fingerprint
    if ref is None:
        return None
    payload_path = bundle.resolve(ref)
    try:
        digest = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    except OSError:
        digest = "missing-payload"
    return f"{ref.render()} #{digest}"


# ---------------------------------------------------------------------------
# the diff
# ---------------------------------------------------------------------------


@dataclass
class TableChange:
    """One table's delta between two refreshes."""

    table: str
    path: str  # bundle-relative
    change: str  # table-added | table-dropped | table-changed
    schema_reasons: list[str] = field(default_factory=list)
    profile_reasons: list[str] = field(default_factory=list)
    annotation_reasons: list[str] = field(default_factory=list)
    #: ``[confirmed]`` lines from the *previous* bundle, verbatim, when the
    #: change demotes them (schema change or drop). Never silently dropped.
    confirmed_lines: list[str] = field(default_factory=list)

    @property
    def invalidating_reasons(self) -> list[str]:
        return [*self.schema_reasons, *self.profile_reasons]

    def to_obj(self, database: str) -> dict:
        return {
            "artifact": f"db/{database}/{self.path}",
            "table": self.table,
            "change": self.change,
            "reasons": self.invalidating_reasons or [self.change],
            "confirmed_lines": list(self.confirmed_lines),
        }


@dataclass
class RefreshDiff:
    """Everything one refresh changed, ready to render and to serialize."""

    database: str
    engine: str | None
    tolerance: DiffTolerance
    previous_date: date | None = None
    previous_completeness: str | None = None
    current_date: date | None = None
    current_completeness: str | None = None
    baseline: bool = False
    added: list[TableChange] = field(default_factory=list)
    dropped: list[TableChange] = field(default_factory=list)
    changed: list[TableChange] = field(default_factory=list)
    annotation_only: list[TableChange] = field(default_factory=list)
    unchanged: int = 0
    #: Profile shifts observed but inside tolerance — counted, not hidden.
    ignored_shifts: int = 0
    #: Orphaned payload files the refresh removed, bundle-relative.
    orphans_removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def invalidated(self) -> list[TableChange]:
        return [*self.dropped, *self.changed, *self.added]

    @property
    def has_changes(self) -> bool:
        return bool(self.invalidated or self.annotation_only)

    def to_manifest(self) -> dict:
        return {
            "format": MANIFEST_FORMAT,
            "database": self.database,
            "engine": self.engine,
            "completeness": self.current_completeness,
            "baseline": self.baseline,
            "previous": {
                "build_date": _iso(self.previous_date),
                "completeness": self.previous_completeness,
            },
            "current": {
                "build_date": _iso(self.current_date),
                "completeness": self.current_completeness,
            },
            "tolerance": self.tolerance.to_obj(),
            "contract": MANIFEST_CONTRACT,
            "invalidated": [c.to_obj(self.database) for c in self.invalidated],
            "annotation_only": [
                c.to_obj(self.database) for c in self.annotation_only
            ],
            "unchanged_tables": self.unchanged,
            "ignored_shifts": self.ignored_shifts,
            "orphans_removed": list(self.orphans_removed),
            "notes": list(self.notes),
        }

    def render_report(self) -> str:
        lines = [f"# Refresh diff — {self.database} ({self.engine})", ""]
        if self.baseline:
            lines.append(
                "- baseline: no previous bundle to diff against; every "
                "artifact is new and nothing is invalidated"
            )
        else:
            lines.append(
                f"- previous: {_iso(self.previous_date)}, "
                f"{self.previous_completeness}"
            )
        lines.append(
            f"- current: {_iso(self.current_date)}, {self.current_completeness}"
        )
        tol = self.tolerance
        lines.append(
            f"- tolerance: row_count {tol.row_count} (relative), "
            f"distinct_count {tol.distinct_count} (relative), "
            f"rates {tol.rate} (absolute)"
        )
        for note in self.notes:
            lines.append(f"- note: {note}")

        for title, bucket in (
            ("Tables added", self.added),
            ("Tables dropped", self.dropped),
            ("Tables changed", self.changed),
        ):
            if not bucket:
                continue
            lines += ["", f"## {title} ({len(bucket)})"]
            for change in bucket:
                lines.append("")
                lines.append(f"### {change.table} — {change.path}")
                for reason in change.invalidating_reasons:
                    lines.append(f"- {reason}")
                for confirmed in change.confirmed_lines:
                    lines.append(f"- demotes to stale-confirmed: `{confirmed}`")

        if self.annotation_only:
            lines += [
                "",
                f"## Annotation-only changes ({len(self.annotation_only)}) "
                "— informational, nothing invalidated",
                "",
            ]
            for change in self.annotation_only:
                for reason in change.annotation_reasons:
                    lines.append(f"- {change.table}: {reason}")

        if self.orphans_removed:
            lines += ["", "## Housekeeping", ""]
            for orphan in self.orphans_removed:
                lines.append(f"- orphaned fingerprint payload removed: {orphan}")

        lines += [
            "",
            f"{self.unchanged} tables unchanged; "
            f"{self.ignored_shifts} sub-tolerance profile shifts ignored.",
        ]
        return "\n".join(lines) + "\n"


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def diff_snapshots(
    previous: BundleSnapshot | None,
    current: BundleSnapshot,
    tolerance: DiffTolerance | None = None,
    *,
    orphans_removed: tuple[str, ...] = (),
) -> RefreshDiff:
    """Compare two refreshes of one database. ``previous=None`` is the
    baseline run: nothing to compare, nothing invalidated."""
    tolerance = tolerance or DiffTolerance()
    result = RefreshDiff(
        database=current.database,
        engine=current.engine,
        tolerance=tolerance,
        current_date=current.build_date,
        current_completeness=current.completeness,
        orphans_removed=list(orphans_removed),
    )
    if previous is None:
        result.baseline = True
        result.unchanged = len(current.tables)
        return result

    if previous.database != current.database:
        raise CrawlerError(
            f"refusing to diff different databases: previous is "
            f"{previous.database!r}, current is {current.database!r}"
        )
    result.previous_date = previous.build_date
    result.previous_completeness = previous.completeness
    if previous.engine != current.engine:
        result.notes.append(
            f"engine changed: {previous.engine} -> {current.engine}"
        )
    if current.completeness != "COMPLETE":
        result.notes.append(
            f"current bundle is {current.completeness}: absences may be "
            "grant gaps, not real drops"
        )
    if previous.completeness != "COMPLETE":
        result.notes.append(
            f"previous bundle was {previous.completeness}: additions may be "
            "restored visibility, not new tables"
        )

    for name, prev in previous.tables.items():
        if name not in current.tables:
            result.dropped.append(
                TableChange(
                    table=name,
                    path=prev.path,
                    change="table-dropped",
                    confirmed_lines=list(prev.all_confirmed),
                )
            )
    for name, cur in current.tables.items():
        prev = previous.tables.get(name)
        if prev is None:
            result.added.append(
                TableChange(table=name, path=cur.path, change="table-added")
            )
            continue
        change = _diff_table(prev, cur, tolerance, result)
        if change is None:
            result.unchanged += 1
        elif change.invalidating_reasons:
            result.changed.append(change)
        else:
            result.annotation_only.append(change)
    return result


def _diff_table(
    prev: TableSnapshot,
    cur: TableSnapshot,
    tolerance: DiffTolerance,
    result: RefreshDiff,
) -> TableChange | None:
    change = TableChange(table=cur.name, path=cur.path, change="table-changed")
    schema = change.schema_reasons
    profile = change.profile_reasons
    annotation = change.annotation_reasons

    for name in prev.columns:
        if name not in cur.columns:
            schema.append(f"column-dropped: {name}")
    for name, col in cur.columns.items():
        old = prev.columns.get(name)
        if old is None:
            schema.append(f"column-added: {name}")
            continue
        _diff_column(old, col, tolerance, change, result)

    _numeric_shift(
        "row_count", None, prev.row_count, cur.row_count,
        tolerance.row_count, relative=True, reasons=profile, result=result,
    )
    if prev.flags != cur.flags:
        profile.append(
            f"flags-changed: {list(prev.flags)} -> {list(cur.flags)}"
        )
    if prev.description != cur.description:
        annotation.append("description-changed")
    if prev.annotations != cur.annotations:
        annotation.append("table-annotation-changed")

    if not (schema or profile or annotation):
        return None
    if schema:
        # A schema change demotes the table's [confirmed] content: carry the
        # previous lines so the consumer re-queues rather than drops them.
        change.confirmed_lines = list(prev.all_confirmed)
    return change


def _diff_column(
    old: ColumnSnapshot,
    new: ColumnSnapshot,
    tolerance: DiffTolerance,
    change: TableChange,
    result: RefreshDiff,
) -> None:
    name = new.name
    if old.type != new.type:
        change.schema_reasons.append(
            f"type-changed: {name} ({old.type} -> {new.type})"
        )
    if old.constraints != new.constraints:
        change.schema_reasons.append(f"constraint-changed: {name}")
    if old.indexes != new.indexes:
        change.schema_reasons.append(f"index-changed: {name}")

    _numeric_shift(
        "distinct_count", name, old.distinct_count, new.distinct_count,
        tolerance.distinct_count, relative=True,
        reasons=change.profile_reasons, result=result,
    )
    for key in ("distinct_ratio", "null_rate"):
        _numeric_shift(
            key, name, getattr(old, key), getattr(new, key),
            tolerance.rate, relative=False,
            reasons=change.profile_reasons, result=result,
        )

    old_profile, new_profile = dict(old.profile), dict(new.profile)
    for key in sorted(set(old_profile) | set(new_profile)):
        if old_profile.get(key) != new_profile.get(key):
            change.profile_reasons.append(f"{key}-changed: {name}")
    if old.fingerprint != new.fingerprint:
        change.profile_reasons.append(f"fingerprint-changed: {name}")

    if old.annotations != new.annotations:
        change.annotation_reasons.append(f"annotation-changed: {name}")


def _numeric_shift(
    key: str,
    column: str | None,
    old,
    new,
    tolerance: float,
    *,
    relative: bool,
    reasons: list[str],
    result: RefreshDiff,
) -> None:
    """Record a numeric change: beyond tolerance it is a reason, inside it is
    a counted, ignored shift. A number appearing or disappearing is always
    beyond — measurement coverage changed, not just the value."""
    if old == new:
        return
    where = f": {column}" if column else ""
    detail = f"{key}-shifted{where} ({_fmt(old)} -> {_fmt(new)})"
    if old is None or new is None:
        reasons.append(detail)
        return
    delta = abs(new - old)
    if relative:
        beyond = delta > tolerance * abs(old) if old != 0 else True
    else:
        beyond = delta > tolerance
    if beyond:
        reasons.append(detail)
    else:
        result.ignored_shifts += 1


def _fmt(value) -> str:
    return "none" if value is None else str(value)


# ---------------------------------------------------------------------------
# standalone entry point — diff two bundle directories on disk
# ---------------------------------------------------------------------------


def diff_bundles(
    previous_root, current_root, tolerance: DiffTolerance | None = None
) -> RefreshDiff:
    """Diff two on-disk bundles (e.g. a git checkout against a fresh emit)."""
    previous = snapshot(read_database_bundle(previous_root))
    current = snapshot(read_database_bundle(current_root))
    return diff_snapshots(previous, current, tolerance)


def main(argv=None) -> int:  # pragma: no cover — thin wrapper, tested via CLI
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m crawler.diff",
        description="Diff two OKF database bundles: previous refresh against "
        "current. Prints the change report; --manifest writes the stale "
        "manifest for step 2.",
    )
    parser.add_argument("previous", type=Path, help="previous bundle directory")
    parser.add_argument("current", type=Path, help="current bundle directory")
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="write the machine-readable stale manifest here",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="write the markdown change report here (default: stdout)",
    )
    for name, default in DiffTolerance().to_obj().items():
        parser.add_argument(
            f"--{name.replace('_', '-')}-tolerance", type=float,
            default=default, dest=f"{name}_tolerance",
            help=f"{name} tolerance (default {default})",
        )
    args = parser.parse_args(argv)
    tolerance = DiffTolerance(
        row_count=args.row_count_tolerance,
        distinct_count=args.distinct_count_tolerance,
        rate=args.rate_tolerance,
    )
    try:
        diff = diff_bundles(args.previous, args.current, tolerance)
    except CrawlerError as exc:
        print(f"diff failed: {exc}")
        return 1
    report = diff.render_report()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(diff.to_manifest(), indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "MANIFEST_CONTRACT",
    "MANIFEST_FORMAT",
    "BundleSnapshot",
    "ColumnSnapshot",
    "DiffTolerance",
    "RefreshDiff",
    "TableChange",
    "TableSnapshot",
    "diff_bundles",
    "diff_snapshots",
    "snapshot",
]
