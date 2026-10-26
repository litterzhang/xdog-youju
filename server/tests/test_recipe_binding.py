"""Fresh source-side binding schemas and offline publication/reassessment checks."""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from threading import RLock
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from youju.admin import pipeline_json
from youju.database import create_sqlite_engine, initialize_database
from youju.jobs import public_jobs
from youju.models import (
    AuditResult,
    AuditRun,
    OnboardingResult,
    PageAssessment,
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
from youju.onboarding.auditing import AUDIT_POLICY, AuditLedger, current_source_positions
from youju.onboarding.recipes import RecipeRunner, template_candidate
from youju.onboarding.store import toolkit_key
from youju.poc.storage import BlobStore
from youju.positions.drafts import ExtractionDraft

DAY = date(2026, 10, 15)
STAMP = datetime(2026, 10, 15, 4, tzinfo=UTC)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    """Own fixture only: no author/coordinator, shared fixture or model runtime."""
    monkeypatch.setattr(
        "youju.onboarding.auditing.evaluation_day", lambda stamp=None: DAY.isoformat()
    )
    engine = create_sqlite_engine(tmp_path / "binding.sqlite3")
    initialize_database(engine)
    assessment = {
        "business_type": "recruitment",
        "page_roles": ["article"],
        "requires_review": False,
    }
    material = {
        "id": "bundle",
        "source_id": "source",
        "resource_id": "resource",
        "url": "https://example.org/notice",
        "status": "complete",
        "errors": [],
        "attachments": [],
        "assessment": assessment,
        "page": {
            "version_id": "version",
            "url": "https://example.org/notice",
            "format": "html",
            "status": "ok",
            "title": "研究助理招聘公告",
            "blocks": [{"id": "title", "text": "研究助理招聘公告", "locator": "title"}],
            "tables": [],
            "links": [],
            "errors": [],
            "truncated": False,
        },
    }

    class SavedParser:
        async def run_script(self, files, module, function, inputs):
            assert module in {"parser", "undated_parser"} and set(files) == {f"{module}.py"}
            assert (module, function) == ("parser", "parse")
            assert set(inputs) == {"bundle", "config"}  # No clock or cost route in facts.
            return ExtractionDraft(
                positions=[
                    {
                        "key": "fixture-row",
                        "fields": {
                            "title": {"value": "研究助理", "tag": "source"},
                        },
                    }
                ],
                coverage={"supported": True, "complete": True, "needs_detailed": False},
            ).as_dict()

    result = asyncio.run(
        RecipeRunner(SavedParser()).run(
            template_candidate(),
            material,
            now_iso=STAMP.isoformat(),
        )
    )
    assert result["schema_version"] == "position-facts-v3"
    assert result["execution"]["runtime_contract"] == "recipe-runtime-v3"
    assert "validity" not in result and "quality_tier" not in result
    with Session(engine) as session, session.begin():
        session.add(
            Source(
                id="source",
                name="合成官方来源",
                base_url="https://example.org/",
                status=RegistryStatus.ACTIVE,
                verified_at=STAMP,
                authority_evidence="Synthetic approval only",
            )
        )
        session.flush()
        for identity, status in (("active", "active"), ("candidate", "validated")):
            session.add(
                SourceRecipe(
                    id=identity,
                    source_id="source",
                    workspace_id="workspace",
                    revision_id="revision-" + identity,
                    digest=identity,
                    body_ref="saved",
                    status=status,
                    validation={"toolkit_key": toolkit_key()},
                )
            )
        session.flush()
        session.add(
            SourceRecipeBinding(
                source_id="source",
                workspace_id="workspace",
                active_recipe_id="active",
                active_revision_id="revision-active",
                candidate_recipe_id="candidate",
                candidate_revision_id="revision-candidate",
                generation=7,
            )
        )
        session.add(
            SourcePipeline(
                source_id="source",
                stage="active",
                config={"max_depth": 100, "max_resources": 1000, "audit_generation": 999},
                summary={"classification": {"classifier_key": "synthetic"}},
            )
        )
        session.add(Resource(id="resource", source_id="source", original_url=material["url"]))
        session.flush()
        session.add(
            ResourceVersion(
                id="version",
                resource_id="resource",
                content_sha256="a" * 64,
                body_ref="saved",
                byte_length=1,
            )
        )
        session.flush()
        session.add(
            ResourceBundle(
                id="bundle",
                resource_id="resource",
                page_version_id="version",
                digest="bundle",
                body_ref="saved",
                status="complete",
                dependencies=[],
            )
        )
        session.flush()
        session.add(
            ResourceScan(
                resource_id="resource",
                state="done",
                bundle_id="bundle",
                last_version_id="version",
                last_success_at=STAMP,
            )
        )
        session.add(
            PageAssessment(bundle_id="bundle", classifier_key="synthetic", result=assessment)
        )
    store = SimpleNamespace(engine=engine, lock=RLock(), blobs=BlobStore(tmp_path / "blobs"))
    ledger = AuditLedger(store)
    for generation in (0, 7):
        outcome = ledger.record(material, "active", DAY.isoformat(), result, generation=generation)
        assert outcome["status"] == "success", outcome
        with Session(engine) as session, session.begin():
            run = AuditRun(
                id=f"run-{generation}",
                source_id="source",
                recipe_id="active",
                kind="onboarding",
                status="complete",
                finished_at=STAMP,
                summary={
                    "audit_policy": AUDIT_POLICY,
                    "passed": True,
                    "generation": generation,
                    "evaluated_on": DAY.isoformat(),
                    "remaining": 0,
                    "results": [outcome],
                    "admission": {"policy": "quarantine-v1", "blocking_reasons": []},
                },
            )
            session.add(run)
            session.flush()
            if generation == 7:
                session.add(
                    OnboardingResult(
                        source_id="source",
                        recipe_id="active",
                        audit_id=run.id,
                        completed_at=STAMP,
                        summary={"audit": {"position_count": 123}},
                        acquisition={"done": 42},
                    )
                )
    store.approved = lambda session, identity: session.get(Source, identity)
    store.counts = lambda identity: {"done": 1}
    store.bundles = lambda identity, **kwargs: [deepcopy(material)]
    store.load_bundle = lambda identity, **kwargs: deepcopy(material)
    store.status = lambda identity: {"source_id": identity}
    try:
        yield engine, store, material, result
    finally:
        engine.dispose()


def public_ids(session):
    return [job["id"] for job in public_jobs(session, day=DAY)]


def test_fresh_binding_schema_and_nullable_offline_artifact(saved):
    engine, _, _, _ = saved
    columns = {column["name"] for column in inspect(engine).get_columns("source_pipelines")}
    assert not {"active_recipe_id", "candidate_recipe_id"} & columns
    assert not hasattr(SourcePipeline, "active_recipe_id")
    assert not hasattr(SourcePipeline, "candidate_recipe_id")
    with Session(engine) as session, session.begin():
        session.add(
            SourceRecipe(id="offline", source_id="source", digest="offline", body_ref="offline")
        )
        session.flush()
        artifact = session.get(SourceRecipe, "offline")
        assert artifact.workspace_id is None and artifact.revision_id is None
        binding = session.get(SourceRecipeBinding, "source")
        assert binding.generation == 7
        assert binding.updated_at.utcoffset().total_seconds() == 0
        # No service DB FK or automatic ID interpretation.
        binding.active_revision_id = "opaque:external/revision"
        binding.workspace_id = "opaque:external/workspace"


@pytest.mark.parametrize("violation", ["duplicate", "negative", "missing_source", "missing_recipe"])
def test_binding_constraints(saved, violation):
    engine, _, _, _ = saved
    with Session(engine) as session, pytest.raises(IntegrityError), session.begin():
        if violation == "duplicate":
            session.add(SourceRecipeBinding(source_id="source"))
        elif violation == "missing_source":
            session.add(SourceRecipeBinding(source_id="not-a-source"))
        else:
            binding = session.get(SourceRecipeBinding, "source")
            if violation == "negative":
                binding.generation = -1
            else:
                binding.active_recipe_id = "not-a-recipe"
        session.flush()


def test_public_uses_latest_candidates_while_activation_view_uses_binding_generation(saved):
    engine, store, _, _ = saved
    with Session(engine) as session:
        public = list(public_jobs(session, day=DAY))
        assert len(public) == 1
        assert public[0]["admission_audit_id"] == "run-7"
        assert current_source_positions(store, "source")[0]["id"] == public[0]["id"]
        assert len(list(public_jobs(session, day=date(2026, 10, 16)))) == 1
    with Session(engine) as session, session.begin():
        session.get(SourceRecipeBinding, "source").generation = 0
    with Session(engine) as session:
        older = list(public_jobs(session, day=DAY))
        assert len(older) == 1 and older[0]["admission_audit_id"] is None
        assert older[0]["id"] == public[0]["id"]
        assert "needs_review" in older[0]["display_tags"]
    assert current_source_positions(store, "source")[0]["id"] != older[0]["id"]


@pytest.mark.parametrize("change", ["missing", "candidate_only", "unaudited", "wrong_source"])
def test_unadmitted_candidates_remain_visible_but_cross_source_recipe_is_rejected(saved, change):
    engine, store, _, _ = saved
    with Session(engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        if change == "missing":
            session.delete(binding)
        elif change == "candidate_only":
            binding.active_recipe_id = None
        elif change == "unaudited":
            binding.generation = 8
        else:
            session.add(Source(id="other", name="Other", base_url="https://other.example.org/"))
            session.flush()
            session.get(SourceRecipe, "active").source_id = "other"
    with Session(engine) as session:
        rows = list(public_jobs(session, day=DAY))
        assert len(rows) == (0 if change == "wrong_source" else 1)
        if rows:
            assert "needs_review" in rows[0]["display_tags"]
    assert current_source_positions(store, "source") == []


def test_admin_binding_projection_preserves_frozen_baseline(saved):
    engine, _, _, _ = saved
    with Session(engine) as session:
        view = pipeline_json(session, "source")
        assert view["stage"] == "completed"
        assert view["baseline_recipe_id"] == "active"
        assert view["baseline_audit_id"] == "run-7"
        assert view["summary"]["audit"]["position_count"] == 123
        assert view["acquisition"] == {"done": 42}
        assert {
            key: view[key]
            for key in (
                "workspace_id",
                "active_recipe_id",
                "candidate_recipe_id",
                "active_revision_id",
                "candidate_revision_id",
                "generation",
            )
        } == {
            "workspace_id": "workspace",
            "active_recipe_id": "active",
            "candidate_recipe_id": "candidate",
            "active_revision_id": "revision-active",
            "candidate_revision_id": "revision-candidate",
            "generation": 7,
        }
    with Session(engine) as session, session.begin():
        session.delete(session.get(SourceRecipeBinding, "source"))
    with Session(engine) as session:
        view = pipeline_json(session, "source")
        assert view["active_recipe_id"] is None and view["generation"] == 0
        assert view["baseline_recipe_id"] == "active"


def test_reassessment_atomically_advances_binding_only_and_keeps_frozen_result(saved):
    engine, store, material, result = saved
    ledger = AuditLedger(store)
    # An interrupted run left unpublished results: their epoch must not be reused.
    ledger.record(material, "active", DAY.isoformat(), result, generation=30)
    with Session(engine) as session:
        before = public_ids(session)
        frozen = session.get(OnboardingResult, "source")
        baseline = (frozen.recipe_id, frozen.audit_id, frozen.summary, frozen.acquisition)
        old_audits = [(row.id, row.result) for row in session.scalars(select(AuditResult))]
    ledger.reassess("source")
    with Session(engine) as session:
        binding = session.get(SourceRecipeBinding, "source")
        assert binding.generation == 31
        assert binding.active_revision_id == "revision-active"
        assert binding.candidate_recipe_id == "candidate"
        assert session.get(SourcePipeline, "source").config["audit_generation"] == 999
        frozen = session.get(OnboardingResult, "source")
        assert (frozen.recipe_id, frozen.audit_id, frozen.summary, frozen.acquisition) == baseline
        for identity, output in old_audits:
            assert session.get(AuditResult, identity).result == output
        after = public_ids(session)
        assert len(after) == 1 and after != before
        assert current_source_positions(store, "source")[0]["id"] == after[0]
        assert pipeline_json(session, "source")["generation"] == 31


@pytest.mark.parametrize("change", ["generation", "active_recipe_id"])
def test_reassessment_rechecks_binding_before_publication(saved, monkeypatch, change):
    engine, store, _, _ = saved
    put = store.blobs.put

    def concurrent_change(body):
        ref = put(body)
        with Session(engine) as session, session.begin():
            binding = session.get(SourceRecipeBinding, "source")
            setattr(binding, change, 10 if change == "generation" else "candidate")
        return ref

    monkeypatch.setattr(store.blobs, "put", concurrent_change)
    with pytest.raises(ValueError, match="changed during reassessment"):
        AuditLedger(store).reassess("source")
    with Session(engine) as session:
        assert not session.scalar(select(AuditRun).where(AuditRun.kind == "reassessment"))
        assert session.get(OnboardingResult, "source").audit_id == "run-7"
        assert session.get(SourcePipeline, "source").config["audit_generation"] == 999


@pytest.mark.parametrize("deadline", [None, "2026-10-16"])
@pytest.mark.parametrize(
    ("category", "mode"),
    [("fixed_expired", "quick"), ("undated_low_priority", "quick"), ("fixed_active", "detailed")],
)
def test_cost_route_never_supplies_exact_dates_or_job_visibility(saved, deadline, category, mode):
    from youju.positions.deadlines import date_fact

    engine, store, material, original = saved
    route = {**original["routing"], "category": category, "mode": mode}
    dates = {"start": None, "end": date_fact(2026, 10, 16) if deadline else None}

    class RouteBlindParser:
        async def run_script(self, files, module, function, inputs):
            assert set(inputs) == {"bundle", "config"}
            assert "routing" not in inputs["bundle"] and "now_iso" not in inputs["bundle"]
            return ExtractionDraft(
                positions=[
                    {
                        "key": "fixture-row",
                        "fields": {"title": {"value": "研究助理", "tag": "source"}},
                        "dates": dates,
                    }
                ],
                coverage={"supported": True, "complete": True, "needs_detailed": False},
            ).as_dict()

    result = asyncio.run(
        RecipeRunner(RouteBlindParser()).run(
            template_candidate(),
            material,
            now_iso=STAMP.isoformat(),
            routing=route,
        )
    )
    assert result["execution"]["path"] == mode
    assert result["positions"][0]["deadline_on"] == {
        "value": deadline,
        "tag": "source" if deadline else "unknown",
    }
    outcome = AuditLedger(store).record(material, "active", DAY.isoformat(), result, generation=8)
    assert outcome["status"] == "success" and outcome["position_count"] == 1
    with Session(engine) as session, session.begin():
        session.add(
            AuditRun(
                id="route-run",
                source_id="source",
                recipe_id="active",
                kind="incremental",
                status="complete",
                finished_at=STAMP,
                summary={
                    "audit_policy": AUDIT_POLICY,
                    "passed": True,
                    "generation": 8,
                    "evaluated_on": DAY.isoformat(),
                    "remaining": 0,
                    "results": [outcome],
                    "admission": {"policy": "quarantine-v1", "blocking_reasons": []},
                },
            )
        )
        session.get(SourceRecipeBinding, "source").generation = 8
    with Session(engine) as session:
        jobs = list(public_jobs(session, day=DAY))
        assert len(jobs) == 1  # Even fixed_expired quick routing cannot hide valid facts.
        assert jobs[0]["facts"]["deadline_on"]["value"] == deadline
        assert jobs[0]["validity"]["status"] == ("not_expired" if deadline else "unknown")
        assert "routing_category" not in jobs[0]
        later = list(public_jobs(session, day=date(2026, 10, 17)))
        assert len(later) == 1
        assert ("expired" in later[0]["display_tags"]) is bool(deadline)
        assert session.get(OnboardingResult, "source").summary["audit"]["position_count"] == 123


@pytest.mark.parametrize("policy", [None, "superseded-source-policy"])
def test_public_tags_missing_or_stale_admission_without_hiding_candidates(saved, policy):
    engine, _, _, _ = saved
    with Session(engine) as session:
        before = public_ids(session)
        assert len(before) == 1
        frozen = deepcopy(session.get(OnboardingResult, "source").summary)
    with Session(engine) as session, session.begin():
        run = session.get(AuditRun, "run-7")
        summary = dict(run.summary)
        if policy is None:
            summary.pop("audit_policy")
        else:
            summary["audit_policy"] = policy
        run.summary = summary
    with Session(engine) as session:
        assert public_ids(session) == before
        assert "needs_review" in next(public_jobs(session, day=DAY))["display_tags"]
        binding = session.get(SourceRecipeBinding, "source")
        assert binding.active_recipe_id == "active" and binding.generation == 7
        assert session.get(OnboardingResult, "source").summary == frozen
