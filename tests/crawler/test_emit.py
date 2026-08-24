"""Emission — crawl result plus annotations, rendered as an OKF bundle.

Session 4's acceptance is "the emitted bundle passes the session-1 okf
validator", and the fixture bundles are the ground truth for what the
rendering looks like. Both live here: every prepared bundle in this module is
pushed through ``okf.validate_bundle`` expecting zero errors, and the columns
of a table whose every value the repository pins (``genre``, ``media_type``)
are compared with the committed fixture files line for line.

The fixture comparison is exact: markdown line and payload speak the same
algo vocabulary (specs/04), and payload paths carry the schema segment
(adjudicated before session 5).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from okf import errors as okf_errors
from okf import read_database_bundle, read_document, validate_bundle

from crawler import (
    INSUFFICIENT,
    AllowList,
    Column,
    ColumnAnnotation,
    ColumnProfile,
    ColumnStats,
    Constraint,
    CrawlConfig,
    CrawlResult,
    Index,
    NullAnnotator,
    Reconciliation,
    Table,
    TableAnnotation,
    TableProfile,
    TableStats,
    TopValue,
    annotate,
    emit,
    measure,
    prepare,
)
from crawler import cli
from measure_fakes import FakeDatabase, FakeTable
from test_annotate import ScriptedAnnotator, sales_result
from test_fixture_parity import CODE_LISTS, FixtureColumn


def null_annotations(result):
    return annotate(result, NullAnnotator())


class MapAnnotator(ScriptedAnnotator):
    """Table annotations by (schema, table); still sanitized by the pass."""

    def __init__(self, tables, database=None):
        super().__init__(database=database)
        self.tables = tables

    def annotate_table(self, view):
        return self.tables.get((view.schema, view.table))


# -- the rendered table file --------------------------------------------------


class TestTableRendering:
    def test_frontmatter_carries_the_fixture_fields_in_the_fixture_order(self):
        result = sales_result()
        prepared = prepare(result, null_annotations(result))
        (doc,) = prepared.tables
        assert doc.frontmatter.keys() == [
            "type", "name", "description", "description_confirmed",
            "database", "engine", "row_count", "row_count_source",
            "crawl_date", "flags",
        ]
        assert doc.frontmatter["name"] == "SALES.invoice"
        assert doc.frontmatter["description"] == INSUFFICIENT
        assert doc.frontmatter["description_confirmed"] is False
        assert doc.frontmatter["row_count"] == 412
        assert doc.frontmatter["row_count_source"] == "live"
        assert doc.frontmatter["crawl_date"] == date(2026, 8, 24)
        assert doc.frontmatter["flags"] == ["pi:invoice_id"]

    def test_every_observed_line_in_the_fixtures_shape(self):
        result = sales_result()
        prepared = prepare(result, null_annotations(result))
        (doc,) = prepared.tables
        blocks = {column.name: [l.render() for l in column.lines]
                  for column in doc.columns}

        assert blocks["invoice_id"] == [
            "- [observed] type: INT, not null",
            "- [observed] distinct_count: 412; distinct_ratio: 1.0; null_rate: 0.0",
            "- [observed] format: all-digits; range: [1 .. 412]",
            "- [observed] dense_sequence: true  # contiguous surrogate range"
            " - value overlap non-distinctive",
            "- [observed] index: PRIMARY INDEX (Teradata PI)",
            "- [observed] fingerprint: sha256/8B @ fingerprints/SALES.invoice.invoice_id.json",
            "- [observed] normalization: [none]",
            f"- [inferred:low] {INSUFFICIENT}",
        ]
        assert blocks["customer_id"] == [
            "- [observed] type: INT, not null",
            "- [observed] distinct_count: 59; distinct_ratio: 0.1432; null_rate: 0.0",
            "- [observed] format: all-digits; range: [1 .. 59]",
            "- [observed] constraint: FOREIGN KEY -> SALES.customer.customer_id",
            "- [observed] index: non-unique",
            f"- [inferred:low] {INSUFFICIENT}",
        ]
        assert blocks["billing_state"] == [
            "- [observed] type: VARCHAR(40), nullable",
            "- [observed] distinct_count: 25; distinct_ratio: 0.119; null_rate: 0.4903",
            "- [observed] length: min 2, max 6, avg 2.2",
            "- [observed] format: alpha  (sensitive-listed: range suppressed)",
            "- [observed] sensitive-listed: top-N and fingerprint suppressed",
            "- [observed] fingerprint: suppressed (sensitive)",
            f"- [inferred:low] {INSUFFICIENT}",
        ]
        assert blocks["total"] == [
            "- [observed] type: NUMERIC(10,2), not null",
            "- [observed] distinct_count: 23; distinct_ratio: 0.0558; null_rate: 0.0",
            "- [observed] format: mixed; range: [0.99 .. 25.86]",
            "- [observed] top_values: 1.98(27%), 3.96(14%)",
            f"- [inferred:low] {INSUFFICIENT}",
        ]

    def test_the_annotators_words_land_as_inferred_lines(self):
        result = sales_result()
        annotations = annotate(
            result,
            MapAnnotator({
                ("SALES", "invoice"): TableAnnotation(
                    "Invoice headers: customer, date, total",
                    "Invoice header per purchase.",
                    "high",
                    {"total": ColumnAnnotation("Invoice total amount.", "high")},
                ),
            }),
        )
        prepared = prepare(result, annotations)
        (doc,) = prepared.tables
        assert doc.frontmatter["description"] == (
            "Invoice headers: customer, date, total"
        )
        assert doc.lines[0].render() == (
            "- [inferred:high] Purpose: Invoice header per purchase."
        )
        total = doc.column("total")
        assert total.lines[-1].render() == (
            "- [inferred:high] Invoice total amount."
        )

    def test_temporal_bounds_are_quoted_numeric_bounds_are_bare(self):
        result = sales_result()
        result.columns.append(
            Column("SALES", "invoice", "invoice_date", 5, "TIMESTAMP",
                   "TS", False)
        )
        result.column_profiles.append(
            ColumnProfile(
                "SALES", "invoice", "invoice_date",
                row_count=412, non_null_count=412, null_count=0,
                distinct_count=354, distinct_ratio=0.8592, null_rate=0.0,
                min_value="2021/1/1", max_value="2025/12/22", format="mixed",
            )
        )
        prepared = prepare(result, null_annotations(result))
        block = prepared.tables[0].column("invoice_date")
        assert block.value("format") == (
            "mixed; range: ['2021/1/1' .. '2025/12/22']"
        )

    def test_dictionary_sourced_numbers_say_so_on_the_line(self):
        """specs/01 step 1: profile lines record their source and date."""
        result = sales_result()
        result.measured = False
        result.table_profiles = []
        result.column_profiles = []
        result.table_stats = [
            TableStats("SALES", "invoice", 400, source="stats-estimate",
                       stats_date=date(2026, 8, 20)),
        ]
        result.column_stats = [
            ColumnStats("SALES", "invoice", "invoice_id", distinct_count=410,
                        null_rate=0.0, stats_date=date(2026, 8, 20),
                        approximate=True),
        ]
        prepared = prepare(result, null_annotations(result))
        (doc,) = prepared.tables
        assert doc.frontmatter["row_count"] == 400
        assert doc.frontmatter["row_count_source"] == "stats-estimate"
        assert doc.frontmatter["stats_date"] == date(2026, 8, 20)
        line = doc.column("invoice_id").find("distinct_count")
        assert line.render() == (
            "- [observed] distinct_count: 410; null_rate: 0.0"
            "  # source: stats-estimate 2026-08-20"
        )
        # No profile, no gate ran: no fingerprint, no top-N, no format line.
        assert doc.column("invoice_id").find("fingerprint") is None

    def test_a_table_without_any_row_count_is_emitted_honestly(self):
        result = sales_result()
        result.table_profiles = []
        result.table_stats = []
        prepared = prepare(result, null_annotations(result))
        (doc,) = prepared.tables
        assert "row_count" not in doc.frontmatter
        assert "row_count_source" not in doc.frontmatter
        assert any("no row count" in w for w in prepared.warnings)
        assert prepared.index.entries[0].description == INSUFFICIENT

    def test_top_values_cap_at_the_published_six(self):
        result = sales_result()
        many = tuple(TopValue(str(v), 10, 2) for v in range(1, 9))
        result.column_profiles = [
            p if p.column != "total" else ColumnProfile(
                "SALES", "invoice", "total",
                row_count=412, distinct_count=23, top_values=many,
            )
            for p in result.column_profiles
        ]
        prepared = prepare(result, null_annotations(result))
        line = prepared.tables[0].column("total").value("top_values")
        assert line == "1(2%), 2(2%), 3(2%), 4(2%), 5(2%), 6(2%)"


# -- the index and the bundle --------------------------------------------------


class TestIndexAndBundle:
    def test_the_index_document_in_the_fixtures_shape(self):
        result = sales_result()
        result.reconciliation = Reconciliation(
            status="COMPLETE", cataloged_tables=1, visible_tables=1,
            note="cataloged 1 base tables plus 0 in system schemas = 1 of 1 visible",
        )
        prepared = prepare(result, null_annotations(result))
        index = prepared.index
        assert index.frontmatter.keys() == [
            "type", "database", "description", "engine", "build_date",
            "completeness",
        ]
        assert index.frontmatter["completeness"] == "COMPLETE"
        assert index.frontmatter.comment("completeness").startswith(
            "reconciliation: cataloged 1"
        )
        assert index.frontmatter["build_date"] == date(2026, 8, 24)
        (summary,) = index.summary
        assert summary.render() == f"- [inferred:low] {INSUFFICIENT}"
        (entry,) = index.entries
        assert entry.render() == (
            f"- `SALES/invoice.md` — {INSUFFICIENT} (412 rows)"
        )

    def test_incompleteness_reaches_the_frontmatter(self):
        result = sales_result()
        result.reconciliation = Reconciliation(
            status="INCOMPLETE", cataloged_tables=1, expected_tables=3,
            note="cataloged 1 of 3 expected base tables",
        )
        prepared = prepare(result, null_annotations(result))
        assert prepared.index.frontmatter["completeness"] == "INCOMPLETE"

    def test_a_crawl_that_never_reconciled_is_unverified(self):
        result = sales_result()
        prepared = prepare(result, null_annotations(result))
        assert prepared.index.frontmatter["completeness"] == "UNVERIFIED"
        assert prepared.index.frontmatter.comment("completeness") == (
            "reconciliation: never ran"
        )

    def test_views_are_recorded_not_emitted(self):
        result = sales_result()
        result.tables.append(Table("SALES", "invoice_summary_v", "VIEW"))
        prepared = prepare(result, null_annotations(result))
        assert [doc.name for doc in prepared.tables] == ["SALES.invoice"]
        assert any(
            "invoice_summary_v" in w and "not emitted" in w
            for w in prepared.warnings
        )

    def test_same_table_name_in_two_schemas_gets_two_payloads(self):
        """The schema segment in the payload path (specs/04, adjudicated) is
        what keeps a multi-schema estate from sharing one file."""
        result = sales_result()
        result.tables.append(Table("ARCHIVE", "invoice", "BASE TABLE"))
        result.columns.append(
            Column("ARCHIVE", "invoice", "invoice_id", 1, "INT", "I", False)
        )
        clash = result.column_profiles[0]
        result.column_profiles.append(
            ColumnProfile(
                "ARCHIVE", "invoice", "invoice_id",
                row_count=10, distinct_count=10,
                fingerprint=clash.fingerprint.__class__(
                    "ARCHIVE", "invoice", "invoice_id", "sha256/8B", (),
                    5000, 10, ("cc" * 8,),
                ),
            )
        )
        prepared = prepare(result, null_annotations(result))
        assert sorted(f.path for f in prepared.fingerprints) == [
            "fingerprints/ARCHIVE.invoice.invoice_id.json",
            "fingerprints/SALES.invoice.invoice_id.json",
        ]
        assert not any("already taken" in w for w in prepared.warnings)

    def test_budget_and_temporal_refusals_render_the_suppression_line(self):
        """specs/04 suppression vocabulary: a gated column that could have
        had a fingerprint says why it has none; an ineligible column says
        nothing at all."""
        result = sales_result()
        result.column_profiles = [
            ColumnProfile(
                "SALES", "invoice", "invoice_id",
                row_count=412, distinct_count=412, distinct_ratio=1.0,
                suppressed=("budget",),
            ),
            ColumnProfile(
                "SALES", "invoice", "customer_id",
                row_count=412, distinct_count=59, distinct_ratio=0.1432,
                suppressed=("unparseable-temporal",),
            ),
            ColumnProfile(
                "SALES", "invoice", "billing_state",
                row_count=412, distinct_count=25, sensitive=True,
                suppressed=("sensitive-listed", "sensitive-listed"),
            ),
            ColumnProfile(
                "SALES", "invoice", "total",
                row_count=412, distinct_count=23, distinct_ratio=0.0558,
                suppressed=("distinct-gate", "cardinality-gate"),
            ),
        ]
        prepared = prepare(result, null_annotations(result))
        (doc,) = prepared.tables
        assert doc.column("invoice_id").value("fingerprint") == "suppressed (budget)"
        assert doc.column("customer_id").value("fingerprint") == (
            "suppressed (unparseable-temporal)"
        )
        assert doc.column("billing_state").value("fingerprint") == (
            "suppressed (sensitive)"
        )
        # Failed the cardinality gate: ineligible, so no line at all.
        assert doc.column("total").find("fingerprint") is None


# -- on disk --------------------------------------------------------------------


class TestEmitToDisk:
    def test_the_emitted_bundle_passes_the_validator(self, tmp_path):
        result = sales_result()
        result.reconciliation = Reconciliation(
            status="COMPLETE", cataloged_tables=1, note="all accounted for"
        )
        emission = emit(result, null_annotations(result), tmp_path)
        assert emission.root == tmp_path / "db" / "MUSICSTORE_SALES"
        bundle = read_database_bundle(emission.root)
        assert okf_errors(validate_bundle(bundle)) == []
        # Everything the null annotator wrote is queued for humans.
        assert len(bundle.needs_review) == 6  # 4 columns + purpose + summary

    def test_written_files_read_back_byte_for_byte(self, tmp_path):
        result = sales_result()
        emission = emit(result, null_annotations(result), tmp_path)
        for path in emission.documents:
            text = path.read_text(encoding="utf-8")
            assert text.endswith("\n") and not text.endswith("\n\n")
            assert read_document(path).render() == text

    def test_the_payload_lands_where_the_markdown_points(self, tmp_path):
        result = sales_result()
        emission = emit(result, null_annotations(result), tmp_path)
        (payload,) = emission.fingerprints
        assert payload == (
            emission.root / "fingerprints" / "SALES.invoice.invoice_id.json"
        )
        stored = json.loads(payload.read_text(encoding="utf-8"))
        assert stored["algo"] == "sha256/8B"
        assert stored["count"] == 412

    def test_two_emits_produce_identical_bytes(self, tmp_path):
        result = sales_result()
        first = emit(result, null_annotations(result), tmp_path / "one")
        second = emit(result, null_annotations(result), tmp_path / "two")
        for a, b in zip(
            first.documents + first.fingerprints,
            second.documents + second.fingerprints,
        ):
            assert a.read_bytes() == b.read_bytes()


# -- fixture parity ---------------------------------------------------------------

#: The fixture generator's own annotator output for the two tables whose
#: every value the repository pins — supplied here through the real pass, so
#: the whole file can be compared with the committed one.
FIXTURE_ANNOTATIONS = {
    "genre": TableAnnotation(
        "Music genre reference codes", "Small reference list of music genres.",
        "high",
        {
            "genre_id": ColumnAnnotation(
                "Unique identifier; candidate key of genre.", "high"),
            "name": ColumnAnnotation(
                "Display name of the genre record.", "high"),
        },
    ),
    "media_type": TableAnnotation(
        "Media format reference codes", "Reference list of media/file formats.",
        "high",
        {
            "media_type_id": ColumnAnnotation(
                "Unique identifier; candidate key of media_type.", "high"),
            "name": ColumnAnnotation(
                "Display name of the media_type record.", "high"),
        },
    ),
}


def measured_fixture_table(table_name: str) -> CrawlResult:
    """The real measuring pass over a fully reconstructible fixture table —
    the same construction test_fixture_parity end-to-ends, plus the PK
    constraint the fixture file publishes."""
    schema = "CORE"
    identifier, name = f"{table_name}_id", "name"
    fixture_names = CODE_LISTS[("MUSICSTORE_CORE", table_name, name)]

    tables = [Table(schema, table_name, "BASE TABLE")]
    columns = [
        Column(schema, table_name, identifier, 1, "INT", "integer", False),
        Column(schema, table_name, name, 2, "VARCHAR(120)", "character varying",
               True, length=120),
    ]
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database="MUSICSTORE_CORE",
        engine="postgres",
        crawl_date=date(2026, 8, 23),
        tables=tables,
        columns=columns,
        constraints=[
            Constraint("PRIMARY KEY", schema, table_name, (identifier,),
                       name=f"pk_{table_name}"),
        ],
        indexes=[Index(schema, table_name, f"pk_{table_name}", True,
                       (identifier,))],
        allowlist=allow.to_obj(),
        reconciliation=Reconciliation(status="COMPLETE", cataloged_tables=1),
    )
    database = FakeDatabase(
        {
            (schema, table_name): FakeTable(
                columns=(identifier, name),
                rows=list(zip(range(1, len(fixture_names) + 1), fixture_names)),
                character={name},
            )
        }
    )
    config = CrawlConfig.from_obj(
        {
            "database": "MUSICSTORE_CORE",
            "engine": "postgres",
            "measure": {"fixture_mode": True},
        }
    )
    measure(
        database.connection("postgres", allow), config, result,
        today=date(2026, 8, 23),
    )
    assert result.warnings == []
    return result


@pytest.mark.parametrize("table_name", ["genre", "media_type"])
class TestFixtureParity:
    def test_the_emitted_file_matches_the_committed_fixture(
        self, fixture_root, table_name
    ):
        """Measured by the real pass, annotated with the fixture's own words,
        rendered by this emitter — against the committed ground truth.

        Frontmatter ``engine`` differs (the fixtures simulate an ansi estate;
        this measuring run is the postgres toolset); everything else must
        match to the byte.
        """
        result = measured_fixture_table(table_name)
        annotations = annotate(
            result,
            MapAnnotator({("CORE", table_name): FIXTURE_ANNOTATIONS[table_name]}),
        )
        assert annotations.warnings == []
        prepared = prepare(result, annotations)
        (emitted,) = prepared.tables

        fixture = read_document(
            fixture_root / "db" / "MUSICSTORE_CORE" / "CORE" / f"{table_name}.md"
        )
        expected = fixture.frontmatter.as_dict()
        actual = emitted.frontmatter.as_dict()
        assert actual.pop("engine") == "postgres"
        assert expected.pop("engine") == "ansi"
        assert actual == expected

        assert [l.render() for l in emitted.lines] == [
            l.render() for l in fixture.lines
        ]
        assert [c.name for c in emitted.columns] == [
            c.name for c in fixture.columns
        ]
        for ours, theirs in zip(emitted.columns, fixture.columns):
            assert [l.render() for l in ours.lines] == [
                l.render() for l in theirs.lines
            ], ours.name

    def test_the_emitted_payloads_are_byte_identical_to_the_fixtures(
        self, fixture_root, table_name, tmp_path
    ):
        result = measured_fixture_table(table_name)
        emission = emit(result, null_annotations(result), tmp_path)
        for written in emission.fingerprints:
            fixture = (
                fixture_root / "db" / "MUSICSTORE_CORE" / "fingerprints"
                / written.name
            )
            assert written.read_bytes() == fixture.read_bytes(), written.name

    def test_the_emitted_bundle_passes_the_validator(self, table_name, tmp_path):
        result = measured_fixture_table(table_name)
        emission = emit(result, null_annotations(result), tmp_path)
        issues = validate_bundle(read_database_bundle(emission.root))
        assert okf_errors(issues) == []


# -- the command line -------------------------------------------------------------


class TestCli:
    def test_from_crawl_to_valid_bundle(self, tmp_path, capsys):
        result = measured_fixture_table("genre")
        crawl_json = tmp_path / "crawl.json"
        crawl_json.write_text(
            json.dumps(result.to_obj()), encoding="utf-8"
        )
        okf_root = tmp_path / "okf"
        assert cli.main(
            ["--from-crawl", str(crawl_json), "--emit", str(okf_root)]
        ) == 0
        err = capsys.readouterr().err
        assert "okf validator: valid" in err
        assert (okf_root / "db" / "MUSICSTORE_CORE" / "index.md").is_file()

    def test_from_crawl_without_emit_is_refused(self, tmp_path):
        crawl_json = tmp_path / "crawl.json"
        crawl_json.write_text("{}", encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--from-crawl", str(crawl_json)])
        assert excinfo.value.code == 2

    def test_the_llm_annotator_unconfigured_fails_before_annotating(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.delenv("CRAWLER_LLM_MODEL", raising=False)
        result = measured_fixture_table("genre")
        crawl_json = tmp_path / "crawl.json"
        crawl_json.write_text(json.dumps(result.to_obj()), encoding="utf-8")
        assert cli.main(
            ["--from-crawl", str(crawl_json), "--emit", str(tmp_path / "okf"),
             "--annotator", "llm"]
        ) == 1
        assert "CRAWLER_LLM_MODEL" in capsys.readouterr().err
        assert not (tmp_path / "okf").exists()

    def test_measure_needs_a_live_crawl(self, tmp_path):
        crawl_json = tmp_path / "crawl.json"
        crawl_json.write_text("{}", encoding="utf-8")
        with pytest.raises(SystemExit):
            cli.main(["--from-crawl", str(crawl_json), "--measure",
                      "--emit", str(tmp_path / "okf")])
