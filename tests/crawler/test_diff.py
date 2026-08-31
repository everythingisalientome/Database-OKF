"""The refresh diff — change report, stale manifest, and the CLI refresh flow.

Every bundle here is produced by the real emitter over a hand-built crawl
result, then read back through the okf package — the diff is exercised over
exactly the bytes a scheduled refresh would see, not over synthetic
snapshots. The base result is :func:`test_annotate.sales_result`, one
Teradata-flavoured table with a PI, an FK, a sensitive column, top-N and a
fingerprint; each test mutates a copy and compares.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from okf import read_database_bundle

from crawler import (
    MANIFEST_FORMAT,
    Column,
    ColumnProfile,
    ConfigError,
    CrawlConfig,
    CrawlerError,
    DiffTolerance,
    Fingerprint,
    NullAnnotator,
    Reconciliation,
    Table,
    TableProfile,
    annotate,
    diff_snapshots,
    emit,
    snapshot,
)
from crawler import cli
from test_annotate import sales_result

CRAWL_DATE = date(2026, 8, 23)


def measured_result():
    result = sales_result()
    result.reconciliation = Reconciliation("COMPLETE", 1, expected_tables=1)
    return result


def emitted_snapshot(result, root):
    annotations = annotate(result, NullAnnotator())
    emit(result, annotations, root)
    bundle = read_database_bundle(Path(root) / "db" / result.database)
    return snapshot(bundle)


def diff_results(previous, current, tmp_path, tolerance=None):
    prev = emitted_snapshot(previous, tmp_path / "prev")
    cur = emitted_snapshot(current, tmp_path / "cur")
    return diff_snapshots(prev, cur, tolerance)


# -- nothing changed ---------------------------------------------------------


def test_identical_bundles_diff_empty(tmp_path):
    diff = diff_results(measured_result(), measured_result(), tmp_path)
    assert not diff.has_changes
    assert diff.invalidated == []
    assert diff.unchanged == 1
    assert diff.ignored_shifts == 0
    assert diff.to_manifest()["invalidated"] == []


def test_a_source_stamp_comment_is_not_drift(tmp_path):
    """The counts line carries ``# source: stats <date>`` when the numbers
    came from the dictionary. Provenance of the measurement is not movement
    of the data."""
    current = measured_result()
    current.column_profiles[1] = replace(
        current.column_profiles[1], source="stats", stats_date=CRAWL_DATE
    )
    diff = diff_results(measured_result(), current, tmp_path)
    assert not diff.has_changes


# -- schema changes ----------------------------------------------------------


def test_a_column_added_invalidates_the_table(tmp_path):
    current = measured_result()
    current.columns.append(
        Column("SALES", "invoice", "discount", 5, "NUMERIC(4,2)", "D", True,
               precision=4, scale=2)
    )
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert change.table == "SALES.invoice"
    assert "column-added: discount" in change.schema_reasons
    (entry,) = diff.to_manifest()["invalidated"]
    assert entry["artifact"] == "db/MUSICSTORE_SALES/SALES/invoice.md"
    assert entry["change"] == "table-changed"
    assert "column-added: discount" in entry["reasons"]


def test_a_column_dropped_invalidates_the_table(tmp_path):
    current = measured_result()
    current.columns = [c for c in current.columns if c.name != "total"]
    current.column_profiles = [
        p for p in current.column_profiles if p.column != "total"
    ]
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert "column-dropped: total" in change.schema_reasons


def test_a_type_change_invalidates_the_table(tmp_path):
    current = measured_result()
    current.columns[3] = replace(current.columns[3], type="VARCHAR(20)")
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert any(r.startswith("type-changed: total") for r in change.schema_reasons)


def test_tables_added_and_dropped(tmp_path):
    current = measured_result()
    current.tables = [Table("SALES", "refunds", "BASE TABLE")]
    current.columns = [
        Column("SALES", "refunds", "refund_id", 1, "INT", "I", False)
    ]
    current.constraints, current.indexes = [], []
    current.table_profiles = [
        TableProfile("SALES", "refunds", row_count=3, source="live")
    ]
    current.column_profiles = []
    diff = diff_results(measured_result(), current, tmp_path)
    assert [c.table for c in diff.added] == ["SALES.refunds"]
    assert [c.table for c in diff.dropped] == ["SALES.invoice"]
    manifest = diff.to_manifest()
    assert {e["change"] for e in manifest["invalidated"]} == {
        "table-added", "table-dropped",
    }


# -- profile shifts and the tolerance ---------------------------------------


def test_a_row_count_shift_beyond_tolerance_invalidates(tmp_path):
    current = measured_result()
    current.table_profiles[0] = replace(
        current.table_profiles[0], row_count=500
    )
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert "row_count-shifted (412 -> 500)" in change.profile_reasons


def test_a_row_count_shift_inside_tolerance_is_counted_not_flagged(tmp_path):
    current = measured_result()
    current.table_profiles[0] = replace(
        current.table_profiles[0], row_count=430
    )
    diff = diff_results(measured_result(), current, tmp_path)
    assert not diff.has_changes
    assert diff.unchanged == 1
    assert diff.ignored_shifts == 1
    assert diff.to_manifest()["ignored_shifts"] == 1


def test_rates_compare_absolutely(tmp_path):
    inside, beyond = measured_result(), measured_result()
    inside.column_profiles[1] = replace(
        inside.column_profiles[1], distinct_ratio=0.16
    )
    beyond.column_profiles[1] = replace(
        beyond.column_profiles[1], distinct_ratio=0.31
    )
    assert not diff_results(measured_result(), inside, tmp_path).has_changes
    diff = diff_results(measured_result(), beyond, tmp_path)
    (change,) = diff.changed
    assert "distinct_ratio-shifted: customer_id (0.1432 -> 0.31)" in (
        change.profile_reasons
    )


def test_the_tolerance_is_configurable(tmp_path):
    current = measured_result()
    current.table_profiles[0] = replace(
        current.table_profiles[0], row_count=430
    )
    strict = DiffTolerance(row_count=0.01)
    diff = diff_results(measured_result(), current, tmp_path, strict)
    (change,) = diff.changed
    assert "row_count-shifted (412 -> 430)" in change.profile_reasons


def test_a_changed_fingerprint_payload_is_drift(tmp_path):
    """The markdown line is identical — same algo, same path. Only the
    payload bytes moved, which is exactly the overlap evidence step 2
    scores, so it has to invalidate."""
    current = measured_result()
    profile = current.column_profiles[0]
    current.column_profiles[0] = replace(
        profile,
        fingerprint=replace(profile.fingerprint, hashes=("cc" * 8, "dd" * 8)),
    )
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert change.profile_reasons == ["fingerprint-changed: invoice_id"]


def test_a_top_values_change_is_drift(tmp_path):
    current = measured_result()
    profile = current.column_profiles[3]
    current.column_profiles[3] = replace(
        profile, top_values=(replace(profile.top_values[0], percent=31),)
        + profile.top_values[1:]
    )
    diff = diff_results(measured_result(), current, tmp_path)
    (change,) = diff.changed
    assert "top_values-changed: total" in change.profile_reasons


# -- annotation changes are informational ------------------------------------


def test_annotation_changes_invalidate_nothing(tmp_path):
    root = tmp_path / "cur"
    prev = emitted_snapshot(measured_result(), tmp_path / "prev")
    emitted_snapshot(measured_result(), root)
    table_file = root / "db" / "MUSICSTORE_SALES" / "SALES" / "invoice.md"
    text = table_file.read_text(encoding="utf-8")
    text = text.replace(
        "- [inferred:low] insufficient evidence to describe",
        "- [inferred:high] Invoice line total in local currency.",
        1,
    )
    table_file.write_text(text, encoding="utf-8")
    cur = snapshot(read_database_bundle(root / "db" / "MUSICSTORE_SALES"))
    diff = diff_snapshots(prev, cur)
    assert diff.invalidated == []
    (change,) = diff.annotation_only
    assert any(
        r.startswith("annotation-changed") for r in change.annotation_reasons
    )
    assert diff.to_manifest()["invalidated"] == []


# -- [confirmed] content is never silently dropped ---------------------------


def test_confirmed_lines_travel_with_a_schema_change(tmp_path):
    confirmed = (
        "- [confirmed] by: preet, date: 2026-08-20, source: process-docs — "
        "grand total including tax."
    )
    prev_root = tmp_path / "prev"
    emitted_snapshot(measured_result(), prev_root)
    table_file = prev_root / "db" / "MUSICSTORE_SALES" / "SALES" / "invoice.md"
    text = table_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    lines.insert(lines.index("### total") + 1, confirmed)
    table_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    prev = snapshot(read_database_bundle(prev_root / "db" / "MUSICSTORE_SALES"))

    current = measured_result()
    current.columns.append(
        Column("SALES", "invoice", "discount", 5, "INT", "I", True)
    )
    cur = emitted_snapshot(current, tmp_path / "cur")
    diff = diff_snapshots(prev, cur)
    (change,) = diff.changed
    assert change.confirmed_lines == [confirmed]
    (entry,) = diff.to_manifest()["invalidated"]
    assert entry["confirmed_lines"] == [confirmed]
    assert "stale-confirmed" in diff.render_report()


# -- completeness propagates -------------------------------------------------


def test_an_incomplete_current_bundle_carries_a_caution(tmp_path):
    current = measured_result()
    current.reconciliation = Reconciliation(
        "INCOMPLETE", 1, expected_tables=2, note="cataloged 1 of 2"
    )
    diff = diff_results(measured_result(), current, tmp_path)
    manifest = diff.to_manifest()
    assert manifest["completeness"] == "INCOMPLETE"
    assert manifest["current"]["completeness"] == "INCOMPLETE"
    assert any("grant gaps" in note for note in manifest["notes"])
    assert "INCOMPLETE" in diff.render_report()


# -- guard rails -------------------------------------------------------------


def test_baseline_run_invalidates_nothing(tmp_path):
    cur = emitted_snapshot(measured_result(), tmp_path)
    diff = diff_snapshots(None, cur)
    assert diff.baseline
    assert diff.invalidated == []
    assert diff.unchanged == 1
    manifest = diff.to_manifest()
    assert manifest["baseline"] is True
    assert manifest["previous"]["build_date"] is None
    assert "baseline" in diff.render_report()


def test_different_databases_are_refused(tmp_path):
    other = measured_result()
    other.database = "MUSICSTORE_CORE"
    prev = emitted_snapshot(measured_result(), tmp_path / "a")
    cur = emitted_snapshot(other, tmp_path / "b")
    with pytest.raises(CrawlerError, match="different databases"):
        diff_snapshots(prev, cur)


def test_the_manifest_is_self_describing(tmp_path):
    diff = diff_results(measured_result(), measured_result(), tmp_path)
    manifest = diff.to_manifest()
    assert manifest["format"] == MANIFEST_FORMAT
    assert "stale" in manifest["contract"]
    assert manifest["database"] == "MUSICSTORE_SALES"
    assert manifest["tolerance"] == DiffTolerance().to_obj()
    assert manifest["previous"]["build_date"] == "2026-08-24"
    assert manifest["current"]["build_date"] == "2026-08-24"
    json.dumps(manifest)  # must serialize as-is


# -- configuration -----------------------------------------------------------


def test_tolerances_load_from_config():
    config = CrawlConfig.from_obj(
        {
            "database": "X",
            "engine": "postgres",
            "refresh": {"row_count": 0.2, "rate": 0.01},
        }
    )
    assert config.refresh == DiffTolerance(row_count=0.2, rate=0.01)
    assert CrawlConfig.from_obj(config.to_obj()).refresh == config.refresh


def test_unknown_refresh_keys_are_an_error():
    with pytest.raises(ConfigError, match="unknown refresh keys: tolerance"):
        CrawlConfig.from_obj(
            {"database": "X", "engine": "postgres",
             "refresh": {"tolerance": 0.5}}
        )


def test_negative_tolerances_are_refused():
    with pytest.raises(ConfigError, match="cannot be negative"):
        DiffTolerance(rate=-0.1)


def test_an_inline_connection_password_is_refused():
    """Secrets are never config values. A config carrying one is refused
    before it can be committed, not silently passed to the driver."""
    with pytest.raises(ConfigError, match="password_env"):
        CrawlConfig.from_obj(
            {"database": "X", "engine": "postgres",
             "connection": {"host": "h", "Password": "hunter2"}}
        )


# -- the CLI refresh flow ----------------------------------------------------


def crawl_json(result, path) -> str:
    path.write_text(json.dumps(result.to_obj()), encoding="utf-8")
    return str(path)


class TestCliRefresh:
    def test_diff_needs_emit(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli.main(
                ["--from-crawl",
                 crawl_json(measured_result(), tmp_path / "c.json"), "--diff"]
            )

    def test_first_refresh_is_a_baseline(self, tmp_path, capsys):
        okf_root = tmp_path / "okf"
        rc = cli.main(
            ["--from-crawl",
             crawl_json(measured_result(), tmp_path / "c.json"),
             "--emit", str(okf_root), "--diff"]
        )
        assert rc == 0
        manifest = json.loads(
            (okf_root / "refresh" / "MUSICSTORE_SALES" / "stale.json")
            .read_text(encoding="utf-8")
        )
        assert manifest["baseline"] is True
        assert manifest["invalidated"] == []
        assert "baseline" in capsys.readouterr().err

    def test_a_refresh_flags_the_changed_table_and_cleans_orphans(
        self, tmp_path, capsys
    ):
        okf_root = tmp_path / "okf"
        base = ["--emit", str(okf_root), "--diff"]
        assert cli.main(
            ["--from-crawl",
             crawl_json(measured_result(), tmp_path / "c1.json"), *base]
        ) == 0
        capsys.readouterr()

        # The simulated schema change: invoice_id dropped (its payload file
        # becomes an orphan), a new column added.
        current = measured_result()
        current.columns = [
            c for c in current.columns if c.name != "invoice_id"
        ]
        current.columns.append(
            Column("SALES", "invoice", "discount", 5, "INT", "I", True)
        )
        current.column_profiles = [
            p for p in current.column_profiles if p.column != "invoice_id"
        ]
        current.indexes = [
            i for i in current.indexes if "invoice_id" not in i.columns
        ]
        assert cli.main(
            ["--from-crawl", crawl_json(current, tmp_path / "c2.json"), *base]
        ) == 0

        refresh_dir = okf_root / "refresh" / "MUSICSTORE_SALES"
        manifest = json.loads(
            (refresh_dir / "stale.json").read_text(encoding="utf-8")
        )
        (entry,) = manifest["invalidated"]
        assert entry["table"] == "SALES.invoice"
        assert "column-dropped: invoice_id" in entry["reasons"]
        assert "column-added: discount" in entry["reasons"]
        assert manifest["orphans_removed"] == [
            "fingerprints/SALES.invoice.invoice_id.json"
        ]
        orphan = (
            okf_root / "db" / "MUSICSTORE_SALES" / "fingerprints"
            / "SALES.invoice.invoice_id.json"
        )
        assert not orphan.exists()

        report = (refresh_dir / "report.md").read_text(encoding="utf-8")
        assert "column-dropped: invoice_id" in report
        assert "orphaned fingerprint payload removed" in report

        err = capsys.readouterr().err
        assert "stale: SALES.invoice" in err
        assert "okf validator: valid" in err
