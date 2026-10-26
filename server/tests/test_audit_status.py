"""Focused status reassessment checks; synthetic inputs, no model/network calls."""

import asyncio
import copy
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_onboarding import BASE, fixture_pages, run_onboarding, setup_source

from youju.models import AuditResult, FetchAttempt, Position, Resource, ResourceScan, SourcePipeline
from youju.models.base import utc_now
from youju.onboarding.auditing import AuditLedger, audit_dimensions, current_source_positions
from youju.positions.contracts import CONTRACT
from youju.positions.extraction import validate_output


def activated_partial_source(tmp_path):
    pages = fixture_pages()
    body, mime = pages[BASE + "two"]
    # Keep the valid spreadsheet, but add an unfetched ancillary source. The page
    # and readable rows still have complete literal evidence.
    body = body.replace(
        b"</main>", b'<a href="https://other.example.org/form.docx">Form</a></main>'
    )
    pages[BASE + "two"] = body, mime
    engine, store, transport, pipeline = setup_source(tmp_path, pages=pages)
    assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
    return engine, store, transport, pipeline


def test_partial_status_opt_in_query_and_no_cost_immutable_reassessment(tmp_path):
    engine, store, transport, pipeline = activated_partial_source(tmp_path)
    try:
        with Session(engine) as session:
            row = session.scalar(select(AuditResult).where(AuditResult.status == "partial"))
            assert row and row.parse_id and not row.errors
            dims = audit_dimensions(row, "partial")
            assert dims["extraction_status"] == "parsed" and not dims["review_required"]
            assert dims["material_status"] == "partial" and dims["warnings"]
            old = (row.id, copy.deepcopy(row.result), copy.deepcopy(row.errors))
            attempts = session.scalar(select(func.count()).select_from(FetchAttempt))
        assert len(current_source_positions(store, "source")) == 1
        available = current_source_positions(store, "source", include_partial=True)
        assert len(available) == 2
        partial = next(x for x in available if x["material_status"] == "partial")
        assert not partial["review_required"] and partial["warnings"]
        assert partial["validity"]["verification_scope"] == "position"
        assert (
            len(
                current_source_positions(
                    store, "source", include_partial=True, include_expired=True
                )
            )
            == 3
        )
        calls = list(transport.calls)
        old_generation = store.status("source")["binding"]["generation"]
        result = AuditLedger(store).reassess("source")
        assert result["binding"]["generation"] > old_generation
        assert result["summary"]["audit"]["statuses"] == {"success": 2, "partial": 1}
        assert result["summary"]["audit"]["admission"]["review_required"] == 0
        assert result["summary"]["audit"]["model_calls"] == []
        assert transport.calls == calls
        with Session(engine) as session:
            row = session.get(AuditResult, old[0])
            assert (row.id, row.result, row.errors) == old
            assert session.scalar(select(func.count()).select_from(FetchAttempt)) == attempts
        assert len(current_source_positions(store, "source", include_partial=True)) == 2
        # Opt-in never reads a stale partial result while its previously missing
        # or available dependency is being reacquired.
        with Session(engine) as session, session.begin():
            resource = session.scalar(
                select(Resource).where(Resource.normalized_url == BASE + "positions.xlsx")
            )
            session.get(ResourceScan, resource.id).state = "ready"
        assert len(current_source_positions(store, "source", include_partial=True)) == 1
        with pytest.raises(ValueError, match="pending"):
            AuditLedger(store).reassess("source")
    finally:
        engine.dispose()


def test_warning_names_cannot_hide_bad_fields_and_failed_placeholder_is_not_an_omission(tmp_path):
    engine, store, _, pipeline = activated_partial_source(tmp_path)
    try:
        bundle = next(b for b in store.bundles("source") if b["status"] == "partial")
        with Session(engine) as session:
            row = session.scalar(select(AuditResult).where(AuditResult.bundle_id == bundle["id"]))
            result, recipe, day = copy.deepcopy(row.result), row.recipe_id, row.evaluated_on
        valid = validate_output(bundle, result)
        assert not valid
        result["errors"].append(
            {"code": "INCOMPLETE_MATERIAL", "message": "Forged excuse for unsupported content"}
        )
        reviewed = AuditLedger(store).record(bundle, recipe, day, result, generation=10)
        assert reviewed["status"] == "review" and reviewed["review_required"]
        result["positions"][0]["title"] = {"value": "实验助理", "tag": "unknown"}
        invalid = AuditLedger(store).record(bundle, recipe, day, result, generation=11)
        assert invalid["status"] == "review"
        assert any(e["code"] == "POSITION_V3_SCHEMA" for e in invalid["errors"])
        primary = {
            "code": "POSITION_V3_SCHEMA",
            "message": "Title tag contradicts its known value",
            "failure_code": "RESULT_INVALID",
            "scope": "resource",
        }
        empty = {
            "schema_version": CONTRACT,
            "status": "failed",
            "positions": [],
            "errors": [primary],
            "coverage": result["coverage"],
            "routing": result["routing"],
            "execution": result["execution"],
        }
        failed = AuditLedger(store).record(bundle, recipe, day, empty, [primary], generation=12)
        assert failed["status"] == "failed" and failed["repair_required"]
        assert not any(e["code"] == "SUSPICIOUS_ZERO_POSITIONS" for e in failed["errors"])
        original_empty = dict(primary, code="SUSPICIOUS_ZERO_POSITIONS")
        failed = AuditLedger(store).record(
            bundle, recipe, day, empty, [original_empty], generation=13
        )
        assert any(e["code"] == "SUSPICIOUS_ZERO_POSITIONS" for e in failed["errors"])
        with Session(engine) as session:
            for audit in session.scalars(select(AuditResult).where(AuditResult.generation >= 11)):
                assert audit.parse_id is None
            # Structurally valid review facts remain visible with risk tags;
            # malformed rows and empty failures never invent positions.
            quarantined = session.scalar(select(AuditResult).where(AuditResult.generation == 10))
            assert quarantined.status == "review" and quarantined.errors and quarantined.parse_id
            assert session.scalar(select(func.count()).select_from(Position)) == 4
            from youju.jobs import public_jobs

            published = list(
                public_jobs(
                    session,
                    day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    include_partial=True,
                )
            )
            assert len(published) == 3
            reviewed_rows = [item for item in published if item["audit_id"] == quarantined.id]
            assert reviewed_rows and all(
                "needs_review" in item["display_tags"] for item in reviewed_rows
            )
            assert session.get(SourcePipeline, "source").stage == "active"
    finally:
        engine.dispose()
