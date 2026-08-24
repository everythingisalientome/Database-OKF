"""Fixture parity — the measuring pass against the ground-truth bundles.

Session 3's acceptance is "measured numbers == fixture numbers, digit for
digit". The bundles under ``tests/fixtures/okf`` are that ground truth, and
this module is the part of the comparison that can be made without a
database: the values behind seventeen of the fixtures' fingerprints are fully
determined by the fixtures' own published numbers (a contiguous integer range
is its own value list), and three more are code lists small enough to write
down. Where the fixtures publish a number this pass derives, the derivation is
checked against it directly.

What this cannot check is the raw data: whether Chinook's ``track.composer``
really has 977 NULLs is a question only the rows can answer, and
``tests/fixtures/source`` — which ``README.md`` documents as holding the
Chinook SQL and the fixture generator — is not in the repository. The live
half of the acceptance lives in ``test_acceptance_chinook.py`` and skips
itself until that data is loaded.

Three things are checked here, and it is worth being clear about which is
which:

* **Fingerprint payloads** are a real end-to-end check. The value list is
  reconstructed from the fixture, but the normalization, the hashing, the
  sort order and the payload shape are the crawler's, and the fixture's hash
  set is what they have to reproduce.
* **Gate decisions** are a real check with nothing circular in them: the
  fixtures' own profile numbers go in, and which columns *have* a fingerprint
  and top-N values in the fixtures is what comes out.
* **Derived arithmetic** re-derives each published rate from the counts that
  produced it, which catches a rounding rule or a denominator drifting.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from crawler import (
    AllowList,
    Column,
    CrawlConfig,
    CrawlResult,
    Index,
    Table,
    measure,
    profile,
)
from crawler.fingerprint import Hasher
from crawler.formats import classify_column
from crawler.normalize import normalize_sample
from measure_fakes import FakeDatabase, FakeTable

#: ``distinct_count: 347; distinct_ratio: 1.0; null_rate: 0.0``
COUNTS = re.compile(
    r"distinct_count:\s*(?P<distinct>\d+)"
    r"(?:;\s*distinct_ratio:\s*(?P<ratio>[\d.]+))?"
    r"(?:;\s*null_rate:\s*(?P<null_rate>[\d.]+))?"
)
#: ``range: [1 .. 347]`` or ``range: ['A' .. 'Z']``
RANGE = re.compile(r"range:\s*\[(?P<low>.+?)\s+\.\.\s+(?P<high>.+?)\]\s*$")
#: ``length: min 2, max 95, avg 22.7``
LENGTH = re.compile(r"min\s+(\d+),\s*max\s+(\d+),\s*avg\s+([\d.]+)")
#: ``1(4%), 2(4%)`` — the rendered top-N line.
TOP_VALUE = re.compile(r"(?P<value>.+?)\((?P<percent>\d+)%\)")

#: The three code lists the fixtures hash that are not integer ranges. Their
#: values are public knowledge (they are Chinook's reference tables), and
#: writing them down is what lets the text side of C1 be checked at all.
CODE_LISTS = {
    ("MUSICSTORE_CORE", "genre", "name"): [
        "Rock", "Jazz", "Metal", "Alternative & Punk", "Rock And Roll",
        "Blues", "Latin", "Reggae", "Pop", "Soundtrack", "Bossa Nova",
        "Easy Listening", "Heavy Metal", "R&B/Soul", "Electronica/Dance",
        "World", "Hip Hop/Rap", "Science Fiction", "TV Shows",
        "Sci Fi & Fantasy", "Drama", "Comedy", "Alternative", "Classical",
        "Opera",
    ],
    ("MUSICSTORE_CORE", "media_type", "name"): [
        "MPEG audio file", "Protected AAC audio file",
        "Protected MPEG-4 video file", "Purchased AAC audio file",
        "AAC audio file",
    ],
    ("MUSICSTORE_SALES", "employee", "title"): [
        "General Manager", "Sales Manager", "Sales Support Agent",
        "IT Manager", "IT Staff",
    ],
    # Chinook's seven distinct hire dates, in the canonical temporal
    # rendering (adopted P15). test_temporal proves the renderer produces
    # these from native datetimes; this entry proves the payload machinery
    # reproduces the committed file from them.
    ("MUSICSTORE_SALES", "employee", "hire_date"): [
        "2002/8/14", "2002/5/1", "2002/4/1", "2003/5/3", "2003/10/17",
        "2004/1/2", "2004/3/4",
    ],
}


# -- reading the fixtures ---------------------------------------------------


class FixtureColumn:
    """One fixture column's published numbers, parsed out of the markdown."""

    def __init__(self, database, table_doc, column):
        self.database = database
        self.table = table_doc.table
        self.name = column.name
        self.column = column
        self.row_count = int(table_doc.frontmatter.get("row_count"))
        text = " ".join(line.render() for line in column.lines)
        counts = COUNTS.search(text)
        self.distinct_count = int(counts.group("distinct")) if counts else None
        self.distinct_ratio = (
            float(counts.group("ratio"))
            if counts and counts.group("ratio")
            else None
        )
        self.null_rate = (
            float(counts.group("null_rate"))
            if counts and counts.group("null_rate")
            else None
        )
        self.min_value = self.max_value = None
        self.min_length = self.max_length = self.avg_length = None
        for line in column.lines:
            rendered = line.render()
            found = RANGE.search(rendered)
            if found:
                self.min_value = found.group("low").strip("'")
                self.max_value = found.group("high").strip("'")
            if rendered.startswith("- [observed] length:"):
                sizes = LENGTH.search(rendered)
                if sizes:
                    self.min_length = int(sizes.group(1))
                    self.max_length = int(sizes.group(2))
                    self.avg_length = float(sizes.group(3))
        self.dense_sequence = column.find("dense_sequence") is not None
        format_value = column.value("format")
        format_match = (
            re.match(r"([a-z-]+)", format_value) if format_value else None
        )
        self.format = format_match.group(1) if format_match else None
        self.indexed = bool(
            column.find_all("index") or column.find_all("constraint")
        )
        self.sensitive = column.is_sensitive
        self.has_fingerprint = column.fingerprint is not None
        self.has_top_values = column.top_values is not None

    @property
    def key(self):
        return (self.database, self.table, self.name)

    @property
    def id(self) -> str:
        return f"{self.database}/{self.table}.{self.name}"

    @property
    def null_count(self):
        """The count the published rate was rounded from."""
        if self.null_rate is None:
            return None
        return round(self.null_rate * self.row_count)

    @property
    def non_null_count(self):
        null = self.null_count
        return None if null is None else self.row_count - null

    @property
    def integer_values(self):
        """The value list, when the fixture's own numbers determine it.

        A contiguous integer range is the one value set a profile pins down
        exactly: ``distinct_count`` equal to ``max - min + 1`` leaves no room
        for a value to be anything other than what it is.
        """
        try:
            low, high = int(self.min_value), int(self.max_value)
        except (TypeError, ValueError):
            return None
        if self.distinct_count != high - low + 1:
            return None
        return [str(v) for v in range(low, high + 1)]

    @property
    def known_values(self):
        return CODE_LISTS.get(self.key) or self.integer_values


def fixture_columns(db_bundles):
    columns = []
    for bundle in db_bundles:
        for table_doc in bundle.tables:
            for column in table_doc.columns:
                columns.append(FixtureColumn(bundle.database, table_doc, column))
    return columns


@pytest.fixture(scope="module")
def columns(db_bundles):
    return fixture_columns(db_bundles)


# -- C1: the payloads, end to end ------------------------------------------


def _fingerprint_cases(db_bundles):
    cases = []
    for bundle in db_bundles:
        for table_doc in bundle.tables:
            for column in table_doc.columns:
                if column.fingerprint is None:
                    continue
                fixture = FixtureColumn(bundle.database, table_doc, column)
                if fixture.known_values is None:
                    continue
                cases.append((bundle, fixture))
    return cases


def pytest_generate_tests(metafunc):
    if "fingerprint_case" not in metafunc.fixturenames:
        return
    from okf import read_root

    root = read_root(metafunc.config.rootpath / "tests" / "fixtures" / "okf")
    cases = _fingerprint_cases(root.databases)
    metafunc.parametrize(
        "fingerprint_case", cases, ids=[f.id for _bundle, f in cases]
    )


def test_the_reconstructible_fingerprints_are_reproduced(fingerprint_case):
    """The crawler's own normalize-and-hash against the committed payload.

    Byte for byte, in the fixtures' own key order: ``algo``, the applied
    rules, the sample cap, the count, and the sorted hash set. Only the value
    list comes from the fixture; everything the payload says about it is the
    crawler's.
    """
    bundle, fixture = fingerprint_case
    expected = json.loads(
        bundle.resolve(fixture.column.fingerprint).read_text(encoding="utf-8")
    )

    values = fixture.known_values
    # What C1 hands back: the normalized value and one raw representative.
    normalized = normalize_sample([(v.strip().upper(), v) for v in values])
    hasher = Hasher(key=None)  # fixture mode: unkeyed, per the payload's algo

    assert {
        "algo": hasher.algo,
        "normalization": list(normalized.rules),
        "sample_cap": 5000,
        "count": len(normalized.values),
        "hashes": list(hasher.hash_all(normalized.values)),
    } == expected


def test_every_fixture_fingerprint_is_unkeyed_and_says_so(db_bundles):
    """The fixtures are public and reproducible; a prod bundle is neither.
    Markdown line and payload speak the same algo vocabulary (specs/04)."""
    for bundle in db_bundles:
        for _table, _column, ref in bundle.fingerprint_refs():
            assert ref.algo == "sha256/8B"
            assert bundle.fingerprint(ref).algo == "sha256/8B"


# -- the gates --------------------------------------------------------------

#: The columns the fixtures suppress: no top-N, no fingerprint, no range.
SENSITIVE = {
    ("customer", "address"), ("customer", "postal_code"),
    ("customer", "phone"), ("customer", "fax"), ("customer", "email"),
    ("employee", "address"), ("employee", "postal_code"),
    ("employee", "phone"), ("employee", "fax"), ("employee", "email"),
    ("employee", "birth_date"),
}


def test_the_fixtures_sensitive_columns_are_exactly_the_configured_ones(columns):
    """The compliance list is an input, so the test states it rather than
    reading it back out of the thing under test."""
    marked = {(c.table, c.name) for c in columns if c.sensitive}
    assert marked == SENSITIVE


def test_the_fingerprint_gate_picks_the_fixtures_fingerprinted_columns(columns):
    """distinct_ratio > 0.5 or index/constraint membership, and not sensitive.

    Run over every column in both bundles, this reproduces the fixtures'
    fingerprint set exactly — including the low-ratio foreign keys that are
    fingerprinted because somebody indexed them (``track.genre_id`` at
    0.0071) and the high-ratio columns that are fingerprinted without any
    index at all (``invoice_line.invoice_line_id``).
    """
    predicted, actual = set(), set()
    for column in columns:
        gate = profile.fingerprint_gate(
            _fake_column(column),
            ratio=column.distinct_ratio,
            indexed=column.indexed,
            sensitive=column.sensitive,
        )
        if gate.allowed:
            predicted.add(column.id)
        if column.has_fingerprint:
            actual.add(column.id)
    assert predicted == actual


def test_the_top_n_gate_picks_the_fixtures_top_n_columns(columns):
    """distinct_count <= 30 and not sensitive, over every fixture column."""
    predicted = {
        c.id
        for c in columns
        if profile.top_n_gate(
            distinct_count=c.distinct_count, sensitive=c.sensitive
        ).allowed
    }
    assert predicted == {c.id for c in columns if c.has_top_values}


def _fake_column(fixture):
    """The crawler's Column for a fixture column — type is all the gate reads."""
    raw = (fixture.column.value("type") or "UNKNOWN").split(",")[0].strip()
    return Column(
        schema=fixture.database,
        table=fixture.table,
        name=fixture.name,
        ordinal=1,
        type=raw,
        raw_type=raw,
        nullable=True,
        scale=_scale(raw),
    )


def _scale(rendered: str):
    match = re.search(r"\((\d+),(\d+)\)", rendered)
    return int(match.group(2)) if match else None


# -- derived arithmetic -----------------------------------------------------


def test_published_rates_re_derive_from_their_counts(columns):
    """Every ``null_rate`` and ``distinct_ratio`` in both bundles, recomputed.

    The denominators differ — nulls over rows, distincts over non-nulls — and
    getting them the wrong way round produces plausible numbers, which is
    what makes this worth asserting over all 63 columns rather than one.
    """
    for column in columns:
        assert profile.null_rate(column.null_count, column.row_count) == (
            column.null_rate
        ), f"{column.id}: null_rate"
        if column.distinct_ratio is not None:
            assert profile.distinct_ratio(
                column.distinct_count, column.non_null_count
            ) == column.distinct_ratio, f"{column.id}: distinct_ratio"


def test_the_dense_sequence_flag_re_derives(columns):
    """Fill of a contiguous range, starting at 1.

    ``customer.support_rep_id`` is the case that pins the rule down: three
    distinct values filling [3 .. 5] completely, and unflagged in the
    fixtures, because a range starting at 3 is not a surrogate sequence.
    """
    for column in columns:
        flagged = profile.dense_sequence(
            _fake_column(column),
            column.distinct_count,
            column.min_value,
            column.max_value,
        )
        assert flagged == column.dense_sequence, column.id


def test_top_n_percentages_re_derive_from_the_published_line(columns):
    """The rendered ``value(pct%)`` list, recomputed from frequencies.

    Only checkable where the frequencies are recoverable — a code list whose
    values are all equally frequent, which is most of the fixtures' top-N
    lines and all of the ones over reconstructible columns.
    """
    checked = 0
    for column in columns:
        if not column.has_top_values or column.distinct_count != column.non_null_count:
            continue
        published = TOP_VALUE.findall(column.column.top_values)
        assert published, column.id
        for _value, percent in published:
            assert int(percent) == round(100 / column.non_null_count), column.id
        checked += 1
    assert checked >= 5


def test_reconstructible_columns_classify_to_their_published_format(columns):
    """The classifier (adopted P14) against every fixture column whose value
    set the repo pins — integer ranges, the code lists, the rendered hire
    dates. The expected category is read out of the fixture markdown, so a
    classifier boundary drifting from the bundles fails here by name."""
    checked = 0
    for column in columns:
        values = column.known_values
        if values is None or column.format is None:
            continue
        normalized = [str(v).strip().upper() for v in values]
        assert classify_column(normalized) == column.format, column.id
        checked += 1
    assert checked >= 15


def test_every_fixture_column_publishes_a_format(columns):
    """P14's reason for existing: the format line is on every column, the
    sensitive ones included — that is the adjudicated ruling in the ground
    truth itself."""
    for column in columns:
        assert column.format is not None, column.id
    assert any(c.format == "phone-like" for c in columns if c.sensitive)


# -- end to end, over the tables whose values are fully known ---------------

FULLY_KNOWN = ("genre", "media_type")


@pytest.mark.parametrize("table_name", FULLY_KNOWN)
def test_a_full_table_measures_to_the_fixture_numbers(db_bundles, table_name):
    """The whole pass over a table whose every value is reconstructible.

    Row count, distincts, rates, min/max, length statistics, the dense
    sequence flag, top-N with its percentages, and both fingerprint payloads
    — measured by the real crawler through the real templates, and compared
    with the committed bundle.
    """
    bundle = next(b for b in db_bundles if b.database == "MUSICSTORE_CORE")
    table_doc = bundle.table(table_name)
    fixtures = {
        c.name: FixtureColumn(bundle.database, table_doc, c)
        for c in table_doc.columns
    }
    identifier, name = f"{table_name}_id", "name"

    schema = "CORE"
    tables = [Table(schema, table_name, "BASE TABLE")]
    columns = [
        Column(schema, table_name, identifier, 1, "INT", "integer", False),
        Column(schema, table_name, name, 2, "VARCHAR(120)", "character varying",
               True, length=120),
    ]
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database=bundle.database,
        engine="postgres",
        crawl_date=date(2026, 8, 23),
        tables=tables,
        columns=columns,
        indexes=[Index(schema, table_name, f"pk_{table_name}", True, (identifier,))],
        allowlist=allow.to_obj(),
    )

    ids_ = [int(v) for v in fixtures[identifier].integer_values]
    names = CODE_LISTS[(bundle.database, table_name, name)]
    database = FakeDatabase(
        {
            (schema, table_name): FakeTable(
                columns=(identifier, name),
                rows=list(zip(ids_, names)),
                character={name},
            )
        }
    )
    config = CrawlConfig.from_obj(
        {
            "database": bundle.database,
            "engine": "postgres",
            "measure": {"fixture_mode": True},
        }
    )
    measure(
        database.connection("postgres", allow),
        config,
        result,
        today=date(2026, 8, 23),
    )

    assert result.warnings == []
    table_profile = result.table_profiles[0]
    assert table_profile.row_count == int(table_doc.frontmatter.get("row_count"))
    assert table_profile.source == table_doc.frontmatter.get("row_count_source")

    for measured in result.column_profiles:
        fixture = fixtures[measured.column]
        assert measured.distinct_count == fixture.distinct_count, measured.column
        assert measured.distinct_ratio == fixture.distinct_ratio, measured.column
        assert measured.null_rate == fixture.null_rate, measured.column
        assert measured.min_value == fixture.min_value, measured.column
        assert measured.max_value == fixture.max_value, measured.column
        assert measured.dense_sequence == fixture.dense_sequence, measured.column
        assert measured.format == fixture.format, measured.column
        if fixture.min_length is not None:
            assert measured.min_length == fixture.min_length
            assert measured.max_length == fixture.max_length
            assert measured.avg_length == fixture.avg_length

        published = TOP_VALUE.findall(fixture.column.top_values)
        measured_top = {(v.value, v.percent) for v in measured.top_values}
        for value, percent in published:
            assert (value.strip(", "), int(percent)) in measured_top

        payload = json.loads(
            bundle.resolve(fixture.column.fingerprint).read_text(encoding="utf-8")
        )
        assert measured.fingerprint.to_payload() == payload, measured.column
