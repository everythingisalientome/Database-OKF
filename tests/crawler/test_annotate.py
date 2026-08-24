"""The annotation pass — views, the LLM seam, and the sanitizer.

The contract under test is specs/01 step 6: descriptions are required at
database, table and column level; the annotator sees derived artifacts only;
every annotator output is ``[inferred:conf]`` with a confidence the
vocabulary knows; and a missing or unusable answer becomes the explicit
unknown at ``low`` — never a silent omission. The model behind the seam is
untrusted by construction, so most of these tests feed the pass a
misbehaving annotator and assert the bundle-facing result is still correct.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from crawler import (
    INSUFFICIENT,
    AnnotationError,
    Column,
    ColumnAnnotation,
    ColumnProfile,
    ColumnStats,
    Constraint,
    CrawlResult,
    DatabaseAnnotation,
    Fingerprint,
    Index,
    LLMAnnotator,
    NullAnnotator,
    Table,
    TableAnnotation,
    TableProfile,
    TableStats,
    TopValue,
    annotate,
    database_view,
    table_views,
)
from crawler.annotate import database_prompt, table_prompt

CRAWL_DATE = date(2026, 8, 24)


def sales_result() -> CrawlResult:
    """A hand-built measured crawl: one Teradata-flavoured table exercising
    PI, FK, sensitive suppression, top-N and a fingerprint."""
    schema = "SALES"
    result = CrawlResult(
        database="MUSICSTORE_SALES",
        engine="teradata",
        crawl_date=CRAWL_DATE,
        tables=[Table(schema, "invoice", "BASE TABLE")],
        columns=[
            Column(schema, "invoice", "invoice_id", 1, "INT", "I", False),
            Column(schema, "invoice", "customer_id", 2, "INT", "I", False),
            Column(schema, "invoice", "billing_state", 3, "VARCHAR(40)", "CV",
                   True, length=40),
            Column(schema, "invoice", "total", 4, "NUMERIC(10,2)", "D", False,
                   precision=10, scale=2),
        ],
        constraints=[
            Constraint("FOREIGN KEY", schema, "invoice", ("customer_id",),
                       name="fk_invoice_customer",
                       referenced_table="SALES.customer",
                       referenced_columns=("customer_id",)),
        ],
        indexes=[
            Index(schema, "invoice", None, True, ("invoice_id",),
                  primary_index=True),
            Index(schema, "invoice", "idx_customer", False, ("customer_id",)),
        ],
        measured=True,
    )
    result.table_profiles = [
        TableProfile(schema, "invoice", row_count=412, source="live"),
    ]
    result.column_profiles = [
        ColumnProfile(
            schema, "invoice", "invoice_id",
            row_count=412, non_null_count=412, null_count=0,
            distinct_count=412, distinct_ratio=1.0, null_rate=0.0,
            min_value="1", max_value="412", format="all-digits",
            dense_sequence=True,
            fingerprint=Fingerprint(schema, "invoice", "invoice_id",
                                    "sha256/8B", (), 5000, 412,
                                    ("aa" * 8, "bb" * 8)),
        ),
        ColumnProfile(
            schema, "invoice", "customer_id",
            row_count=412, non_null_count=412, null_count=0,
            distinct_count=59, distinct_ratio=0.1432, null_rate=0.0,
            min_value="1", max_value="59", format="all-digits",
        ),
        ColumnProfile(
            schema, "invoice", "billing_state",
            row_count=412, non_null_count=210, null_count=202,
            distinct_count=25, distinct_ratio=0.119, null_rate=0.4903,
            min_length=2, max_length=6, avg_length=2.2,
            format="alpha", sensitive=True,
            suppressed=("sensitive-listed",),
        ),
        ColumnProfile(
            schema, "invoice", "total",
            row_count=412, non_null_count=412, null_count=0,
            distinct_count=23, distinct_ratio=0.0558, null_rate=0.0,
            min_value="0.99", max_value="25.86", format="mixed",
            top_values=(TopValue("1.98", 112, 27), TopValue("3.96", 58, 14)),
        ),
    ]
    return result


# -- views -------------------------------------------------------------------


class TestTableViews:
    def test_the_view_carries_the_derived_artifacts(self):
        (view,) = table_views(sales_result())
        assert view.qualified == "SALES.invoice"
        assert view.row_count == 412
        assert view.row_count_source == "live"
        assert view.flags == ("pi:invoice_id",)
        assert [c.name for c in view.columns] == [
            "invoice_id", "customer_id", "billing_state", "total",
        ]
        invoice_id = view.columns[0]
        assert invoice_id.distinct_ratio == 1.0
        assert invoice_id.dense_sequence
        assert invoice_id.indexes == ("PRIMARY INDEX (Teradata PI)",)
        customer_id = view.columns[1]
        assert customer_id.constraints == (
            "FOREIGN KEY -> SALES.customer.customer_id",
        )
        assert customer_id.indexes == ("non-unique",)

    def test_views_are_sorted_and_views_of_kind_view_are_excluded(self):
        result = sales_result()
        result.tables = [
            Table("SALES", "invoice", "BASE TABLE"),
            Table("CORE", "album", "BASE TABLE"),
            Table("SALES", "summary_v", "VIEW"),
        ]
        result.columns.append(Column("CORE", "album", "album_id", 1, "INT", "I", False))
        names = [v.qualified for v in table_views(result)]
        assert names == ["CORE.album", "SALES.invoice"]

    def test_a_sensitive_column_contributes_no_value_in_any_form(self):
        """Belt and braces: even a profile that somehow carries values for a
        sensitive column must not leak them into a view (and so a prompt)."""
        result = sales_result()
        poisoned = ColumnProfile(
            "SALES", "invoice", "billing_state",
            row_count=412, distinct_count=25,
            min_value="POISON-LOW", max_value="POISON-HIGH",
            top_values=(TopValue("POISON-TOP", 10, 5),),
            sensitive=True,
        )
        result.column_profiles = [
            p for p in result.column_profiles if p.column != "billing_state"
        ] + [poisoned]
        (view,) = table_views(result)
        state = next(c for c in view.columns if c.name == "billing_state")
        assert state.min_value is None and state.max_value is None
        assert state.top_values == ()
        assert "POISON" not in json.dumps(state.to_obj())
        assert state.to_obj()["sensitive_listed"] is True
        # specs/04: the absence of a fingerprint is explained, not implied.
        assert state.fingerprint_suppressed == "sensitive"

    def test_without_profiles_the_view_falls_back_to_dictionary_stats(self):
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
        (view,) = table_views(result)
        assert view.row_count == 400
        assert view.row_count_source == "stats-estimate"
        assert view.stats_date == date(2026, 8, 20)
        invoice_id = view.columns[0]
        assert invoice_id.distinct_count == 410
        assert invoice_id.source == "stats-estimate"
        assert view.columns[1].distinct_count is None

    def test_junk_flags_ride_behind_the_pi_flag(self):
        result = sales_result()
        result.table_profiles = [
            TableProfile("SALES", "invoice", row_count=0, source="live",
                         flags=("junk-suspect", "empty"), profiled=False,
                         note="no rows"),
        ]
        (view,) = table_views(result)
        assert view.flags == ("pi:invoice_id", "junk-suspect", "empty")

    def test_an_unresolved_fk_target_is_reported_not_guessed(self):
        result = sales_result()
        result.constraints = [
            Constraint("FOREIGN KEY", "SALES", "invoice", ("customer_id",)),
        ]
        (view,) = table_views(result)
        customer_id = view.columns[1]
        assert customer_id.constraints == ("FOREIGN KEY -> (target unresolved)",)

    def test_a_primary_key_column_does_not_repeat_its_backing_index(self):
        result = sales_result()
        result.constraints.append(
            Constraint("PRIMARY KEY", "SALES", "invoice", ("invoice_id",))
        )
        result.indexes.append(
            Index("SALES", "invoice", "pk_invoice", True, ("invoice_id",))
        )
        (view,) = table_views(result)
        invoice_id = view.columns[0]
        assert invoice_id.constraints == ("PRIMARY KEY",)
        # The PI is never elided; the plain unique index is.
        assert invoice_id.indexes == ("PRIMARY INDEX (Teradata PI)",)


# -- the pass over misbehaving annotators -------------------------------------


class ScriptedAnnotator:
    """Returns exactly what a test hands it."""

    def __init__(self, table=None, database=None):
        self.table = table
        self.database = database

    def annotate_table(self, view):
        return self.table

    def annotate_database(self, view):
        return self.database


class TestAnnotatePass:
    def test_no_model_still_describes_everything_explicitly(self):
        annotations = annotate(sales_result(), NullAnnotator())
        table = annotations.for_table("SALES", "invoice")
        assert table.description == INSUFFICIENT
        assert table.purpose == INSUFFICIENT
        assert table.confidence == "low"
        assert set(table.columns) == {
            "invoice_id", "customer_id", "billing_state", "total",
        }
        assert all(
            c == ColumnAnnotation(INSUFFICIENT, "low")
            for c in table.columns.values()
        )
        assert annotations.database.summary == INSUFFICIENT
        assert annotations.database.confidence == "low"
        assert annotations.warnings == []

    def test_missing_columns_are_filled_never_skipped(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation(
                "Invoice headers", "Invoice header per purchase.", "high",
                {"total": ColumnAnnotation("Invoice total amount.", "high")},
            ),
        )
        table = annotate(sales_result(), annotator).for_table("SALES", "invoice")
        assert table.columns["total"].text == "Invoice total amount."
        assert table.columns["invoice_id"] == ColumnAnnotation(INSUFFICIENT, "low")

    def test_an_unknown_confidence_is_demoted_to_low_and_reported(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation(
                "Invoice headers", "Purpose prose.", "certain",
                {"total": ColumnAnnotation("Total.", "VERY HIGH")},
            ),
        )
        annotations = annotate(sales_result(), annotator)
        table = annotations.for_table("SALES", "invoice")
        assert table.confidence == "low"
        assert table.columns["total"].confidence == "low"
        assert any("'certain'" in w for w in annotations.warnings)
        assert any("SALES.invoice.total" in w for w in annotations.warnings)

    def test_prose_is_flattened_and_stray_tags_are_stripped(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation(
                "- [inferred:high] Invoice\nheaders",
                "Line one.\n   Line two.", "high",
                {"total": ColumnAnnotation("- [observed] Total amount.", "medium")},
            ),
        )
        table = annotate(sales_result(), annotator).for_table("SALES", "invoice")
        assert table.description == "Invoice headers"
        assert table.purpose == "Line one. Line two."
        # The model does not get to award itself [observed].
        assert table.columns["total"].text == "Total amount."

    def test_a_hallucinated_column_is_dropped_and_reported(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation(
                "Invoice headers", "Purpose.", "high",
                {"discount_pct": ColumnAnnotation("Does not exist.", "high")},
            ),
        )
        annotations = annotate(sales_result(), annotator)
        table = annotations.for_table("SALES", "invoice")
        assert "discount_pct" not in table.columns
        assert any("discount_pct" in w for w in annotations.warnings)

    def test_an_annotation_error_falls_back_and_is_reported(self):
        class Broken:
            def annotate_table(self, view):
                raise AnnotationError("the model returned interpretive dance")

            def annotate_database(self, view):
                raise AnnotationError("still dancing")

        annotations = annotate(sales_result(), Broken())
        table = annotations.for_table("SALES", "invoice")
        assert table.description == INSUFFICIENT
        assert annotations.database.summary == INSUFFICIENT
        assert len(annotations.warnings) == 2
        assert all("explicit unknown stands in" in w for w in annotations.warnings)

    def test_the_explicit_unknown_is_always_low_whatever_the_model_claims(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation(
                INSUFFICIENT, INSUFFICIENT, "high",
                {"total": ColumnAnnotation(INSUFFICIENT, "high")},
            ),
        )
        annotations = annotate(sales_result(), annotator)
        table = annotations.for_table("SALES", "invoice")
        assert table.confidence == "low"
        assert table.columns["total"].confidence == "low"
        assert annotations.warnings == []

    def test_a_description_without_a_purpose_serves_for_both(self):
        annotator = ScriptedAnnotator(
            table=TableAnnotation("Invoice headers", "", "medium", {}),
        )
        table = annotate(sales_result(), annotator).for_table("SALES", "invoice")
        assert table.purpose == "Invoice headers"

    def test_the_database_one_liner_falls_back_to_the_first_sentence(self):
        annotator = ScriptedAnnotator(
            database=DatabaseAnnotation(
                "", "Sales system of record. Holds invoicing. Contains "
                "customers.", "high",
            ),
        )
        annotations = annotate(sales_result(), annotator)
        assert annotations.database.description == "Sales system of record."
        assert annotations.database.confidence == "high"

    def test_the_database_view_is_built_from_the_annotated_tables(self):
        result = sales_result()
        views = table_views(result)
        annotation = TableAnnotation("Invoice headers", "Purpose prose.", "high", {})
        view = database_view(result, [(views[0], annotation)])
        assert view.completeness == "UNVERIFIED"
        assert view.tables == (
            ("SALES.invoice", 412, "Invoice headers", "Purpose prose."),
        )


# -- the LLM seam --------------------------------------------------------------


TABLE_RESPONSE = {
    "description": "Invoice headers: customer, date, total",
    "purpose": "Invoice header per purchase.",
    "confidence": "high",
    "columns": {
        "invoice_id": {"text": "Unique identifier.", "confidence": "high"},
        "customer_id": {"text": "Reference identifier (customer).",
                        "confidence": "medium"},
        "billing_state": {"text": "Billing state code.", "confidence": "medium"},
        "total": {"text": "Invoice total amount.", "confidence": "high"},
    },
}

DATABASE_RESPONSE = {
    "description": "Sales system of record.",
    "summary": "Sales system of record. Contains invoicing. Line items "
    "reference external catalogs. No declared foreign keys.",
    "confidence": "high",
}


class ScriptedModel:
    """A ``complete`` callable that records its prompts."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class TestLLMAnnotator:
    def test_the_prompt_carries_the_evidence_and_the_contract(self):
        model = ScriptedModel(json.dumps(TABLE_RESPONSE))
        (view,) = table_views(sales_result())
        LLMAnnotator(model).annotate_table(view)
        (prompt,) = model.prompts
        assert '"table": "SALES.invoice"' in prompt
        assert '"NUMERIC(10,2)"' in prompt
        assert '"value": "1.98"' in prompt  # top-N literals are allowed inputs
        assert "PRIMARY INDEX (Teradata PI)" in prompt
        assert INSUFFICIENT in prompt  # the explicit-unknown instruction
        assert '"confidence": "high|medium|low"' in prompt

    def test_a_sensitive_columns_values_never_reach_a_prompt(self):
        result = sales_result()
        (view,) = table_views(result)
        prompt = table_prompt(view)
        assert "sensitive_listed" in prompt
        assert "min_value" not in json.dumps(
            next(c for c in view.columns if c.name == "billing_state").to_obj()
        )

    def test_a_json_answer_becomes_a_table_annotation(self):
        model = ScriptedModel(json.dumps(TABLE_RESPONSE))
        (view,) = table_views(sales_result())
        annotation = LLMAnnotator(model).annotate_table(view)
        assert annotation.description == "Invoice headers: customer, date, total"
        assert annotation.columns["total"].text == "Invoice total amount."

    def test_a_fenced_answer_is_accepted(self):
        model = ScriptedModel("```json\n" + json.dumps(TABLE_RESPONSE) + "\n```")
        (view,) = table_views(sales_result())
        annotation = LLMAnnotator(model).annotate_table(view)
        assert annotation.confidence == "high"

    def test_prose_instead_of_json_raises_and_the_pass_recovers(self):
        (view,) = table_views(sales_result())
        with pytest.raises(AnnotationError, match="did not return JSON"):
            LLMAnnotator(ScriptedModel("Sure! This table looks like invoices."))\
                .annotate_table(view)

        model = ScriptedModel(
            "Sure! This table looks like invoices.", json.dumps(DATABASE_RESPONSE)
        )
        annotations = annotate(sales_result(), LLMAnnotator(model))
        table = annotations.for_table("SALES", "invoice")
        assert table.description == INSUFFICIENT
        assert any("SALES.invoice" in w for w in annotations.warnings)
        # The database call still ran, with the fallback one-liners in view.
        assert annotations.database.description == "Sales system of record."

    def test_a_json_list_is_refused(self):
        (view,) = table_views(sales_result())
        with pytest.raises(AnnotationError, match="not an object"):
            LLMAnnotator(ScriptedModel("[1, 2]")).annotate_table(view)

    def test_end_to_end_the_pass_sees_json_and_the_bundle_sees_prose(self):
        model = ScriptedModel(
            json.dumps(TABLE_RESPONSE), json.dumps(DATABASE_RESPONSE)
        )
        annotations = annotate(sales_result(), LLMAnnotator(model))
        table = annotations.for_table("SALES", "invoice")
        assert table.columns["billing_state"].text == "Billing state code."
        assert annotations.database.summary.startswith("Sales system of record.")
        assert annotations.warnings == []
        # The database prompt was built from the annotated tables.
        assert "Invoice headers: customer, date, total" in model.prompts[1]

    def test_the_database_prompt_carries_the_annotated_tables(self):
        result = sales_result()
        views = table_views(result)
        annotation = TableAnnotation("Invoice headers", "Purpose prose.", "high", {})
        prompt = database_prompt(database_view(result, [(views[0], annotation)]))
        assert '"table": "SALES.invoice"' in prompt
        assert '"purpose": "Purpose prose."' in prompt
        assert "3-6 sentences" in prompt
