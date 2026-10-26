"""Focused source-wide counts: current material only, active recipe only, no execution."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from test_admin_api import auth, configured, register_source  # noqa: F401
from test_incremental_view import changed_cycle, saved_projections  # noqa: F401
from test_onboarding import setup_source

from youju.models import AuditResult, SourceRecipe, SourceRecipeBinding
from youju.onboarding.source_summary import parsing_summary


def test_summary_is_source_wide_current_and_does_not_replace_frozen_baseline(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        changed_cycle(engine, store, transport, pipeline)
        calls = list(transport.calls)
        with Session(engine) as session, session.begin():
            summary = parsing_summary(session, "source")
            assert summary["scope"] == "current_saved_notices"
            # Three baseline notices; the run changed two; the entire source now has four.
            assert summary["notice_count"] == 4
            assert summary["position_count"] == 4
            assert sum(summary["statuses"].values()) == 4
            binding = session.get(SourceRecipeBinding, "source")
            original = session.scalar(
                select(AuditResult).where(AuditResult.recipe_id == binding.active_recipe_id)
            )
            candidate = SourceRecipe(
                source_id="source", digest="c" * 64, body_ref="d" * 64, status="candidate"
            )
            session.add(candidate)
            session.flush()
            session.add_all(
                [
                    AuditResult(
                        bundle_id=original.bundle_id,
                        recipe_id=candidate.id,
                        evaluated_on="2099-01-01",
                        generation=binding.generation,
                        status="failed",
                        result={},
                    ),
                    AuditResult(
                        bundle_id=original.bundle_id,
                        recipe_id=binding.active_recipe_id,
                        evaluated_on="2099-01-01",
                        generation=binding.generation + 10,
                        status="failed",
                        result={},
                    ),
                ]
            )
            session.flush()
            after = parsing_summary(session, "source")
            for key in ("notice_count", "position_count", "statuses", "evaluated_through"):
                assert after[key] == summary[key]
        assert transport.calls == calls
    finally:
        engine.dispose()


def test_summary_endpoint_is_admin_only_and_empty_source_stays_pending(configured):  # noqa: F811
    app, client, _ = configured
    _, source = register_source(client)
    path = f"/api/admin/sources/{source['id']}/parsing-summary"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth("normal")).status_code == 403
    assert (
        client.get("/api/admin/sources/missing/parsing-summary", headers=auth()).status_code == 404
    )
    response = client.get(path, headers=auth())
    assert response.status_code == 200
    body = response.json
    assert body["state"] == "pending"
    assert body["source_id"] == source["id"]
    assert body["notice_count"] == body["position_count"] == 0
    assert body["recipe_id"] is body["evaluated_through"] is None


def test_saved_v3_summary_separates_cost_archives_from_failures_and_position_facts(
    saved_projections,  # noqa: F811
):
    with Session(saved_projections) as session:
        session.connection().exec_driver_sql("PRAGMA query_only = ON")
        summary = parsing_summary(session, "source")
        assert summary["state"] == "ready"
        assert summary["notice_count"] == 6 and summary["position_count"] == 3
        assert summary["statuses"] == {
            "success": 2,
            "partial": 1,
            "empty": 0,
            "archived": 1,
            "review": 1,
            "failed": 1,
            "pending": 0,
            "cancelled": 0,
            "associated": 0,
        }
        assert summary["extraction_paths"] == {"quick": 5, "detailed": 1}
        assert summary["routing_categories"] == {"fixed_expired": 5, "unknown": 1}
        assert summary["field_issues_by_code"] == {"UNKNOWN_POSITION_FIELD": 1}
        assert summary["coverage_issues_by_code"] == {"UNSUPPORTED_POSITION_LAYOUT": 1}
        assert summary["review_required"] == 3
        assert summary["evaluated_from"] == summary["evaluated_through"] == "2026-10-25"
        assert "validity" not in summary and "quality_tiers" not in summary
        assert not session.new and not session.dirty and not session.deleted


def test_saved_quick_only_archives_complete_without_positions_or_expiry_claims(
    saved_projections,  # noqa: F811
):
    from copy import deepcopy

    with Session(saved_projections) as session, session.begin():
        archive = deepcopy(session.get(AuditResult, "quick-archive").result)
        archive["routing"]["category"] = "undated_low_priority"
        for audit in session.scalars(select(AuditResult)):
            audit.result = deepcopy(archive)
            audit.status = "partial"
            audit.errors = []
            audit.parse_id = None
    with Session(saved_projections) as session:
        session.connection().exec_driver_sql("PRAGMA query_only = ON")
        summary = parsing_summary(session, "source")
        assert summary["state"] == "ready"
        assert summary["position_count"] == 0  # Existing historical rows are not current facts.
        assert summary["statuses"]["archived"] == summary["notice_count"] == 6
        assert summary["statuses"]["review"] == summary["statuses"]["failed"] == 0
        assert summary["routing_categories"] == {"undated_low_priority": 6}
        assert summary["extraction_paths"] == {"quick": 6}
        assert summary["evaluated_from"] == summary["evaluated_through"] == "2026-10-25"
        assert "expired" not in summary["statuses"] and "validity" not in summary


def test_quick_position_facts_still_require_current_independent_review(
    saved_projections,  # noqa: F811
):
    from copy import deepcopy

    from youju.models import IncrementalRun
    from youju.models.onboarding import SemanticCaseLabel
    from youju.onboarding.incremental_view import run_detail

    with Session(saved_projections) as session, session.begin():
        original = deepcopy(session.get(AuditResult, "quick-facts").result)
        session.add(
            SemanticCaseLabel(
                id="e" * 64,
                source_id="source",
                bundle_id="quick-facts",
                bundle_digest="d" * 64,
                expected_ref="f" * 64,
                reviewer="synthetic-reviewer",
                split="regression",
            )
        )
    with Session(saved_projections) as session:
        session.connection().exec_driver_sql("PRAGMA query_only = ON")
        summary = parsing_summary(session, "source")
        assert summary["position_count"] == 2
        assert summary["statuses"]["review"] == 2
        assert summary["statuses"]["archived"] == 1
        assert summary["review_required"] == 4
        view = run_detail(session, session.get(IncrementalRun, "run"))
        row = next(row for row in view["notices"]["items"] if row["bundle_id"] == "quick-facts")
        assert row["extraction_path"] == "quick" and row["status"] == "review"
        assert row["position_count"] == 0 and row["positions"] == []
        assert row["review_required"] is True
        assert row["error_codes"] == ["SEMANTIC_EXPECTATION"]
        assert view["summary"]["delta"]["position_count"] == 2
        assert session.get(AuditResult, "quick-facts").result == original
        assert not session.new and not session.dirty and not session.deleted
