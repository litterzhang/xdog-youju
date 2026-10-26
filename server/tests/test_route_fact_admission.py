"""In-memory v3 admission/public-job checks; no network, model, or real source IO."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from youju.database import initialize_database
from youju.jobs import EMPLOYMENT_FILTERS, job_validity, public_jobs
from youju.models import (
    AuditResult,
    AuditRun,
    MaterialDocument,
    OnboardingResult,
    PageAssessment,
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
from youju.models.onboarding import AnnouncementChangeReview
from youju.notices import decide_route
from youju.onboarding import auditing
from youju.onboarding import pipeline as pipeline_module
from youju.onboarding.auditing import (
    AuditLedger,
    admission_quality,
    capture_diagnostics,
    current_source_positions,
    split_audit_issues,
)
from youju.onboarding.pipeline import Pipeline
from youju.onboarding.recipes import RecipeRunner, template_candidate
from youju.onboarding.store import digest, json_bytes, toolkit_key
from youju.positions import Condition, PositionFacts, TaggedField
from youju.positions.deadlines import date_fact
from youju.positions.drafts import ExtractionDraft
from youju.positions.facts import EMPLOYMENT_TYPES, POSITION_FIELDS
from youju.positions.normalization import normalize_extraction
from youju.positions.parsing import extract_position_draft

DAY = date(2026, 10, 24)
STAMP = datetime(2026, 10, 24, 4, tzinfo=UTC)
ASSESSMENT = {"business_type": "recruitment", "page_roles": ["article"], "requires_review": False}


class MemoryBlobs:
    root = Path("/synthetic-not-used")

    def __init__(self):
        self.data = {}

    def put(self, body):
        key = digest(body.decode())
        self.data[key] = body
        return key

    def read(self, key):
        return self.data[key]


class MemoryStore:
    def __init__(self, engine):
        self.engine, self.lock, self.blobs = engine, RLock(), MemoryBlobs()
        self.materials = {}

    def bundles(self, source_id, **kwargs):
        assert source_id == "source"
        return deepcopy(list(self.materials.values()))

    def load_bundle(self, bundle_id, **kwargs):
        return deepcopy(self.materials[bundle_id])

    def counts(self, source_id):
        return {"done": len(self.materials), "ready": 0, "running": 0}

    def approved(self, session, source_id):
        assert session.get(Source, source_id).status == RegistryStatus.ACTIVE

    def status(self, source_id):
        with self.lock, Session(self.engine) as session:
            row = session.get(SourcePipeline, source_id)
            binding = session.get(SourceRecipeBinding, source_id)
            return {
                "stage": row.stage,
                "state": row.state,
                "summary": deepcopy(row.summary),
                "binding": {"generation": binding.generation},
            }

    def add_notice(self, key="notice", *, texts=("报名请提交材料。",), partial=False):
        material = {
            "id": key,
            "resource_id": key,
            "source_id": "source",
            "url": f"https://example.org/{key}",
            "status": "partial" if partial else "complete",
            "assessment": deepcopy(ASSESSMENT),
            "errors": [],
            "attachments": [],
            "page": {
                "version_id": "v-" + key,
                "format": "html",
                "status": "ok",
                "title": "合成招聘公告",
                "blocks": [
                    {"id": f"{key}:{i}", "text": text, "locator": f"p:{i}"}
                    for i, text in enumerate(texts)
                ],
                "tables": [],
                "links": [],
                "errors": [],
                "truncated": False,
            },
        }
        dependencies = []
        if partial:
            error = {"code": "ATTACHMENT_MISSING", "message": "Synthetic unavailable annex"}
            material["errors"] = [error]
            dependencies = [
                {"url": "https://example.org/missing", "document_id": None, "error": [error]}
            ]
        self.materials[key] = material
        with self.lock, Session(self.engine) as session, session.begin():
            session.add(Resource(id=key, source_id="source", original_url=material["url"]))
            session.flush()
            session.add(
                ResourceVersion(
                    id="v-" + key,
                    resource_id=key,
                    content_sha256=digest(key),
                    body_ref="saved",
                    byte_length=1,
                )
            )
            session.flush()
            session.add(
                MaterialDocument(
                    id="d-" + key,
                    version_id="v-" + key,
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
                    page_version_id="v-" + key,
                    digest=digest(material),
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
                    last_version_id="v-" + key,
                    last_document_id="d-" + key,
                    last_success_at=STAMP,
                )
            )
            session.add(
                PageAssessment(
                    bundle_id=key, classifier_key="synthetic", result=deepcopy(ASSESSMENT)
                )
            )
        return material

    def admit(self, summaries):
        with self.lock, Session(self.engine) as session, session.begin():
            binding = session.get(SourceRecipeBinding, "source")
            summary = {
                "passed": True,
                "audit_policy": auditing.AUDIT_POLICY,
                "evaluated_on": DAY.isoformat(),
                "generation": binding.generation,
                "remaining": 0,
                "classifier_key": "synthetic",
                "results": summaries,
                "admission": {
                    "policy": "quarantine-v1",
                    "blocking_reasons": [],
                    "source_errors_by_code": {},
                },
            }
            session.add(
                AuditRun(
                    source_id="source",
                    recipe_id="recipe",
                    kind="onboarding",
                    status="complete",
                    finished_at=STAMP,
                    summary=summary,
                )
            )


@pytest.fixture
def store(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    initialize_database(engine)
    value = MemoryStore(engine)
    candidate_ref = value.blobs.put(json_bytes(template_candidate()))
    with Session(engine) as session, session.begin():
        session.add(
            Source(
                id="source",
                name="合成官方来源",
                base_url="https://example.org/",
                status=RegistryStatus.ACTIVE,
                authority_evidence="Synthetic official approval",
                verified_at=STAMP,
            )
        )
        session.flush()
        session.add(
            SourceRecipe(
                id="recipe",
                source_id="source",
                digest="synthetic",
                body_ref=candidate_ref,
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
                summary={"classification": {"classifier_key": "synthetic"}},
            )
        )
        session.add(
            SourceRecipeBinding(source_id="source", active_recipe_id="recipe", generation=0)
        )
    monkeypatch.setattr(auditing, "utc_now", lambda: STAMP)
    monkeypatch.setattr(pipeline_module, "utc_now", lambda: STAMP)
    yield value
    engine.dispose()


def facts(
    title="合成助理",
    *,
    endpoint=None,
    conditions=(),
    rule=None,
    start=None,
    employment=None,
    staffing=None,
):
    fields = {name: TaggedField() for name in POSITION_FIELDS}
    fields["title"] = TaggedField(title, "source")
    if endpoint or start:
        outer_tag = (
            "ai_inferred"
            if any(p and p["tag"] == "ai_inferred" for p in (start, endpoint))
            else "source"
        )
        fields["application_dates"] = TaggedField({"start": start, "end": endpoint}, outer_tag)
        if endpoint and endpoint["date"]:
            fields["deadline_on"] = TaggedField(endpoint["date"], endpoint["tag"])
        if start and start["date"]:
            fields["application_start_on"] = TaggedField(start["date"], start["tag"])
    if employment:
        fields["employment_type"] = TaggedField(employment, "source")
    if staffing:
        fields["staffing_status"] = TaggedField(staffing, "source")
    if rule:
        fields["application_rule"] = TaggedField(rule, "source")
    logic = (
        {"operator": "all", "items": [{"condition_id": c.id} for c in conditions]}
        if conditions
        else None
    )
    return PositionFacts(fields, tuple(conditions), logic)


def output(material, positions=(), *, status=None, complete=True, errors=()):
    routing = decide_route(material, material.get("assessment"), now=STAMP).as_dict()
    return {
        "schema_version": "position-facts-v3",
        "status": status or ("success" if positions else "review"),
        "positions": [p.as_dict() for p in positions],
        "errors": list(errors),
        "coverage": {"supported": True, "complete": complete, "needs_detailed": not complete},
        "routing": routing,
        "execution": {
            "path": routing["mode"],
            "model_used": False,
            "runtime_contract": "recipe-runtime-v3",
            "parser": "undated_parser.py"
            if routing["category"].startswith("undated_")
            else "parser.py",
            "script_issues": [],
        },
    }


def record(store, material, result, generation=0):
    return AuditLedger(store).record(
        material, "recipe", DAY.isoformat(), result, generation=generation
    )


def visible(store, **kwargs):
    with Session(store.engine) as session:
        return list(public_jobs(session, day=DAY, **kwargs))


def count_positions(store):
    with Session(store.engine) as session:
        return session.scalar(select(func.count()).select_from(Position))


def test_detailed_worker_facts_are_stored_losslessly_with_structured_dates(store):
    material = store.add_notice()
    condition = Condition(
        "education", "education", "at_least", TaggedField({"level": "bachelor"}, "source")
    )
    position = facts(endpoint=date_fact(2026, 11, 1, clock="14:00"), conditions=[condition])
    result = output(material, [position])
    original = deepcopy(result)
    summary = record(store, material, result)
    assert result == original
    assert summary["status"] == "success" and summary["extraction_path"] == "detailed"
    assert summary["routing_category"] == "undated_high_priority"
    assert not ({"quality_tier", "validity", "coarse_eligible", "disposition"} & summary.keys())
    with Session(store.engine) as session:
        saved = session.scalar(select(Position))
        assert saved.facts == position.as_dict()
        assert saved.deadline_on == date(2026, 11, 1)
        audit = session.get(AuditResult, summary["id"])
        assert (
            audit.result["coverage"] == result["coverage"]
            and audit.result["execution"] == result["execution"]
        )
    store.admit([summary])
    row = visible(store)[0]
    assert row["facts"] == position.as_dict()
    assert row["validity"]["verification_scope"] == "position"
    assert not ({"routing", "routing_category", "quality_tier"} & row.keys())


@pytest.mark.parametrize("with_rows", [False, True])
@pytest.mark.parametrize("text", ["报名截止2014年6月30日", "发布日期：2014年6月30日"])
def test_quick_archive_is_inventory_coverage_not_expiry_or_public_facts(store, with_rows, text):
    material = store.add_notice(texts=[text])
    result = output(
        material,
        [facts()] if with_rows else [],
        complete=False,
        status="partial" if with_rows else "review",
    )
    summary = record(store, material, result)
    assert summary["status"] == "partial" and summary["errors"] == []
    assert summary["extraction_status"] == ("parsed" if with_rows else "archived")
    assert not summary["review_required"]
    assert admission_quality([summary], 1) == (
        ([summary], [], False) if with_rows else ([], [summary], True)
    )
    assert count_positions(store) == int(with_rows)
    store.admit([summary])
    assert len(visible(store, include_partial=True)) == int(with_rows)
    assert len(visible(store)) == int(with_rows)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "failed"},
        {"errors": [{"code": "SEMANTIC_FAILURE"}]},
        {"runtime_contract": None},
        {"schema_version": "position-facts-v2"},
        {"extraction_path": "detailed", "position_count": 0},
        {"routing_category": "unknown"},
    ],
)
def test_archive_admission_never_excuses_failures_or_missing_receipts(change):
    row = {
        "status": "partial",
        "errors": [],
        "position_count": 0,
        "schema_version": "position-facts-v3",
        "runtime_contract": "recipe-runtime-v3",
        "extraction_path": "quick",
        "routing_category": "undated_low_priority",
    }
    assert admission_quality([row], 1)[2]
    assert not admission_quality([{**row, **change}], 1)[2]
    assert not admission_quality([row], 2)[2]
    assert not admission_quality([], 0)[2]


def test_acquisition_warnings_remain_separate_and_partial_jobs_are_retained(store):
    material = store.add_notice(partial=True)
    result = output(material, [facts()])
    summary = record(store, material, result)
    assert summary["status"] == "partial" and not summary["errors"]
    assert "ATTACHMENT_MISSING" in {e["code"] for e in summary["warnings"]}
    assert count_positions(store) == 1
    store.admit([summary])
    assert not visible(store, include_partial=False)
    assert len(visible(store, include_partial=True)) == 1


def test_partial_coverage_without_acquisition_warnings_is_not_erased(store):
    material = store.add_notice()
    summary = record(store, material, output(material, [facts()], status="partial", complete=False))
    assert summary["status"] == "partial" and not summary["errors"]
    assert summary["coverage"]["needs_detailed"] and summary["warnings"] == []
    assert count_positions(store) == 1
    store.admit([summary])
    assert len(visible(store, include_partial=True)) == 1


def test_field_conflict_keeps_known_rows_and_unknown_dates_public_with_partial_capture_opt_in(
    store,
):
    material = store.add_notice(partial=True)
    issue = {"code": "APPLICATION_DATE_CONFLICT", "message": "Synthetic conflicting endpoints"}
    summary = record(
        store,
        material,
        output(material, [facts()], status="partial", complete=False, errors=[issue]),
    )
    assert summary["status"] == "partial" and summary["errors"] == []
    assert summary["field_issues"] == [issue]
    assert summary["warnings"] and issue not in summary["warnings"]
    assert count_positions(store) == 1
    store.admit([summary])
    assert not visible(store, include_partial=False)
    assert len(visible(store, include_partial=True)) == 1


def test_warning_code_lookalike_is_still_a_semantic_error(store):
    material = store.add_notice(partial=True)
    issue = {"code": "ATTACHMENT_MISSING", "message": "Different untrusted semantic assertion"}
    summary = record(store, material, output(material, [facts()], status="partial", errors=[issue]))
    assert issue in summary["errors"] and issue not in summary["warnings"]


@pytest.mark.parametrize(
    "change", ["wrong_path", "quick_model", "missing_coverage", "unknown_category"]
)
def test_invalid_runtime_or_domain_contract_does_not_admit_rows(store, change):
    material = store.add_notice(texts=["报名截止2014年6月30日"])
    result = output(material, [facts()])
    if change == "wrong_path":
        result["execution"]["path"] = "detailed"
    elif change == "quick_model":
        result["execution"]["model_used"] = True
    elif change == "missing_coverage":
        result.pop("coverage")
    else:
        result["routing"]["category"] = "cancelled"
    summary = record(store, material, result)
    assert summary["status"] == "review" and summary["errors"]
    assert not admission_quality([summary], 1)[2]
    assert count_positions(store) == 0


def test_job_deadlines_are_independent_of_notice_validity_and_cost_category(store):
    material = store.add_notice()
    positions = [
        facts("expired", endpoint=date_fact(2014, 6, 30)),
        facts("future", endpoint=date_fact(2026, 11, 1)),
        facts("inferred", endpoint=date_fact(2014, 6, 30, inferred=True)),
        facts("missing-year", endpoint=date_fact(None, 6, 30)),
        facts("unknown"),
    ]
    result = output(material, positions)
    # Old notice metadata is not an accepted execution contract. A fallible
    # current cost category still cannot override precise job facts.
    from youju.onboarding.auditing import runtime_issues

    assert runtime_issues({**result, "validity": {"status": "expired", "deadline": "1900-01-01"}})
    result["routing"].update(category="fixed_active", mode="detailed")
    result["execution"]["parser"] = "parser.py"
    summary = record(store, material, result)
    store.admit([summary])
    rows = visible(store)
    assert {r["facts"]["title"]["value"] for r in rows} == {
        "expired",
        "future",
        "inferred",
        "missing-year",
        "unknown",
    }
    for row in rows:
        assert row["validity"]["verification_scope"] == "position"
        assert set(row["validity"]) == {
            "status",
            "deadline",
            "reason",
            "evidence_ids",
            "current_hiring_status",
            "verification_scope",
            "evaluated_at",
        }
        assert row["extraction_path"] == "detailed"
        if row["facts"]["title"]["value"] == "expired":
            assert row["validity"]["status"] == "expired" and "expired" in row["display_tags"]
        elif row["facts"]["title"]["value"] != "future":
            assert row["validity"]["status"] == "unknown"
    assert len(current_source_positions(store, "source")) == 4
    assert len(current_source_positions(store, "source", include_expired=True)) == 5


def test_quick_exact_positions_are_saved_and_public_without_route_quality_gates(store):
    material = store.add_notice(texts=["报名截止2014年6月30日"])
    position = facts(endpoint=date_fact(2026, 11, 1))
    summary = record(store, material, output(material, [position]))
    assert count_positions(store) == 1
    assert admission_quality([summary], 1) == ([summary], [], False)
    store.admit([summary])
    assert visible(store)[0]["facts"] == position.as_dict()
    assert visible(store)[0]["extraction_path"] == "quick"
    assert len(current_source_positions(store, "source")) == 1


def test_education_facets_use_typed_education_only_never_degree_tokens(store):
    def condition(kind, level):
        return Condition(kind, kind, "at_least", TaggedField({"level": level}, "source"))

    material = store.add_notice()
    positions = [
        facts("education-bachelor", conditions=[condition("education", "bachelor")]),
        facts("degree-bachelor", conditions=[condition("degree", "bachelor")]),
        facts("education-master", conditions=[condition("education", "master")]),
        facts(
            "postgraduate-degree-master",
            conditions=[condition("education", "postgraduate"), condition("degree", "master")],
        ),
        facts("education-doctorate", conditions=[condition("education", "doctorate")]),
        facts("unknown"),
    ]
    summary = record(store, material, output(material, positions))
    store.admit([summary])
    for selected in ("bachelor", "master", "doctorate"):
        rows = visible(store, education=selected)
        assert [r["facts"]["title"]["value"] for r in rows] == ["education-" + selected]
    assert {r["facts"]["title"]["value"] for r in visible(store, education="unknown")} == {
        "degree-bachelor",
        "unknown",
    }


@pytest.mark.parametrize(
    "clock,status",
    [("13:59:59", "not_expired"), ("14:00:59", "not_expired"), ("14:01:00", "expired")],
)
def test_job_only_minute_boundary(clock, status):
    position = facts(endpoint=date_fact(2026, 10, 24, clock="14:00"))
    value = job_validity(position, DAY, now_iso=f"2026-10-24T{clock}+08:00")
    assert value["status"] == status and value["evidence_ids"] == []


def test_day_precision_inference_mixed_certainty_and_rule_conflicts():
    assert job_validity(facts(endpoint=date_fact(2026, 10, 24)), DAY)["status"] == "not_expired"
    assert (
        job_validity(facts(endpoint=date_fact(2014, 6, 30, inferred=True)), DAY)["status"]
        == "unknown"
    )
    mixed = facts(start=date_fact(2014, 6, 1, inferred=True), endpoint=date_fact(2014, 6, 30))
    assert job_validity(mixed, DAY)["status"] == "expired"
    for rule in ("until_filled", "open_ended"):
        assert job_validity(facts(rule=rule), DAY)["status"] == "unknown"


@pytest.mark.parametrize("clock", ["2026-10-24T12:00:00", "2026-10-25T12:00:00+08:00"])
def test_job_validity_requires_consistent_aware_clock(clock):
    with pytest.raises(ValueError, match="consistent aware clock"):
        job_validity(facts(), DAY, now_iso=clock)


def test_newer_failed_attempt_labels_but_keeps_saved_candidates(store):
    material = store.add_notice()
    summary = record(store, material, output(material, [facts()]))
    store.admit([summary])
    assert visible(store)
    with Session(store.engine) as session, session.begin():
        session.add(
            AuditResult(
                bundle_id=material["id"],
                recipe_id="recipe",
                evaluated_on="2026-10-25",
                generation=0,
                status="review",
                result=output(material, [facts()], status="review"),
                errors=[],
            )
        )
    rows = visible(store)
    assert len(rows) == 1 and "needs_review" in rows[0]["display_tags"]


def test_reassess_reruns_domain_validation_without_legacy_period_oracle(store, monkeypatch):
    material = store.add_notice()
    summary = record(store, material, output(material, [facts(endpoint=date_fact(2026, 11, 1))]))
    store.admit([summary])
    called = []
    original = auditing.validate_output

    def domain(bundle, result):
        called.append(bundle["id"])
        return original(bundle, result)

    def forbidden(*args, **kwargs):
        pytest.fail("Reassessment must not run a legacy notice-validity oracle")

    monkeypatch.setattr(auditing, "validate_output", domain)
    monkeypatch.setattr("youju.onboarding.toolkit.check_validity", forbidden)
    monkeypatch.setattr("youju.onboarding.periods.evaluate_period", forbidden)
    status = AuditLedger(store).reassess("source")
    assert called == [material["id"]]
    assert status["binding"]["generation"] == 1
    assert status["summary"]["audit"]["routing_categories"] == {"undated_high_priority": 1}
    assert len(visible(store)) == 1
    assert count_positions(store) == 2  # old generation is immutable


class ScriptPort:
    def __init__(self, draft):
        self.draft, self.calls = draft, []

    async def run_script(self, files, module, function, inputs):
        self.calls.append(deepcopy(inputs))
        return deepcopy(self.draft)


class RecordingRunner(RecipeRunner):
    def __init__(self, port):
        super().__init__(port)
        self.routes = []

    async def run(self, candidate, bundle, **kwargs):
        self.routes.append(deepcopy(kwargs["routing"]))
        return await super().run(candidate, bundle, **kwargs)


def make_pipeline(store, runner, monkeypatch):
    # Only host workspace persistence is doubled; audits, admission, activation and
    # official Flow execution remain real, with an injected synthetic script port.
    class Workspace:
        def __init__(self, *args, **kwargs):
            pass

        def record_audit(self, feedback):
            pass

    monkeypatch.setattr(pipeline_module, "AuthorWorkspace", Workspace)
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store, pipeline.runner, pipeline.models = store, runner, None
    pipeline.audits, pipeline.classifier_key, pipeline.concurrency = (
        AuditLedger(store),
        "synthetic",
        1,
    )
    pipeline.sandbox = None
    pipeline.incrementals = SimpleNamespace()

    async def reject_candidate(*args):
        pass

    pipeline.recipe_coordinator = SimpleNamespace(reject_candidate=reject_candidate)
    return pipeline


@pytest.mark.parametrize(
    "capability", [None, "UNSUPPORTED_POSITION_LAYOUT", "UNSUPPORTED_POSITION_TABLE"]
)
def test_pipeline_explicit_route_and_zero_row_quick_archive_can_activate_source(
    store, monkeypatch, capability
):
    store.add_notice(texts=["报名时限", "至2014年6月30日止"])
    draft = ExtractionDraft(
        coverage={"supported": False, "complete": False, "needs_detailed": True},
        issues=[{"code": capability, "message": "Synthetic unsupported layout"}]
        if capability
        else [],
    ).as_dict()
    port = ScriptPort(draft)
    runner = RecordingRunner(port)
    pipeline = make_pipeline(store, runner, monkeypatch)
    with Session(store.engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        binding.active_recipe_id, binding.candidate_recipe_id = None, "recipe"
        session.get(SourceRecipe, "recipe").status = "validated"
        session.get(SourcePipeline, "source").stage = "audit"
    summary = asyncio.run(pipeline._audit("source", limit=10, activate=True))
    assert summary["passed"] and summary["admission"]["mode"] == "quick_archives_only"
    assert summary["position_count"] == 0 and summary["archived_announcements"] == 1
    assert summary["routing_categories"] == {"fixed_expired": 1}
    assert summary["extraction_paths"] == {"quick": 1}
    assert summary["coverage_issues_by_code"] == ({capability: 1} if capability else {})
    assert not summary["errors_by_code"] and not summary["warnings_by_code"]
    assert not ({"validity", "coarse_announcements", "quality_tiers"} & summary.keys())
    assert runner.routes[0]["mode"] == "quick" and len(port.calls) == 1
    assert not ({"routing", "now_iso", "validity"} & port.calls[0].keys())
    assert store.status("source")["stage"] == "active"
    assert not count_positions(store)
    with Session(store.engine) as session:
        assert session.get(OnboardingResult, "source") is not None
    assert not visible(store, include_partial=True)
    reassessed = AuditLedger(store).reassess("source")
    assert reassessed["summary"]["audit"]["admission"]["mode"] == "quick_archives_only"
    assert len(port.calls) == 1  # replay performs no script/model work


@pytest.mark.parametrize(
    "issue",
    [
        {"code": "INDEPENDENT_RULE_MISMATCH", "message": "Rule assertion failed"},
        {"code": "UNKNOWN_SOURCE_SEMANTICS", "message": "Unrecognized source semantic failure"},
        {"code": "SEMANTIC_EXPECTATION", "message": "Independent expectation failed"},
        {
            "code": "UNSUPPORTED_POSITION_LAYOUT",
            "message": "Source execution failure",
            "scope": "source",
        },
    ],
)
def test_quick_path_never_whitelists_source_semantic_errors(store, issue):
    material = store.add_notice(texts=["发布日期：2014年6月30日"])
    result = output(material, complete=False, errors=[issue])
    summary = record(store, material, result)
    assert summary["status"] == "review" and issue in summary["errors"]
    assert summary["coverage_issues"] == [] and not admission_quality([summary], 1)[2]


def test_detailed_layout_review_is_not_relabelled_as_quick_archive(store):
    material = store.add_notice()
    issue = {"code": "UNSUPPORTED_POSITION_LAYOUT", "message": "Synthetic layout"}
    summary = record(store, material, output(material, complete=False, errors=[issue]))
    assert summary["status"] == "review"
    assert (
        summary["extraction_path"] == "detailed" and summary["extraction_status"] == "needs_review"
    )
    assert summary["errors"] == [] and summary["coverage_issues"] == [issue]
    assert not admission_quality([summary], 1)[2]


def test_established_staffing_is_a_filter_projection_not_an_employment_enum(store):
    assert "public_institution_staff" in EMPLOYMENT_FILTERS
    assert "public_institution_staff" not in EMPLOYMENT_TYPES
    material = store.add_notice()
    positions = [
        facts("established", staffing="established"),
        facts("established-contract", staffing="established", employment="labor_contract"),
        facts("not-established", staffing="non_established"),
        facts("contract", employment="labor_contract"),
        facts("unknown"),
    ]
    summary = record(store, material, output(material, positions))
    store.admit([summary])

    def titles(selected):
        return {r["facts"]["title"]["value"] for r in visible(store, employment=selected)}

    assert titles("public_institution_staff") == {"established", "established-contract"}
    assert titles("labor_contract") == {"established-contract", "contract"}
    assert titles("unknown") == {"unknown"}


def test_orm_reliable_dates_exclude_inferred_and_unknown_year_candidates(store):
    material = store.add_notice()
    positions = [
        facts("explicit", start=date_fact(2026, 10, 20), endpoint=date_fact(2026, 11, 1)),
        facts("inferred", endpoint=date_fact(2014, 6, 30, inferred=True)),
        facts("missing-year", endpoint=date_fact(None, 6, 30)),
    ]
    summary = record(store, material, output(material, positions))
    with Session(store.engine) as session:
        rows = session.scalars(select(Position).order_by(Position.ordinal)).all()
        assert rows[0].deadline_on == date(2026, 11, 1)
        assert rows[0].application_start_on == date(2026, 10, 20)
        assert rows[1].deadline_on is None and rows[2].deadline_on is None
    store.admit([summary])
    assert len(visible(store)) == 3


def test_reassess_rejects_invalid_domain_facts_without_switching_generation(store):
    material = store.add_notice()
    summary = record(store, material, output(material, [facts()]))
    store.admit([summary])
    with Session(store.engine) as session, session.begin():
        row = session.get(AuditResult, summary["id"])
        result = deepcopy(row.result)
        result["positions"][0]["title"] = {"value": None, "tag": "unknown"}
        row.result = result  # simulate a saved invalid output, not an alternate validator
    with pytest.raises(ValueError, match="Reassessment needs complete extraction"):
        AuditLedger(store).reassess("source")
    assert store.status("source")["binding"]["generation"] == 0


def test_partial_warning_reassessment_does_not_invent_a_schema_failure(store):
    material = store.add_notice(partial=True)
    summary = record(store, material, output(material, [facts()]))
    store.admit([summary])
    assert summary["status"] == "partial"
    reassessed = AuditLedger(store).reassess("source")
    assert not reassessed["summary"]["audit"]["errors_by_code"]
    assert len(visible(store, include_partial=True)) == 1


def test_detailed_runtime_failure_records_v3_diagnostics_and_never_completes_admission(
    store, monkeypatch
):
    store.add_notice()  # undated high priority: missing support needs detailed extraction
    draft = ExtractionDraft(
        coverage={"supported": False, "complete": False, "needs_detailed": True}
    ).as_dict()
    pipeline = make_pipeline(store, RecordingRunner(ScriptPort(draft)), monkeypatch)
    with Session(store.engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        binding.active_recipe_id, binding.candidate_recipe_id = None, "recipe"
        session.get(SourceRecipe, "recipe").status = "validated"
        session.get(SourcePipeline, "source").stage = "audit"
    summary = asyncio.run(pipeline._audit("source", limit=10, activate=True))
    assert not summary["passed"] and summary["statuses"] == {"failed": 1}
    assert summary["admission"]["source_errors_by_code"] == {"MODEL_DISABLED": 1}
    assert store.status("source")["state"] == "blocked" and not count_positions(store)
    with Session(store.engine) as session:
        row = session.scalar(select(AuditResult))
        assert row.result["schema_version"] == "position-facts-v3"
        assert not ({"validity", "quality_tier"} & row.result.keys())
        assert row.result["execution"]["model_used"] is None  # failed attempt is not a usage claim
        assert {e["code"] for e in row.errors} == {"MODEL_DISABLED"}


def test_source_view_and_public_share_job_only_within_day_expiry(store):
    material = store.add_notice()
    summary = record(
        store, material, output(material, [facts(endpoint=date_fact(2026, 10, 24, clock="09:00"))])
    )
    store.admit([summary])
    rows = visible(store, now_iso=STAMP.isoformat())
    assert len(rows) == 1 and "expired" in rows[0]["display_tags"]
    assert not current_source_positions(store, "source")
    assert len(current_source_positions(store, "source", include_expired=True)) == 1


def test_missing_execution_receipt_fails_closed_without_losing_diagnostics(store):
    material = store.add_notice()
    result = output(material, [facts()])
    result["execution"] = None
    summary = record(store, material, result)
    assert summary["status"] == "review"
    assert summary["extraction_path"] == "unknown" and summary["runtime_contract"] is None
    assert {e["code"] for e in summary["errors"]} == {"RUNTIME_RESULT_INVALID"}
    assert not count_positions(store)


@pytest.mark.parametrize(
    "case",
    [
        "future",
        "expired",
        "inferred",
        "missing_year",
        "field_unknown",
        "date_conflict",
        "rule_conflict",
    ],
)
def test_cost_route_counterfactual_has_identical_persistence_and_public_facts(store, case):
    position = {
        "key": "row:1",
        "fields": {"title": {"value": "同一岗位", "tag": "source"}},
        "dates": {"end": date_fact(2026, 11, 1)},
    }
    if case == "expired":
        position["dates"]["end"] = date_fact(2014, 6, 30)
    elif case == "inferred":
        position["dates"]["end"] = date_fact(2014, 6, 30, inferred=True)
    elif case == "missing_year":
        position["dates"]["end"] = date_fact(None, 6, 30)
    elif case == "field_unknown":
        position["fields"]["headcount"] = {"value": "many", "tag": "source"}
    elif case == "date_conflict":
        position["dates"] = {"start": date_fact(2026, 11, 2), "end": date_fact(2026, 11, 1)}
    elif case == "rule_conflict":
        position["dates"]["end"] = date_fact(2014, 6, 30)
        position["fields"]["application_rule"] = {"value": "open_ended", "tag": "source"}
    draft = ExtractionDraft(
        positions=[position],
        coverage={"supported": True, "complete": True, "needs_detailed": False},
    ).as_dict()
    normalized = normalize_extraction(draft)
    assert normalized["positions"]
    if case in {"field_unknown", "date_conflict", "rule_conflict"}:
        assert normalized["status"] == "partial" and normalized["errors"]
    summaries = []
    for path in ("quick", "detailed"):
        material = store.add_notice(key=path)
        result = deepcopy(normalized)
        result.update(
            routing={
                "category": "fixed_expired" if path == "quick" else "unknown",
                "mode": path,
                "reason": "Deliberately fallible cost hypothesis",
                "policy_version": "counterfactual-v1",
                "evaluated_at": STAMP.isoformat(),
            },
            execution={
                "path": path,
                "model_used": False,
                "runtime_contract": "recipe-runtime-v3",
                "parser": "parser.py",
                "script_issues": [],
            },
        )
        summaries.append(record(store, material, result))
    assert count_positions(store) == 2
    assert all(not summary["errors"] for summary in summaries)
    store.admit(summaries)
    rows = visible(store)
    assert len(rows) == 2
    assert all(("expired" in row["display_tags"]) is (case == "expired") for row in rows)
    assert len(current_source_positions(store, "source", include_expired=True)) == 2
    if rows:
        assert rows[0]["facts"] == rows[1]["facts"] == normalized["positions"][0]
        assert rows[0]["validity"] == rows[1]["validity"]
        assert {r["extraction_path"] for r in rows} == {"quick", "detailed"}
        assert all("routing_category" not in r for r in rows)
    if case == "field_unknown":
        assert rows[0]["facts"]["headcount"] == {"value": None, "tag": "unknown"}
    if case in {"date_conflict", "rule_conflict"}:
        assert rows[0]["validity"]["status"] == "unknown"


def capture_fixture():
    return {
        "status": "partial",
        "errors": [{"code": "SAVED_CAPTURE_WARNING", "message": "Synthetic capture warning"}],
        "page": {
            "status": "partial",
            "truncated": False,
            "errors": [],
            "url": "https://example.org/page",
            "links": [{"is_attachment": True, "url": "https://example.org/missing"}],
        },
        "attachments": [
            None,
            {"status": "failed", "url": "https://example.org/failed", "blocks": [], "errors": []},
        ],
    }


def test_capture_diagnostics_match_exact_parser_messages_and_document_indices():
    bundle = capture_fixture()
    expected = [
        {"code": "INCOMPLETE_CAPTURE", "message": "Saved bundle is not declared complete"},
        {"code": "CAPTURE_DIAGNOSTICS", "message": "Bundle contains acquisition diagnostics"},
        {"code": "INCOMPLETE_CAPTURE", "message": "Document capture is incomplete", "document": 0},
        {"code": "INCOMPLETE_CAPTURE", "message": "Document capture is incomplete", "document": 1},
        {"code": "MISSING_ATTACHMENT", "message": "Linked attachment not captured"},
    ]
    assert capture_diagnostics(bundle) == expected
    assert split_audit_issues(bundle, expected) == (expected, [])


@pytest.mark.parametrize(
    "issue",
    [
        {"code": "INCOMPLETE_CAPTURE", "message": "arbitrary candidate message"},
        {"code": "CAPTURE_DIAGNOSTICS", "message": "arbitrary candidate message"},
        {"code": "MISSING_ATTACHMENT", "message": "arbitrary candidate message"},
        {"code": "INCOMPLETE_CAPTURE", "message": "Document capture is incomplete", "document": 99},
        {
            "code": "INCOMPLETE_CAPTURE",
            "message": "Document capture is incomplete",
            "document": True,
        },
        {
            "code": "INCOMPLETE_CAPTURE",
            "message": "Document capture is incomplete",
            "document": 0,
            "scope": "source",
        },
    ],
)
def test_capture_codes_and_near_matches_are_not_a_whitelist(issue):
    assert split_audit_issues(capture_fixture(), [issue]) == ([], [issue])


@pytest.mark.parametrize(
    "change", ["complete_bundle", "clean_document", "clean_bundle_errors", "captured_attachment"]
)
def test_capture_diagnostic_requires_the_actual_bundle_condition(change):
    bundle = capture_fixture()
    if change == "complete_bundle":
        bundle["status"] = "complete"
        issue = {"code": "INCOMPLETE_CAPTURE", "message": "Saved bundle is not declared complete"}
    elif change == "clean_document":
        bundle["page"]["status"] = "ok"
        issue = {
            "code": "INCOMPLETE_CAPTURE",
            "message": "Document capture is incomplete",
            "document": 0,
        }
    elif change == "clean_bundle_errors":
        bundle["errors"] = []
        issue = {
            "code": "CAPTURE_DIAGNOSTICS",
            "message": "Bundle contains acquisition diagnostics",
        }
    else:
        bundle["attachments"].append({"url": "https://example.org/missing", "status": "ok"})
        issue = {"code": "MISSING_ATTACHMENT", "message": "Linked attachment not captured"}
    assert split_audit_issues(bundle, [issue]) == ([], [issue])


@pytest.mark.parametrize("path", ["quick", "detailed"])
def test_actual_parser_capture_diagnostics_preserve_known_partial_job_without_suppression(
    store, path
):
    material = store.add_notice(partial=True)
    material["page"].update(
        status="partial",
        truncated=True,
        tables=[
            {"id": "t", "rows": [["岗位名称", "招聘人数"], ["已知岗位", "2"]], "cell_evidence": []}
        ],
        links=[{"is_attachment": True, "url": "https://example.org/missing"}],
    )
    material["attachments"] = [
        {
            "status": "failed",
            "errors": [],
            "blocks": [],
            "tables": [],
            "url": "https://example.org/failed",
        }
    ]
    draft = extract_position_draft(material)
    assert len(draft["positions"]) == 1
    codes = {issue["code"] for issue in draft["issues"]}
    assert {"INCOMPLETE_CAPTURE", "CAPTURE_DIAGNOSTICS", "MISSING_ATTACHMENT"} <= codes
    result = normalize_extraction(draft)
    original_issues = deepcopy(result["errors"])
    result.update(
        routing={
            "category": "fixed_expired" if path == "quick" else "unknown",
            "mode": path,
            "reason": "Cost only",
            "policy_version": "test-v1",
            "evaluated_at": STAMP.isoformat(),
        },
        execution={
            "path": path,
            "model_used": False,
            "runtime_contract": "recipe-runtime-v3",
            "parser": "parser.py",
            "script_issues": [],
        },
    )
    summary = record(store, material, result)
    assert summary["status"] == "partial" and summary["errors"] == []
    assert all(issue in summary["warnings"] for issue in original_issues)
    assert len(summary["warnings"]) >= len(original_issues)
    assert count_positions(store) == 1
    store.admit([summary])
    rows = visible(store, include_partial=True)
    assert len(rows) == 1 and rows[0]["facts"]["title"]["value"] == "已知岗位"
    assert rows[0]["facts"]["headcount"] == {"value": 2, "tag": "source"}


def test_independent_semantic_mismatch_is_fatal_even_when_usable_rows_are_preserved(store):
    material = store.add_notice()
    issue = {
        "code": "SEMANTIC_EXPECTATION",
        "message": "Independent saved expectation mismatch",
        "scope": "source",
    }
    summary = record(store, material, output(material, [facts()], status="partial", errors=[issue]))
    assert summary["status"] == "review" and summary["errors"] == [issue]
    assert count_positions(store) == 1
    store.admit([summary])
    rows = visible(store, include_partial=True)
    assert len(rows) == 1 and "needs_review" in rows[0]["display_tags"]


@pytest.mark.parametrize("path", ["quick", "detailed"])
def test_current_reviewed_cancellation_blocks_original_without_route_or_period_oracle(
    store, monkeypatch, path
):
    original = store.add_notice(key="original")
    original_result = output(original, [facts(endpoint=date_fact(2026, 11, 1))])
    original_result["routing"].update(
        category="fixed_expired" if path == "quick" else "fixed_active", mode=path
    )
    original_result["execution"]["path"] = path
    original_result["execution"]["parser"] = "parser.py"
    admitted = record(store, original, original_result)
    store.admit([admitted])
    assert len(visible(store)) == 1
    event = store.add_notice(key="cancellation", texts=["经审阅确认取消原招聘公告"])
    with Session(store.engine) as session, session.begin():
        session.add(
            AnnouncementChangeReview(
                id="review-cancel",
                source_id="source",
                target_resource_id="original",
                target_bundle_id=original["id"],
                event_resource_id="cancellation",
                event_bundle_id=event["id"],
                effect="cancellation",
                reviewer="synthetic-reviewer",
                reference="Synthetic independent reviewed association",
            )
        )

    def forbidden(*args, **kwargs):
        pytest.fail("A reviewed cancellation must not re-enter the old period oracle")

    monkeypatch.setattr("youju.onboarding.periods.evaluate_period", forbidden)
    monkeypatch.setattr("youju.onboarding.toolkit.check_validity", forbidden)
    # A cancellation is visible as a risk tag, not an expiry or a hidden candidate.
    rows = visible(store, include_partial=True)
    assert len(rows) == 1 and "cancelled" in rows[0]["display_tags"]
    assert not current_source_positions(store, "source", include_expired=True, include_partial=True)
    original["period_context"] = {
        "change": {
            "review_id": "review-cancel",
            "effect": "cancellation",
            "status": "current",
            "target_bundle_id": original["id"],
            "event_bundle_id": event["id"],
        }
    }
    summary = record(store, original, original_result, generation=1)
    assert summary["status"] == "review"
    change_issue = next(e for e in summary["errors"] if e["code"] == "ASSOCIATED_CHANGE_UNRESOLVED")
    assert change_issue["owner"] == "position_domain" and change_issue["repairable"] is False
    assert (
        change_issue["association_status"] == "current" and change_issue["effect"] == "cancellation"
    )
    assert not admission_quality([summary], 1)[2]
    # No new date/cancellation facts were fabricated and the actual positions survive privately.
    with Session(store.engine) as session:
        saved = session.get(AuditResult, summary["id"])
        assert (
            "validity" not in saved.result
            and saved.result["positions"] == original_result["positions"]
        )
    assert count_positions(store) == 2


def test_explicit_change_context_without_domain_dependency_support_fails_closed(store):
    original = store.add_notice()
    original["period_context"] = {
        "change": {"review_id": "frozen-review", "effect": "cancellation", "status": "current"}
    }
    result = output(original, [facts()])
    summary = record(store, original, result)
    assert summary["status"] == "review" and count_positions(store) == 1
    assert summary["errors"][0]["code"] == "ASSOCIATED_CHANGE_UNRESOLVED"
    store.admit([summary])
    rows = visible(store, include_partial=True)
    assert len(rows) == 1 and "needs_review" in rows[0]["display_tags"]


@pytest.mark.parametrize(
    "clock,status",
    [
        ("2026-10-24T23:59:59+08:00", "not_expired"),
        ("2026-10-25T00:00:00+08:00", "expired"),
        ("2026-10-25T00:00:30+08:00", "expired"),
    ],
)
def test_explicit_24_hour_endpoint_is_exact_next_midnight_without_extra_minute(clock, status):
    endpoint = date_fact(2026, 10, 24, clock="24:00")
    assert endpoint["time"] == "24:00" and endpoint["date"] == "2026-10-24"
    result = job_validity(
        facts(endpoint=endpoint), datetime.fromisoformat(clock).date(), now_iso=clock
    )
    assert result["status"] == status and result["deadline"] == "2026-10-24"


def test_public_24_hour_endpoint_kept_with_expired_tag_at_midnight(store):
    material = store.add_notice()
    summary = record(
        store, material, output(material, [facts(endpoint=date_fact(2026, 10, 24, clock="24:00"))])
    )
    store.admit([summary])
    assert len(visible(store, now_iso="2026-10-24T23:59:59+08:00")) == 1
    with Session(store.engine) as session:
        rows = list(
            public_jobs(session, day=DAY + timedelta(days=1), now_iso="2026-10-25T00:00:00+08:00")
        )
        assert len(rows) == 1 and "expired" in rows[0]["display_tags"]


def test_per_position_date_dispute_does_not_overrule_another_jobs_exact_expiry(store):
    material = store.add_notice()
    draft = ExtractionDraft(
        positions=[
            {
                "key": "conflicted",
                "fields": {
                    "title": {"value": "有冲突岗位", "tag": "source"},
                    "application_rule": {"value": "open_ended", "tag": "source"},
                },
                "dates": {"end": date_fact(2014, 6, 30)},
            },
            {
                "key": "expired",
                "fields": {"title": {"value": "已截止岗位", "tag": "source"}},
                "dates": {"end": date_fact(2014, 6, 30)},
            },
        ],
        coverage={"supported": True, "complete": True, "needs_detailed": False},
    ).as_dict()
    result = normalize_extraction(draft)
    assert result["errors"][0]["code"] == "APPLICATION_RULE_CONFLICT"
    metadata = output(material)
    result.update(routing=metadata["routing"], execution=metadata["execution"])
    summary = record(store, material, result)
    store.admit([summary])
    rows = visible(store)
    assert len(rows) == 2
    by_title = {row["facts"]["title"]["value"]: row for row in rows}
    assert by_title["有冲突岗位"]["validity"]["status"] == "unknown"
    assert by_title["已截止岗位"]["validity"]["status"] == "expired"
    assert "expired" in by_title["已截止岗位"]["display_tags"]
    assert by_title["有冲突岗位"]["review_required"] is True
    assert by_title["已截止岗位"]["review_required"] is False
    assert "needs_review" not in by_title["已截止岗位"]["display_tags"]
    assert len(current_source_positions(store, "source")) == 1


@pytest.mark.parametrize("same_artifacts", [True, False])
def test_shared_runtime_readmission_preserves_terminal_service_revision_review(
    store, same_artifacts
):
    revision = "same-immutable-revision"
    with Session(store.engine) as session, session.begin():
        old = session.get(SourceRecipe, "recipe")
        old.workspace_id = "w" * 64
        old.revision_id = revision
        new = SourceRecipe(
            id="runtime-revalidated",
            source_id="source",
            digest="new-runtime-identity",
            body_ref=old.body_ref if same_artifacts else "different-artifact",
            status="validated",
            workspace_id=old.workspace_id,
            revision_id=revision,
            validation={"errors": [], "toolkit_key": toolkit_key()},
        )
        session.add(new)
        binding = session.get(SourceRecipeBinding, "source")
        binding.active_revision_id = binding.candidate_revision_id = revision
        binding.candidate_recipe_id = new.id
        prior = {
            "status": "delivered",
            "intent": {
                "revision_id": revision,
                "decision": "accept",
                "request_id": "original-adoption",
            },
        }
        pipeline = session.get(SourcePipeline, "source")
        pipeline.stage = "audit"
        pipeline.summary = dict(pipeline.summary, revision_review=prior, audit_id="runtime-audit")
        session.add(
            AuditRun(
                id="runtime-audit",
                source_id="source",
                recipe_id=new.id,
                kind="onboarding",
                status="complete",
                finished_at=STAMP,
                summary={
                    "passed": True,
                    "generation": 1,
                    "results": [],
                    "admission": {"policy": "quarantine-v1"},
                },
            )
        )
    pipeline = Pipeline(store)
    if not same_artifacts:
        with pytest.raises(ValueError, match="identical artifacts"):
            pipeline._activate("source", "runtime-revalidated")
    else:
        pipeline._activate("source", "runtime-revalidated")
    with Session(store.engine) as session:
        row = session.get(SourcePipeline, "source")
        binding = session.get(SourceRecipeBinding, "source")
        assert row.summary["revision_review"] == prior
        assert binding.active_recipe_id == ("runtime-revalidated" if same_artifacts else "recipe")
        assert binding.active_revision_id == revision


def test_saved_material_audit_does_not_schedule_capture_retries(store, monkeypatch):
    store.add_notice(texts=["报名时限", "至2014年6月30日止"])
    draft = ExtractionDraft(
        coverage={"supported": False, "complete": False, "needs_detailed": True}
    ).as_dict()
    pipeline = make_pipeline(store, RecordingRunner(ScriptPort(draft)), monkeypatch)
    with Session(store.engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        binding.active_recipe_id, binding.candidate_recipe_id = None, "recipe"
        session.get(SourceRecipe, "recipe").status = "validated"
        session.get(SourcePipeline, "source").stage = "audit"

    def forbidden(*args):
        pytest.fail("Saved-material replay cannot schedule acquisition")

    monkeypatch.setattr(pipeline, "_render_retries", forbidden)
    monkeypatch.setattr(pipeline, "_needs_parent_render", lambda *args: True)
    result = asyncio.run(
        pipeline._audit("source", limit=10, activate=True, retry_acquisition=False)
    )
    assert result["acquisition_retries_enabled"] is False
    assert result["passed"] and store.status("source")["stage"] == "active"


@pytest.mark.parametrize("pin", ["matching", "different_recipe", "different_generation"])
def test_background_saved_reaudit_policy_is_pinned_to_candidate_epoch(store, monkeypatch, pin):
    from unittest.mock import AsyncMock

    with Session(store.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        binding = session.get(SourceRecipeBinding, "source")
        binding.candidate_recipe_id = "recipe"
        row.stage = "audit"
        row.summary = dict(
            row.summary,
            candidate_generation=2,
            runtime_reparse={
                "reuse_saved_materials": True,
                "recipe_id": "other" if pin == "different_recipe" else "recipe",
                "generation": 1 if pin == "different_generation" else 2,
            },
        )
    original_status = store.status
    # Mirror PipelineStore.status's top-level candidate pointer; this lightweight
    # historical test store otherwise exposes only the binding generation.
    monkeypatch.setattr(
        store,
        "status",
        lambda source_id: {
            **original_status(source_id),
            "candidate_recipe_id": "recipe",
        },
    )
    pipeline = Pipeline(store)
    pipeline.recipe_coordinator.flush_review = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(pipeline, "_audit", audit)
    asyncio.run(pipeline.step("source", limit=3))
    audit.assert_awaited_once_with(
        "source", limit=3, activate=True, retry_acquisition=pin != "matching"
    )
