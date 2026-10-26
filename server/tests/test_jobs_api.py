"""Actual saved-audit projection/API checks with synthetic material and no external IO."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from threading import RLock
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_sourcekit import bundle as material_bundle

from youju import create_app
from youju.api.jobs import current_day, jobs_api
from youju.database import create_sqlite_engine, initialize_database
from youju.jobs import job_validity, public_jobs
from youju.models import (
    AuditResult,
    AuditRun,
    MaterialDocument,
    PageAssessment,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceBundle,
    ResourceScan,
    ResourceVersion,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.notices import RoutingCategory, RoutingDecision
from youju.onboarding.auditing import AUDIT_POLICY, AuditLedger
from youju.onboarding.recipes import RecipeRunner, template_candidate
from youju.onboarding.store import toolkit_key
from youju.positions import Condition, PositionFacts, TaggedField
from youju.positions.deadlines import date_fact
from youju.positions.facts import POSITION_FIELDS
from youju.positions.parsing import extract_position_draft

DAY = date(2026, 10, 14)
STAMP = datetime(2026, 10, 13, 4, tzinfo=UTC)
TEXTS = {
    "expired": "报名截止2026年10月12日。",
    "future": "报名截止2026年10月16日。",
    "today": "报名截止2026年10月14日。",
    "unknown": "欢迎应聘。",
    "open": "本招聘启事报名长期有效。",
    "filled": "报名招满为止。",
    "partial": "报名截止2026年10月16日。",
}


class DraftSandbox:
    """Replace isolated execution only; exercise the real official Flow facade."""

    async def run_script(self, files, module, function, inputs):
        assert module in {"parser", "undated_parser"} and set(files) == {f"{module}.py"}
        assert (module, function) == ("parser", "parse")
        return extract_position_draft(inputs["bundle"], inputs["config"])


def runtime_result(material, *, now_iso=None, mode="detailed"):
    now_iso = now_iso or STAMP.isoformat()
    category = RoutingCategory.UNKNOWN if mode == "detailed" else RoutingCategory.FIXED_EXPIRED
    routing = RoutingDecision(
        category, mode, "synthetic-cost-policy", "synthetic-v3", datetime.fromisoformat(now_iso)
    ).as_dict()
    return asyncio.run(
        RecipeRunner(DraftSandbox()).run(
            template_candidate(), material, now_iso=now_iso, routing=routing
        )
    )


@pytest.fixture
def saved(tmp_path, monkeypatch, request):
    path = tmp_path / "jobs.sqlite3"
    engine = create_sqlite_engine(path)
    initialize_database(engine)
    store = SimpleNamespace(engine=engine, lock=RLock())
    with Session(engine) as session, session.begin():
        session.add(
            Source(
                id="source",
                name="示例官方来源",
                base_url="https://example.org/",
                status=RegistryStatus.ACTIVE,
                verified_at=STAMP,
                authority_evidence="Synthetic official approval",
            )
        )
        session.flush()
        session.add(
            SourceRecipe(
                id="recipe",
                source_id="source",
                digest="recipe",
                body_ref="saved",
                status="active",
                validation={"toolkit_key": toolkit_key()},
            )
        )
        session.flush()
        session.add(
            SourcePipeline(
                source_id="source",
                stage="active",
                state="ready",
                activated_at=STAMP,
            )
        )
        session.add(SourceRecipeBinding(source_id="source", active_recipe_id="recipe"))
    summaries, ids = [], {}
    for number, (key, period) in enumerate(TEXTS.items(), 1):
        material = material_bundle(
            key,
            texts=[period],
            rows=[
                ["岗位名称", "人数", "专业"],
                [f"{key} 助理", "2", "计算机"],
            ],
        )
        material["page"]["version_id"] = "version-" + key
        dependencies = []
        if key == "partial":
            material["status"] = "partial"
            material["attachments"] = [
                {
                    "url": "https://example.org/missing.xlsx",
                    "format": "xlsx",
                    "status": "failed",
                    "blocks": [],
                    "tables": [],
                    "truncated": False,
                    "errors": [
                        {"code": "attachment_unavailable", "message": "Synthetic missing file"}
                    ],
                }
            ]
            dependencies = [
                {
                    "url": "https://example.org/missing.xlsx",
                    "document_id": None,
                    "error": material["attachments"][0]["errors"],
                }
            ]
        with Session(engine) as session, session.begin():
            session.add(Resource(id=key, source_id="source", original_url=material["url"]))
            session.flush()
            session.add(
                ResourceVersion(
                    id="version-" + key,
                    resource_id=key,
                    content_sha256=f"{number:064x}",
                    body_ref="saved",
                    byte_length=1,
                )
            )
            session.flush()
            session.add(
                MaterialDocument(
                    id="doc-" + key,
                    version_id="version-" + key,
                    parser_key="synthetic",
                    format="html",
                    status="ok",
                    body_ref="saved",
                )
            )
            session.add(
                ResourceBundle(
                    id=key,
                    resource_id=key,
                    page_version_id="version-" + key,
                    digest=key,
                    body_ref="saved",
                    status=material["status"],
                    dependencies=dependencies,
                )
            )
            session.flush()
            session.add(
                ResourceScan(
                    resource_id=key,
                    state="done",
                    bundle_id=key,
                    last_version_id="version-" + key,
                    last_document_id="doc-" + key,
                    last_success_at=STAMP,
                )
            )
            session.add(
                PageAssessment(
                    bundle_id=key,
                    classifier_key="synthetic",
                    result={
                        "business_type": "recruitment",
                        "page_roles": ["article"],
                        "requires_review": False,
                    },
                )
            )
        result = runtime_result(material, mode=getattr(request, "param", "detailed"))
        assert result["schema_version"] == "position-facts-v3"
        assert all(PositionFacts.from_dict(item) for item in result["positions"])
        assert "validity" not in result and "quality_tier" not in result
        summary = AuditLedger(store).record(material, "recipe", "2026-10-13", result)
        assert summary["status"] == ("partial" if key == "partial" else "success"), summary
        summaries.append(summary)
        with Session(engine) as session:
            audit = session.get(AuditResult, summary["id"])
            ids[key] = session.scalar(
                select(Position.id).where(Position.parse_id == audit.parse_id)
            )
    with Session(engine) as session, session.begin():
        session.add(
            AuditRun(
                id="admitted",
                source_id="source",
                recipe_id="recipe",
                kind="onboarding",
                status="complete",
                finished_at=STAMP,
                summary={
                    "passed": True,
                    "audit_policy": AUDIT_POLICY,
                    "evaluated_on": "2026-10-13",
                    "generation": 0,
                    "remaining": 0,
                    "results": summaries,
                    "admission": {"policy": "quarantine-v1", "blocking_reasons": []},
                },
            )
        )
        session.get(SourcePipeline, "source").summary = {"audit_id": "admitted"}
    app = create_app({"TESTING": True, "DATABASE": str(path)})
    if "jobs" not in app.blueprints:
        app.register_blueprint(jobs_api, url_prefix="/api")
    monkeypatch.setattr("youju.api.jobs.current_day", lambda stamp=None: DAY)
    monkeypatch.setattr(
        "youju.api.jobs.utc_now", lambda: datetime.fromisoformat("2026-10-14T00:00:00+08:00")
    )
    yield SimpleNamespace(engine=engine, client=app.test_client(), ids=ids, summaries=summaries)
    engine.dispose()


def assert_job_contract(row, facts):
    assert set(row) == {
        "id",
        "source_id",
        "source_name",
        "official_url",
        "bundle_id",
        "recipe_id",
        "audit_id",
        "admission_audit_id",
        "display_tags",
        "audit_evaluated_on",
        "audit_evaluated_at",
        "expiry_evaluated_on",
        "expiry_precision",
        "last_successful_check",
        "facts",
        "evidence",
        "validity",
        "review_status",
        "extraction_status",
        "extraction_path",
        "coverage_issues",
        "field_issues",
        "coverage",
        "material_status",
        "review_required",
        "repair_required",
        "warnings",
    }
    assert row["facts"] == facts == PositionFacts.from_dict(facts).as_dict()
    assert isinstance(row["field_issues"], list) and isinstance(row["coverage_issues"], list)
    assert type(row["coverage"]["complete"]) is bool
    assert "routing" not in row and "quality_tier" not in row
    assert facts["schema_version"] == "position-facts-v3"
    assert set(facts) == {*POSITION_FIELDS, "schema_version", "conditions", "condition_logic"}
    for field in POSITION_FIELDS:
        assert set(facts[field]) == {"value", "tag"}
    assert isinstance(facts["conditions"], list)
    assert "facts_origin" not in facts


def test_actual_projection_survives_tomorrow_preserves_evidence_and_no_writes(saved):
    with Session(saved.engine) as session:
        before = session.scalar(select(func.count()).select_from(AuditResult))
        snapshot = {r.id: deepcopy(r.result) for r in session.scalars(select(AuditResult))}
        rows = list(public_jobs(session, day=DAY))
        assert {r["id"] for r in rows} == {saved.ids[k] for k in TEXTS}
        row = next(r for r in rows if r["id"] == saved.ids["future"])
        position = session.get(Position, row["id"])
        audit = session.get(AuditResult, row["audit_id"])
        assert_job_contract(row, position.facts)
        assert row["evidence"] == position.evidence
        assert row["official_url"] == "https://example.org/future"
        assert row["audit_evaluated_on"] == "2026-10-13"
        assert row["audit_evaluated_at"] == audit.created_at.isoformat()
        assert row["last_successful_check"] == STAMP.isoformat()
        assert row["validity"]["current_hiring_status"] == "not_verified"
        later = list(public_jobs(session, day=date(2026, 10, 17)))
        assert {r["id"] for r in later} == set(saved.ids.values())
        assert session.scalar(select(func.count()).select_from(AuditResult)) == before
        assert {r.id: r.result for r in session.scalars(select(AuditResult))} == snapshot


def test_api_list_search_pagination_detail_and_partial_opt_in(saved):
    page = saved.client.get("/api/jobs?page_size=2").get_json()
    assert page["total"] == 7 and len(page["items"]) == 2 and page["has_next"]
    with Session(saved.engine) as session:
        for row in page["items"]:
            assert_job_contract(row, session.get(Position, row["id"]).facts)
    assert page["filters"] == {
        **{key: "all" for key in ("deadline", "degree", "staffing", "experience")},
        "education": "all",
        "employment": "all",
    }
    assert page["timezone"] == "Asia/Shanghai" and page["evaluated_on"] == DAY.isoformat()
    assert saved.client.get("/api/jobs?page=5&page_size=2").get_json()["items"] == []
    assert saved.client.get("/api/jobs?q=计算机").get_json()["total"] == 7
    assert saved.client.get("/api/jobs?q=%25").get_json()["total"] == 0
    assert saved.client.get("/api/jobs?q=_%27%20OR%201%3D1").get_json()["total"] == 0
    assert saved.client.get("/api/jobs?include_partial=true").get_json()["total"] == 7
    partial = "/api/jobs/" + saved.ids["partial"]
    assert saved.client.get(partial).status_code == 200
    assert saved.client.get(partial + "?include_partial=false").status_code == 404
    row = saved.client.get(partial + "?include_partial=true").get_json()
    with Session(saved.engine) as session:
        assert_job_contract(row, session.get(Position, row["id"]).facts)
    assert row["material_status"] == "partial" and row["warnings"]
    assert row["validity"]["verification_scope"] == "position"
    expired = saved.client.get("/api/jobs/" + saved.ids["expired"])
    assert expired.status_code == 200
    assert "expired" in expired.get_json()["display_tags"]
    assert saved.client.get("/api/jobs/unknown-id").status_code == 404
    assert saved.client.post("/api/jobs").status_code == 405
    unknown = saved.client.get("/api/jobs/" + saved.ids["unknown"]).get_json()
    assert unknown["validity"]["status"] == "unknown"
    opened = saved.client.get("/api/jobs/" + saved.ids["open"]).get_json()
    assert opened["facts"]["application_rule"]["value"] == "open_ended"
    assert opened["validity"]["status"] == "unknown"


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=1001",
        "page=1.2",
        "page_size=51",
        "page_size=0",
        "include_partial=1",
        "include_partial=yes",
        "include_partial=true&include_partial=false",
        "education=",
        "education=graduate",
        "education=Bachelor",
        "education=%20master",
        "education=master%20",
        "education=本科",
        "education=all&education=all",
        "education=bachelor&education=master",
        "employment=",
        "employment=direct",
        "employment=fixed_term",
        "employment=temporary",
        "employment=direct_employment",
        "employment=事业编制",
        "employment=Labor_dispatch",
        "employment=unknown%20",
        "employment=all&employment=all",
        "employment=unknown&employment=labor_dispatch",
        "q=" + "a" * 101,
        "q=%00",
        "page=1&page=2",
        "include_expired=true",
        "other=true",
    ],
)
def test_bounded_input_validation(saved, query):
    response = saved.client.get("/api/jobs?" + query)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "INVALID_QUERY"


@pytest.mark.parametrize(
    "mutation",
    [
        "source_paused",
        "pipeline_inactive",
        "recipe_inactive",
        "wrong_toolkit",
        "recipe_errors",
        "wrong_generation",
        "scan_pending",
        "scan_failed",
        "stale_version",
        "stale_bundle",
        "assessment_review",
        "assessment_cancellation",
        "assessment_correction",
        "audit_errors",
        "audit_failed",
        "audit_review",
        "audit_cancelled",
        "parse_cancelled",
        "no_admission",
    ],
)
def test_all_saved_candidates_display_risks_except_paused_sources(saved, mutation):
    with Session(saved.engine) as session, session.begin():
        position = session.get(Position, saved.ids["future"])
        update_facts(
            position,
            fields={"employment_type": TaggedField("labor_dispatch", "source")},
            conditions=(
                Condition(
                    "education", "education", "equals", TaggedField({"level": "bachelor"}, "source")
                ),
            ),
        )
        pipeline = session.get(SourcePipeline, "source")
        scan = session.get(ResourceScan, "future")
        audit = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "future"))
        recipe = session.get(SourceRecipe, "recipe")
        if mutation == "source_paused":
            session.get(Source, "source").status = RegistryStatus.PAUSED
        elif mutation == "pipeline_inactive":
            pipeline.stage = "audit"
        elif mutation == "recipe_inactive":
            recipe.status = "retired"
        elif mutation == "wrong_toolkit":
            recipe.validation = {"toolkit_key": "old"}
        elif mutation == "recipe_errors":
            recipe.validation = dict(recipe.validation, errors=[{"code": "INVALID"}])
        elif mutation == "wrong_generation":
            session.get(SourceRecipeBinding, "source").generation = 1
        elif mutation == "scan_pending":
            scan.state = "ready"
        elif mutation == "scan_failed":
            scan.state = "review"
        elif mutation == "stale_version":
            scan.last_version_id = "version-open"
        elif mutation == "stale_bundle":
            scan.bundle_id = "open"
        elif mutation.startswith("assessment_"):
            assessment = session.scalar(
                select(PageAssessment).where(PageAssessment.bundle_id == "future")
            )
            assessment.result = dict(
                assessment.result,
                **(
                    {"requires_review": True}
                    if mutation == "assessment_review"
                    else {"business_type": mutation.removeprefix("assessment_")}
                ),
            )
        elif mutation == "audit_errors":
            audit.errors = [{"code": "CONFLICT"}]
        elif mutation.startswith("audit_"):
            audit.status = mutation.removeprefix("audit_")
        elif mutation == "parse_cancelled":
            session.get(ParseResult, audit.parse_id).status = "cancelled"
        elif mutation == "no_admission":
            session.get(AuditRun, "admitted").status = "blocked"
    response = saved.client.get("/api/jobs/" + saved.ids["future"])
    assert response.status_code == (404 if mutation == "source_paused" else 200)
    rows = saved.client.get("/api/jobs").get_json()["items"]
    assert (saved.ids["future"] in {r["id"] for r in rows}) is (mutation != "source_paused")
    if mutation in {
        "audit_review",
        "audit_failed",
        "audit_errors",
        "no_admission",
        "assessment_review",
    }:
        assert "needs_review" in response.get_json()["display_tags"]
    if mutation in {"audit_cancelled", "parse_cancelled", "assessment_cancellation"}:
        assert "cancelled" in response.get_json()["display_tags"]
    if mutation == "audit_failed":
        assert "parse_failed" in response.get_json()["display_tags"]
    if mutation in {"stale_version", "stale_bundle"}:
        assert "material_stale" in response.get_json()["display_tags"]
    filtered = saved.client.get(
        "/api/jobs?education=bachelor&employment=labor_dispatch&include_partial=true"
    ).get_json()
    assert (saved.ids["future"] in {r["id"] for r in filtered["items"]}) is (
        mutation != "source_paused"
    )


def test_failed_refresh_keeps_last_candidates_and_adds_failure_tags(saved):
    with Session(saved.engine) as session, session.begin():
        pipeline = session.get(SourcePipeline, "source")
        pipeline.summary = {"audit_id": "in-progress", "audit": {"passed": False}}
        pipeline.state = "running"
    assert saved.client.get("/api/jobs/" + saved.ids["future"]).status_code == 200
    with Session(saved.engine) as session, session.begin():
        session.add(
            AuditResult(
                bundle_id="future",
                recipe_id="recipe",
                evaluated_on="2026-10-14",
                status="failed",
                errors=[{"code": "FAIL"}],
                result={},
            )
        )
    response = saved.client.get("/api/jobs/" + saved.ids["future"])
    assert response.status_code == 200
    assert {"parse_failed", "needs_review"} <= set(response.get_json()["display_tags"])


@pytest.mark.parametrize("state", ["ready", "running", "review", "changed", "done"])
def test_dependency_refresh_adds_tag_without_hiding_saved_facts(saved, state):
    with Session(saved.engine) as session, session.begin():
        bundle = session.get(ResourceBundle, "future")
        bundle.dependencies = [{"url": "https://example.org/open", "document_id": "doc-open"}]
        scan = session.get(ResourceScan, "open")
        if state == "changed":
            scan.last_document_id = "doc-filled"
        else:
            scan.state = state
    response = saved.client.get("/api/jobs/" + saved.ids["future"])
    assert response.status_code == 200
    assert ("material_stale" in response.get_json()["display_tags"]) is (state != "done")
    filtered = saved.client.get(
        "/api/jobs?education=unknown&employment=unknown&q=future"
    ).get_json()
    assert filtered["total"] == 1


def test_partial_missing_dependency_becoming_pending_keeps_candidate(saved):
    with Session(saved.engine) as session, session.begin():
        session.add(
            Resource(
                id="missing", source_id="source", original_url="https://example.org/missing.xlsx"
            )
        )
        session.flush()
        session.add(ResourceScan(resource_id="missing", state="ready"))
    assert (
        saved.client.get("/api/jobs/" + saved.ids["partial"] + "?include_partial=true").status_code
        == 200
    )
    filtered = saved.client.get(
        "/api/jobs?education=unknown&employment=unknown&q=partial&include_partial=true"
    ).get_json()
    assert filtered["total"] == 1
    assert "material_incomplete" in filtered["items"][0]["display_tags"]


def test_shanghai_boundary_and_uncertainty_are_not_reinterpreted():
    assert current_day(datetime(2026, 10, 13, 16, tzinfo=UTC)) == DAY
    assert current_day(datetime(2026, 10, 13, 15, 59, tzinfo=UTC)) == date(2026, 10, 13)
    material = material_bundle(texts=["报名截止2026年10月12日。"], rows=[["岗位名称"], ["助理"]])
    original = runtime_result(material)
    facts = PositionFacts.from_dict(original["positions"][0])
    assert job_validity(facts, DAY)["status"] == "expired"
    for year_status in ("inferred", "unknown"):
        data = extract_position_draft(material)
        data["notice_facts"]["dates"]["end"] = date_fact(
            2026 if year_status == "inferred" else None, 10, 12, inferred=year_status == "inferred"
        )
        from youju.positions.normalization import normalize_extraction

        uncertain = PositionFacts.from_dict(normalize_extraction(data)["positions"][0])
        snapshot = uncertain.as_dict()
        assert job_validity(uncertain, DAY)["status"] == "unknown"
        assert uncertain.as_dict() == snapshot
    assert "validity" not in original


def test_database_unavailable_does_not_create_a_live_database(tmp_path):
    path = tmp_path / "absent.sqlite3"
    app = create_app({"TESTING": True, "DATABASE": str(path)})
    if "jobs" not in app.blueprints:
        app.register_blueprint(jobs_api, url_prefix="/api")
    assert app.test_client().get("/api/jobs").status_code == 503
    assert not path.exists()


@pytest.mark.parametrize("tag", ["source", "ai_inferred", "unknown"])
@pytest.mark.parametrize("has_evidence", [False, True])
def test_only_explicit_job_deadline_adds_expiry_tag_independent_of_route_and_locators(
    saved, tag, has_evidence
):
    with Session(saved.engine) as session, session.begin():
        row = session.get(Position, saved.ids["unknown"])
        update_facts(
            row,
            fields={
                "deadline_on": TaggedField("2026-10-12", tag)
                if tag != "unknown"
                else TaggedField(),
                "application_dates": TaggedField(
                    {"start": None, "end": date_fact(2026, 10, 12, inferred=tag == "ai_inferred")},
                    tag,
                )
                if tag != "unknown"
                else TaggedField(),
            },
        )
        row.evidence = dict(
            row.evidence,
            deadline_on=[
                {
                    "id": "synthetic-position-deadline",
                    "text": "报名截止2026年10月12日。",
                    "locator": "Synthetic per-position deadline cell",
                }
            ]
            if has_evidence
            else [],
        )
    # Only a source-tagged position deadline excludes this row, never the whole
    # uncertain notice. Inferred/unknown dates cannot hide a possibly active job;
    # optional locators neither promote inference nor gate source-tagged facts.
    with Session(saved.engine) as session:
        rows = {r["id"] for r in public_jobs(session, day=DAY)}
        assert saved.ids["unknown"] in rows
        row = next(public_jobs(session, day=DAY, job_id=saved.ids["unknown"]))
        assert ("expired" in row["display_tags"]) is (tag == "source")
        audit = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "unknown"))
        assert "validity" not in audit.result
        assert audit.result["routing"]["category"] == "unknown"
    assert saved.client.get("/api/jobs/" + saved.ids["unknown"]).status_code == 200


def update_facts(position, *, fields=None, conditions=None, operator="all"):
    """Replace the JSON value so SQLAlchemy persists validated v3 fixture mutations."""
    current = PositionFacts.from_dict(position.facts)
    logic = current.condition_logic
    if conditions is not None:
        logic = (
            {"operator": operator, "items": [{"condition_id": item.id} for item in conditions]}
            if conditions
            else None
        )
    position.facts = PositionFacts(
        dict(current.fields) | (fields or {}),
        current.conditions if conditions is None else conditions,
        logic,
    ).as_dict()


@pytest.fixture
def filtered_saved(saved):
    # Synthetic tagged facts test the saved read projection, not source parsing
    # or admission eligibility. Degree and education remain distinct conditions.
    fields = {
        "expired": (("本科", "学士学位"), "labor_dispatch"),
        "future": (("本科", "学士学位"), "labor_dispatch"),
        "today": (("硕士学历", "硕\u3000士学位"), None),
        "unknown": ((), "labor_dispatch"),
        "open": (("博士学历",), None),
        "filled": ((), None),
        "partial": (("本科", "硕士学历", "硕 士学位"), "labor_dispatch"),
    }
    with Session(saved.engine) as session, session.begin():
        for key, (literals, employment) in fields.items():
            row = session.get(Position, saved.ids[key])
            conditions = tuple(
                Condition(
                    f"qualification-{index}",
                    "degree" if "学位" in literal else "education",
                    "equals",
                    TaggedField(
                        {
                            "level": "doctorate"
                            if "博士" in literal
                            else "master"
                            if "硕" in literal
                            else "bachelor"
                        },
                        "source",
                    ),
                )
                for index, literal in enumerate(literals)
            )
            update_facts(
                row,
                fields={
                    "employment_type": TaggedField(employment, "source")
                    if employment
                    else TaggedField(),
                },
                conditions=conditions,
                operator="any" if key == "partial" else "all",
            )
            row.evidence = {
                "conditions": [
                    {"id": "qualification-" + key, "text": literal, "locator": "学历/学位单元格"}
                    for literal in literals
                ],
                "employment_type": [
                    {"id": "employment-" + key, "text": "劳务派遣", "locator": "用工形式单元格"}
                ]
                if employment
                else [],
            }
        row = session.get(Position, saved.ids["unknown"])
        update_facts(
            row,
            fields={
                "title": TaggedField("unknown 博士教师", "source"),
                "requirements_text": TaggedField("招收博士，负责本科教学与硕士教学。", "source"),
            },
        )
    return saved


@pytest.mark.parametrize(
    ("education", "expected", "with_partial"),
    [
        ("all", {"expired", "future", "today", "unknown", "open", "filled"}, {"partial"}),
        ("bachelor", {"expired", "future"}, {"partial"}),
        ("master", {"today"}, {"partial"}),
        ("doctorate", {"open"}, set()),
        ("unknown", {"unknown", "filled"}, set()),
    ],
)
def test_explicit_education_facets_independent_of_partial_scope(
    filtered_saved, education, expected, with_partial
):
    saved = filtered_saved
    for partial in (False, True):
        response = saved.client.get(
            "/api/jobs",
            query_string={"education": education, "include_partial": str(partial).lower()},
        )
        assert response.status_code == 200
        page = response.get_json()
        keys = expected | (with_partial if partial else set())
        assert page["filters"] == {
            **{key: "all" for key in ("deadline", "degree", "staffing", "experience")},
            "education": education,
            "employment": "all",
        }
        assert page["include_partial"] is partial
        assert page["total"] == len(keys)
        assert {row["id"] for row in page["items"]} == {saved.ids[key] for key in keys}
        for row in page["items"]:
            with Session(saved.engine) as session:
                assert row["evidence"] == session.get(Position, row["id"]).evidence
            assert row["validity"]["current_hiring_status"] == "not_verified"
            if row["id"] == saved.ids["partial"]:
                assert row["material_status"] == "partial" and row["warnings"]
                assert row["validity"]["verification_scope"] == "position"


@pytest.mark.parametrize(
    ("employment", "expected"),
    [
        ("all", {"expired", "future", "today", "unknown", "open", "filled", "partial"}),
        ("labor_dispatch", {"expired", "future", "unknown", "partial"}),
        ("unknown", {"today", "open", "filled"}),
    ],
)
def test_employment_is_exact_saved_enum_not_inferred_direct_employment(
    filtered_saved, employment, expected
):
    saved = filtered_saved
    with Session(saved.engine) as session, session.begin():
        # This claim in general requirements must not reclassify the saved enum.
        update_facts(
            session.get(Position, saved.ids["today"]),
            fields={"requirements_text": TaggedField("劳务派遣；事业单位招聘。", "source")},
        )
    page = saved.client.get(
        "/api/jobs", query_string={"employment": employment, "include_partial": "true"}
    ).get_json()
    assert page["filters"] == {
        **{key: "all" for key in ("deadline", "degree", "staffing", "experience")},
        "education": "all",
        "employment": employment,
    }
    assert page["total"] == len(expected)
    assert {row["id"] for row in page["items"]} == {saved.ids[key] for key in expected}
    if employment != "all":
        assert {row["facts"]["employment_type"]["value"] or "unknown" for row in page["items"]} == {
            employment
        }


def test_filters_search_partial_and_pagination_are_conjunctive(filtered_saved):
    saved = filtered_saved
    query = {
        "education": "bachelor",
        "employment": "labor_dispatch",
        "q": "计算机",
        "include_partial": "false",
    }
    page = saved.client.get("/api/jobs", query_string=query).get_json()
    assert page["total"] == 2 and {r["id"] for r in page["items"]} == {
        saved.ids["future"],
        saved.ids["expired"],
    }
    query.update(include_partial="true", page_size=1)
    pages = [
        saved.client.get("/api/jobs", query_string=dict(query, page=number)).get_json()
        for number in (1, 2, 3)
    ]
    assert [p["total"] for p in pages] == [3, 3, 3]
    assert [p["has_next"] for p in pages] == [True, True, False]
    assert {row["id"] for p in pages for row in p["items"]} == {
        saved.ids["expired"],
        saved.ids["future"],
        saved.ids["partial"],
    }
    assert len(pages[2]["items"]) == 1
    for overrides in (
        {"q": "partial"},
        {"employment": "unknown"},
        {"q": "博士"},
        {"q": "%"},
    ):
        result = saved.client.get(
            "/api/jobs", query_string=dict(query, include_partial="false", **overrides)
        ).get_json()
        assert result["total"] == 0 and result["items"] == [] and not result["has_next"]
    result = saved.client.get(
        "/api/jobs", query_string=dict(query, education="master", q="partial")
    ).get_json()
    assert result["total"] == 1 and result["items"][0]["id"] == saved.ids["partial"]
    # Neither title nor teaching/general requirements create an education facet.
    for education, total in (("doctorate", 0), ("master", 0), ("bachelor", 0), ("unknown", 1)):
        page = saved.client.get(
            "/api/jobs", query_string={"education": education, "q": "博士教师"}
        ).get_json()
        assert page["total"] == total


@pytest.mark.parametrize(
    ("kind", "levels", "matches"),
    [
        ("education", ["bachelor"], {"bachelor"}),
        ("degree", ["bachelor"], {"unknown"}),
        ("degree", ["master"], {"unknown"}),
        ("education", ["master"], {"master"}),
        ("degree", ["doctorate"], {"unknown"}),
        ("education", ["doctorate"], {"doctorate"}),
        ("education", ["bachelor", "master", "doctorate"], {"bachelor", "master", "doctorate"}),
        ("education", ["postgraduate"], set()),
        ("education", ["associate"], set()),
        ("education", ["unrestricted"], set()),
        ("education", [], {"unknown"}),
        ("degree", [], {"unknown"}),
        ("education", [None], {"unknown"}),
        ("degree", [None], {"unknown"}),
    ],
)
def test_typed_degree_is_not_educational_attainment_or_rank_inference(saved, kind, levels, matches):
    conditions = tuple(
        Condition(
            f"qualification-{index}",
            kind,
            "equals",
            TaggedField({"level": level}, "source") if level else TaggedField(),
        )
        for index, level in enumerate(levels)
    )
    with Session(saved.engine) as session, session.begin():
        update_facts(
            session.get(Position, saved.ids["future"]), conditions=conditions, operator="any"
        )
    for education in ("bachelor", "master", "doctorate", "unknown"):
        page = saved.client.get(
            "/api/jobs", query_string={"education": education, "q": "future"}
        ).get_json()
        assert page["total"] == int(education in matches)
        if page["items"]:
            assert page["items"][0]["facts"]["conditions"] == [
                condition.as_dict() for condition in conditions
            ]


@pytest.mark.parametrize(
    "query",
    [
        "education=all",
        "education=master",
        "employment=all",
        "employment=unknown",
        "q=future",
        "page=1",
        "page_size=20",
        "include_partial=true&education=all",
        "include_partial=true&include_partial=true",
    ],
)
def test_detail_rejects_list_only_filters_and_duplicate_arguments(saved, query):
    response = saved.client.get("/api/jobs/" + saved.ids["future"] + "?" + query)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "INVALID_QUERY"


@pytest.mark.parametrize("filters", [{"education": "graduate"}, {"employment": "direct"}])
def test_projection_rejects_unsupported_filters(saved, filters):
    with (
        Session(saved.engine) as session,
        pytest.raises(ValueError, match="Invalid public job filter"),
    ):
        list(public_jobs(session, day=DAY, **filters))


@pytest.mark.parametrize(
    ("employment", "tag"),
    [
        ("public_institution_staff", "source"),
        ("personnel_agency", "source"),
        ("labor_dispatch", "source"),
        ("labor_contract", "source"),
        ("other", "source"),
        ("personnel_agency", "ai_inferred"),
        (None, "unknown"),
    ],
)
def test_expanded_employment_filters_preserve_tagged_saved_identity(saved, employment, tag):
    with Session(saved.engine) as session, session.begin():
        update_facts(
            session.get(Position, saved.ids["future"]),
            fields={
                "employment_type": TaggedField(employment, tag)
                if employment != "public_institution_staff"
                else TaggedField(),
                "staffing_status": TaggedField("established", tag)
                if employment == "public_institution_staff"
                else TaggedField(),
            },
        )
    for selected in (
        "all",
        "public_institution_staff",
        "personnel_agency",
        "labor_dispatch",
        "labor_contract",
        "other",
        "unknown",
    ):
        response = saved.client.get(
            "/api/jobs", query_string={"employment": selected, "q": "future"}
        )
        assert response.status_code == 200
        page = response.get_json()
        assert page["filters"] == {
            **{key: "all" for key in ("deadline", "degree", "staffing", "experience")},
            "education": "all",
            "employment": selected,
        }
        assert page["total"] == int(selected in {"all", employment or "unknown"})
        if page["items"]:
            facts = page["items"][0]["facts"]
            key = (
                "staffing_status" if employment == "public_institution_staff" else "employment_type"
            )
            assert facts[key] == {
                "value": "established" if employment == "public_institution_staff" else employment,
                "tag": tag,
            }
            if employment == "public_institution_staff":
                assert facts["employment_type"]["tag"] == "unknown"


@pytest.mark.parametrize(
    ("education_value", "degree_value", "matches"),
    [
        ("bachelor", None, {"bachelor"}),
        (None, "master", {"unknown"}),
        (None, None, {"unknown"}),
        ("postgraduate", None, set()),
    ],
)
def test_unknown_degree_does_not_erase_education_or_create_a_requirement(
    saved, education_value, degree_value, matches
):
    conditions = tuple(
        Condition(
            kind,
            kind,
            "equals",
            TaggedField({"level": value}, "source") if value else TaggedField(),
        )
        for kind, value in (("education", education_value), ("degree", degree_value))
    )
    with Session(saved.engine) as session, session.begin():
        update_facts(session.get(Position, saved.ids["future"]), conditions=conditions)
    for selected in ("bachelor", "master", "doctorate", "unknown"):
        response = saved.client.get(
            "/api/jobs", query_string={"education": selected, "q": "future"}
        )
        assert response.status_code == 200
        page = response.get_json()
        assert page["total"] == int(selected in matches)
        if page["items"]:
            assert page["items"][0]["facts"]["conditions"] == [
                item.as_dict() for item in conditions
            ]


def test_optional_saved_locators_survive_public_v3_projection(saved):
    evidence = {
        "title": [{"id": "future:r1:c0", "text": "future 助理", "locator": "table 0 row 1 col 0"}]
    }
    with Session(saved.engine) as session, session.begin():
        position = session.get(Position, saved.ids["future"])
        position.evidence = evidence
        facts = deepcopy(position.facts)
    response = saved.client.get("/api/jobs/" + saved.ids["future"])
    assert response.status_code == 200
    row = response.get_json()
    assert_job_contract(row, facts)
    assert row["evidence"] == evidence
    assert "evidence" not in row["facts"] and "facts_origin" not in row


@pytest.mark.parametrize("saved", ["quick", "detailed"], indirect=True)
def test_cost_path_cannot_gate_identical_valid_job_persistence_or_visibility(saved):
    with Session(saved.engine) as session:
        positions = list(session.scalars(select(Position)))
        assert len(positions) == len(TEXTS)
        future = session.get(Position, saved.ids["future"])
        assert future.deadline_on == date(2026, 10, 16)
        assert future.application_start_on is None
        row = next(public_jobs(session, day=DAY, job_id=future.id))
        assert row["facts"] == future.facts
        assert "routing" not in row and "quality_tier" not in row
        assert row["validity"]["verification_scope"] == "position"
        assert row["validity"]["status"] == "not_expired"
        later = list(public_jobs(session, day=date(2026, 10, 17), job_id=future.id))
        assert len(later) == 1 and "expired" in later[0]["display_tags"]
    assert saved.client.get("/api/jobs/" + saved.ids["future"]).status_code == 200
    expired = saved.client.get("/api/jobs/" + saved.ids["expired"])
    assert expired.status_code == 200
    assert "expired" in expired.get_json()["display_tags"]


def test_new_facets_use_literal_facts_and_share_pagination_total(saved):
    with Session(saved.engine) as session, session.begin():
        row = session.get(Position, saved.ids["future"])
        update_facts(
            row,
            fields={"staffing_status": TaggedField("established", "source")},
            conditions=(
                Condition(
                    "education",
                    "education",
                    "equals",
                    TaggedField({"level": "associate"}, "source"),
                ),
                Condition(
                    "degree", "degree", "equals", TaggedField({"level": "bachelor"}, "source")
                ),
                Condition(
                    "experience",
                    "work_experience",
                    "equals",
                    TaggedField({"minimum_years": 0, "kind": None}, "source"),
                ),
            ),
        )
    query = {
        "q": "future",
        "education": "associate",
        "degree": "bachelor",
        "staffing": "established",
        "experience": "not_required",
        "deadline": "not_expired",
        "page_size": 1,
    }
    result = saved.client.get("/api/jobs", query_string=query).get_json()
    assert result["total"] == len(result["items"]) == 1 and not result["has_next"]
    assert result["filters"]["staffing"] == "established"
    for key, value in {
        "degree": "master",
        "staffing": "non_established",
        "experience": "experienced",
        "deadline": "expired",
    }.items():
        page = saved.client.get("/api/jobs", query_string=query | {key: value}).get_json()
        assert page["total"] == 0 and page["items"] == []
    # Missing experience is unknown, not an explicit zero-year requirement.
    for selected, count in (("unknown", 1), ("not_required", 0), ("experienced", 0)):
        page = saved.client.get(
            "/api/jobs", query_string={"q": "unknown", "experience": selected}
        ).get_json()
        assert page["total"] == count


def test_deadline_facets_use_position_clock_and_do_not_claim_current_hiring(saved):
    # This is a typed-fact filter test, not a claim about generic prose parsing.
    with Session(saved.engine) as session, session.begin():
        update_facts(
            session.get(Position, saved.ids["filled"]),
            fields={"application_rule": TaggedField("until_filled", "source")},
        )
    expected = {
        "all": 7,
        "expired": 1,
        "not_expired": 3,
        "unknown": 3,
        "open_ended": 1,
        "until_filled": 1,
    }
    for value, total in expected.items():
        page = saved.client.get(
            "/api/jobs", query_string={"deadline": value, "page_size": 1}
        ).get_json()
        assert page["total"] == total and page["filters"]["deadline"] == value, (value, page)
        assert page["has_next"] == (total > 1)
        assert all(
            row["validity"]["current_hiring_status"] == "not_verified" for row in page["items"]
        )
    for key in ("deadline", "degree", "staffing", "experience"):
        assert saved.client.get("/api/jobs", query_string={key: "invented"}).status_code == 400
        assert (
            saved.client.get(
                f"/api/jobs/{saved.ids['future']}", query_string={key: "all"}
            ).status_code
            == 400
        )
