"""Read-only UI projection: never present the full audit as incremental results."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_admin_api import auth, configured  # noqa: F401
from test_onboarding import BASE, notice, run_onboarding, setup_source, table, xlsx

from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    AuditResult,
    AuditRun,
    IncrementalRun,
    PageAssessment,
    ParseResult,
    Position,
    Resource,
    ResourceBundle,
    ResourceScan,
    ResourceVersion,
    ScanPolicy,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.onboarding.incremental_view import run_detail
from youju.onboarding.lifecycle import incremental_json, set_incremental
from youju.positions.deadlines import date_fact
from youju.positions.facts import POSITION_FIELDS, PositionFacts, TaggedField


def changed_cycle(engine, store, transport, pipeline):
    assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
    html, media = transport.pages[BASE]
    transport.pages[BASE] = (
        html.replace(b"</body>", '<a href="/new">新增招聘公告</a></body>'.encode()),
        media,
    )
    transport.pages[BASE + "new"] = (notice(table("新增岗位")), media)
    original_media = transport.pages[BASE + "positions.xlsx"][1]
    transport.pages[BASE + "positions.xlsx"] = (xlsx("附件更新岗位"), original_media)
    with Session(engine) as session, session.begin():
        for policy in session.scalars(select(ScanPolicy)):
            policy.next_scan_at = utc_now() - timedelta(seconds=1)
        set_incremental(session, "source", True)
    for _ in range(10):
        asyncio.run(pipeline.step("source", limit=50))
        with Session(engine) as session:
            run = session.scalar(select(IncrementalRun))
            if run and run.status == "complete":
                return run.id
    raise AssertionError("Synthetic incremental cycle did not complete")


def test_incremental_projection_counts_only_changed_notices_and_attachment_parents(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        run_id = changed_cycle(engine, store, transport, pipeline)
        calls = list(transport.calls)
        with Session(engine) as session:
            run = session.get(IncrementalRun, run_id)
            audit = session.get(AuditRun, run.audit_id)
            assert audit.summary["total_announcements"] == 4  # Unchanged baseline remains private.
            view = run_detail(session, run)
            assert view["summary"]["delta"] == {
                "state": "ready",
                "reason": None,
                "notice_count": 2,
                "position_count": 2,
                "new_notices": 1,
                "changed_notices": 1,
            }
            notices = view["notices"]["items"]
            assert {row["url"] for row in notices} == {BASE + "new", BASE + "two"}
            assert {row["change"] for row in notices} == {"new", "changed"}
            assert {p["facts"]["title"]["value"] for row in notices for p in row["positions"]} == {
                "新增岗位",
                "附件更新岗位",
            }
            for row in notices:
                assert row["position_count"] == len(row["positions"]) == 1
                assert not row["positions_truncated"] and not row["positions_unavailable"]
            headers = incremental_json(session, "source", history=True)
            assert "audit" not in headers["latest_run"]["summary"]
            assert "resource_changes" not in headers["latest_run"]["summary"]
            assert headers["runs"][0]["summary"]["delta"]["position_count"] == 2
            assert run_detail(session, run, page=2)["notices"]["items"] == []
        assert transport.calls == calls  # Presentation does not fetch, parse or call models.
    finally:
        engine.dispose()


def test_incremental_detail_is_admin_source_scoped_and_preserves_unknown(configured, tmp_path):  # noqa: F811
    app, client, _ = configured
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        run_id = changed_cycle(engine, store, transport, pipeline)
        app.config["DATABASE"] = str(tmp_path / "source.sqlite3")
        path = f"/api/admin/sources/source/incremental/runs/{run_id}"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth("normal")).status_code == 403
        assert client.get(path, headers=auth()).json["notices"]["total"] == 2
        assert client.get(path + "?page=0", headers=auth()).status_code == 400
        with Session(engine) as session, session.begin():
            session.add(Source(id="other", name="另一来源", base_url="https://other.example.org/"))
        assert client.get(path.replace("/source/", "/other/"), headers=auth()).status_code == 404
        with Session(engine) as session, session.begin():
            run = session.get(IncrementalRun, run_id)
            run.baseline = {"old": {"version": "v1"}}  # No attachment bundle comparison available.
        body = client.get(path, headers=auth()).json
        assert body["summary"]["delta"]["state"] == "unavailable"
        assert body["summary"]["delta"]["notice_count"] is None
        assert body["notices"]["items"] == []
    finally:
        engine.dispose()


def test_incremental_view_quarantines_newly_confirmed_semantic_mismatches(tmp_path):
    from copy import deepcopy

    from youju.models import AuditResult
    from youju.onboarding.semantic_labels import register_label

    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        run_id = changed_cycle(engine, store, transport, pipeline)
        with Session(engine) as session:
            run = session.get(IncrementalRun, run_id)
            before = run_detail(session, run)
            target = before["notices"]["items"][0]
            audit = session.scalar(
                select(AuditResult)
                .where(AuditResult.bundle_id == target["bundle_id"])
                .order_by(AuditResult.created_at.desc())
            )
            saved_output = deepcopy(audit.result)
            title = audit.result["positions"][0]["title"]["value"]
        expected = {
            "basis": "human-confirmed",
            "reference": "synthetic reviewed-unit case",
            "positions": [
                {
                    "selector": {
                        "title": title,
                        "hiring_unit": "Fixture unit missing in old output",
                    },
                    "fields": {},
                }
            ],
        }
        register_label(store, "source", target["bundle_id"], expected, reviewer="fixture-reviewer")
        with Session(engine) as session:
            run = session.get(IncrementalRun, run_id)
            view = run_detail(session, run)
            row = next(
                item
                for item in view["notices"]["items"]
                if item["bundle_id"] == target["bundle_id"]
            )
            assert row["status"] == "review" and row["positions"] == []
            assert "SEMANTIC_EXPECTATION" in row["error_codes"]
            assert (
                view["summary"]["delta"]["position_count"]
                == before["summary"]["delta"]["position_count"] - target["position_count"]
            )
            assert session.get(AuditResult, audit.id).result == saved_output
    finally:
        engine.dispose()


@pytest.fixture
def saved_projections():
    """Saved v3 rows only: no crawler, runner, authoring, model or network adapter."""
    engine = create_sqlite_engine()
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(Source(id="source", name="合成来源", base_url=BASE))
        session.flush()
        session.add(
            SourceRecipe(
                id="recipe",
                source_id="source",
                digest="a" * 64,
                body_ref="b" * 64,
                status="active",
            )
        )
        session.flush()
        session.add_all(
            [
                SourceRecipeBinding(source_id="source", active_recipe_id="recipe", generation=1),
                SourcePipeline(source_id="source", summary={"effective_classifier_key": "fixture"}),
            ]
        )
        cases = [
            ("quick-facts", "quick", "success", True, [], []),
            ("detailed-facts", "detailed", "success", True, [], []),
            (
                "quick-partial",
                "quick",
                "partial",
                True,
                [{"code": "UNKNOWN_POSITION_FIELD", "message": "headcount unknown"}],
                [],
            ),
            (
                "quick-archive",
                "quick",
                "partial",
                False,
                [{"code": "UNSUPPORTED_POSITION_LAYOUT", "message": "no supported rows"}],
                [],
            ),
            (
                "quick-review",
                "quick",
                "review",
                False,
                [],
                [{"code": "SEMANTIC_EXPECTATION", "message": "review required"}],
            ),
            (
                "quick-failed",
                "quick",
                "failed",
                False,
                [],
                [{"code": "RUNTIME_RESULT_INVALID", "message": "invalid receipt"}],
            ),
        ]
        ids = []
        for name, path, status, has_position, issues, blocking in cases:
            session.add(Resource(id=name, source_id="source", original_url=BASE + name))
            session.flush()
            session.add(
                ResourceVersion(
                    id=name,
                    resource_id=name,
                    content_sha256="c" * 64,
                    body_ref="synthetic",
                    byte_length=0,
                )
            )
            session.flush()
            session.add(
                ResourceBundle(
                    id=name,
                    resource_id=name,
                    page_version_id=name,
                    digest="d" * 64,
                    body_ref="synthetic",
                    status="complete",
                    dependencies=[],
                )
            )
            session.flush()
            session.add_all(
                [
                    ResourceScan(
                        resource_id=name, bundle_id=name, last_version_id=name, state="done"
                    ),
                    PageAssessment(
                        bundle_id=name,
                        classifier_key="fixture",
                        result={
                            "business_type": "recruitment",
                            "page_roles": ["article"],
                        },
                    ),
                ]
            )
            positions = []
            if has_position:
                fields = {key: TaggedField() for key in POSITION_FIELDS}
                fields.update(
                    title=TaggedField(name, "source"),
                    deadline_on=TaggedField("2030-12-31", "source"),
                    application_dates=TaggedField(
                        {
                            "start": None,
                            "end": date_fact(2030, 12, 31),
                        },
                        "source",
                    ),
                )
                facts = PositionFacts(fields).as_dict()
                positions.append(facts)
                session.add(
                    ParseResult(
                        id=name,
                        version_id=name,
                        parser_key="fixture",
                        status="valuable",
                        role="recruitment",
                        title=name,
                    )
                )
                session.flush()
                session.add(Position(parse_id=name, ordinal=1, facts=facts))
            session.add(
                AuditResult(
                    id=name,
                    bundle_id=name,
                    recipe_id="recipe",
                    evaluated_on="2026-10-25",
                    generation=1,
                    status=status,
                    parse_id=name if has_position else None,
                    errors=blocking,
                    result={
                        "schema_version": "position-facts-v3",
                        "status": status,
                        "positions": positions,
                        "errors": issues + blocking,
                        "coverage": {
                            "supported": has_position,
                            "complete": has_position,
                            "needs_detailed": not has_position,
                        },
                        "routing": {
                            "category": "fixed_expired" if path == "quick" else "unknown",
                            "mode": path,
                            "reason": "synthetic cost hint only",
                            "policy_version": "notice-routing-v1:old-age-days=365",
                            "evaluated_at": "2026-10-25T00:00:00+00:00",
                        },
                        "execution": {
                            "path": path,
                            "model_used": False,
                            "runtime_contract": "recipe-runtime-v3",
                            "script_issues": [],
                        },
                    },
                )
            )
            ids.append({"id": name})
        session.add(
            AuditRun(
                id="audit",
                source_id="source",
                recipe_id="recipe",
                kind="incremental",
                status="complete",
                summary={"results": ids, "evaluated_on": "2026-10-25", "generation": 1},
            )
        )
        session.flush()
        session.add(
            IncrementalRun(
                id="run",
                source_id="source",
                audit_id="audit",
                baseline={},
                status="complete",
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()


def test_saved_v3_projection_keeps_quick_facts_and_cost_only_metadata(saved_projections):
    with Session(saved_projections) as session:
        session.connection().exec_driver_sql("PRAGMA query_only = ON")
        view = run_detail(session, session.get(IncrementalRun, "run"))
        assert view["summary"]["delta"]["position_count"] == 3
        assert view["summary"]["delta"]["notice_count"] == 6
        rows = {row["bundle_id"]: row for row in view["notices"]["items"]}
        for name in ("quick-facts", "detailed-facts", "quick-partial"):
            row = rows[name]
            assert row["position_count"] == len(row["positions"]) == 1
            assert row["positions"][0]["facts"]["deadline_on"] == {
                "value": "2030-12-31",
                "tag": "source",
            }
        for row in rows.values():
            assert "quality_tier" not in row and "validity" not in row
            assert row["extraction_path"] in {"quick", "detailed"}
            assert row["routing_category"] in {"fixed_expired", "unknown"}
        assert rows["quick-partial"]["field_issue_codes"] == ["UNKNOWN_POSITION_FIELD"]
        assert rows["quick-partial"]["review_required"] is True
        assert rows["quick-partial"]["error_codes"] == []
        assert rows["quick-archive"]["coverage_issue_codes"] == ["UNSUPPORTED_POSITION_LAYOUT"]
        for name, status in (
            ("quick-archive", "partial"),
            ("quick-review", "review"),
            ("quick-failed", "failed"),
        ):
            assert rows[name]["status"] == status
            assert rows[name]["position_count"] == 0
            assert rows[name]["positions"] == []
        assert rows["quick-review"]["error_codes"] == ["SEMANTIC_EXPECTATION"]
        assert rows["quick-failed"]["error_codes"] == ["RUNTIME_RESULT_INVALID"]
        assert not session.new and not session.dirty and not session.deleted
