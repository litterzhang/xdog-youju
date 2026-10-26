"""Incremental repair orchestration using saved synthetic notices; no live SDK/model."""

import asyncio
import copy
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, run_onboarding, setup_source

from youju.jobs import public_jobs
from youju.models import (
    AuditResult,
    IncrementalRun,
    OnboardingResult,
    RepairRequest,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.onboarding.auto_repair import _freeze, advance, enqueue
from youju.onboarding.recipe_client import RecipeRPCError
from youju.onboarding.recipes import candidate_digest
from youju.onboarding.store import digest


def failed_incremental(engine, store, *, code="DRAFT_INVALID", unchanged=False, url="two"):
    bundle = next(b for b in store.bundles("source") if b["url"] == BASE + url)
    with Session(engine) as session, session.begin():
        row = session.scalar(select(AuditResult).where(AuditResult.bundle_id == bundle["id"]))
        row.status = "failed"
        row.errors = [{"code": "SYNTHETIC_FIELD_ERROR", "failure_code": code}]
        row.result = {**row.result, "positions": [], "errors": row.errors, "status": "failed"}
        row.result["execution"] = {
            **row.result["execution"],
            "failure": {
                "code": code,
                "origin": "script_draft",
                "evidence_digest": "a" * 64,
            },
        }
        run = IncrementalRun(
            source_id="source",
            baseline={bundle["resource_id"]: {"bundle": bundle["id"]}} if unchanged else {},
        )
        session.add(run)
        session.flush()
        summary = {"results": [{"id": row.id, "status": row.status, "errors": row.errors}]}
        return run.id, summary


@pytest.mark.parametrize(
    "case", ["changed", "unknown", "unchanged", "quick", "domain", "provider", "budget", "script"]
)
def test_queue_only_current_detailed_cost_adapter_failures(tmp_path, case, monkeypatch):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        with Session(engine) as session:
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
            original = session.get(SourceRecipeBinding, "source").active_recipe_id
        if case == "unknown":
            monkeypatch.setattr(
                "youju.onboarding.auto_repair.routing_for",
                lambda bundle, now: {"category": "unknown", "mode": "detailed"},
            )
        run, summary = failed_incremental(
            engine,
            store,
            code={
                "provider": "MODEL_UNAVAILABLE",
                "budget": "MODEL_TOKEN_LIMIT",
                "domain": "RESULT_INVALID",
            }.get(case, "SCRIPT_PARSE_FAILED" if case == "script" else "DRAFT_INVALID"),
            unchanged=case == "unchanged",
            url="one" if case == "quick" else "two",
        )
        ids = enqueue(pipeline, run, summary)
        assert bool(ids) == (case in {"changed", "unknown", "script"})
        assert enqueue(pipeline, run, summary) == []
        with Session(engine) as session:
            state = session.get(SourcePipeline, "source")
            assert (
                state.stage == "active"
                and session.get(SourceRecipeBinding, "source").active_recipe_id == original
            )
            assert session.get(OnboardingResult, "source").summary == frozen
            if ids:
                request = session.get(RepairRequest, ids[0])
                assert request.origin == "incremental_parse_failure"
                assert request.status == "queued"
                assert request_json_safe(store, ids[0])
    finally:
        engine.dispose()


def request_json_safe(store, request_id):
    entry = next(
        r for r in store.status("source")["incremental"]["repairs"] if r["id"] == request_id
    )
    return "context" not in entry and "feedback_ref" not in entry


class FakeRPC:
    def __init__(self, store, candidate, base):
        self.store, self.candidate, self.base = store, candidate, base
        self.calls = []
        self.lost_ack = True
        self.lost_review_ack = False
        self.workspace_id = digest({"source_id": "source"})

    async def call(self, method, **args):
        self.calls.append((method, args))
        if method == "GetWorkspace":
            return {
                "workspace_id": self.workspace_id,
                "physical_path": str(
                    self.store.blobs.root.absolute() / "authoring" / self.workspace_id
                ),
                "accepted_recipe_digest": self.base,
            }
        if method == "InputCompleted":
            return {"input_revision_id": "f" * 32}
        if method == "StartRepair":
            self.request_id = args["request_id"]
            if self.lost_ack:
                self.lost_ack = False
                raise RecipeRPCError("RECIPE_RPC_UNAVAILABLE", transient=True)
            return {"id": "a" * 32}
        if method == "GetTask":
            return {
                "id": "a" * 32,
                "workspace_id": self.workspace_id,
                "request_id": self.request_id,
                "kind": "repair",
                "status": "candidate_ready",
                "input_revision_id": "f" * 32,
                "artifact_digest": candidate_digest(self.candidate),
                "revision_id": "b" * 32,
            }
        if method == "GetRecipeRevision":
            return {
                "id": "b" * 32,
                "workspace_id": self.workspace_id,
                "task_id": "a" * 32,
                "input_revision_id": "f" * 32,
                "candidate": self.candidate,
                "artifact_digest": candidate_digest(self.candidate),
                "sample_validation": [{"candidate": self.candidate, "errors": [], "samples": []}],
            }
        if method == "ReviewRevision":
            if self.lost_review_ack:
                self.lost_review_ack = False
                raise RecipeRPCError("RECIPE_RPC_UNAVAILABLE", transient=True)
            return {
                "decision": args["decision"],
                "workspace_id": args["workspace_id"],
                "revision_id": args["revision_id"],
            }
        raise AssertionError(method)


@pytest.mark.parametrize("regress", [False, True])
def test_saved_full_audit_atomic_adoption_and_lost_repair_ack(tmp_path, regress):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            original = session.get(SourceRecipeBinding, "source").active_recipe_id
            old = json.loads(store.blobs.read(session.get(SourceRecipe, original).body_ref))
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
            saved = {
                r.bundle_id: copy.deepcopy(r.result) for r in session.scalars(select(AuditResult))
            }
        run, summary = failed_incremental(engine, store)
        request_id = enqueue(pipeline, run, summary)[0]
        candidate = copy.deepcopy(old)
        candidate["parser_py"] += "\n# Synthetic Agent-authored repair fixture.\n"
        rpc = FakeRPC(store, candidate, candidate_digest(old))
        rpc.lost_review_ack = regress
        pipeline.recipe_rpc = rpc
        real_runner = pipeline.runner
        calls = []

        class Runner:
            validate = staticmethod(real_runner.validate)

            async def run(self, candidate, bundle, **kwargs):
                calls.append(bundle["id"])
                result = copy.deepcopy(saved[bundle["id"]])
                if regress and bundle["url"] == BASE + "one":
                    result.update(
                        status="failed",
                        positions=[],
                        errors=[
                            {
                                "code": "SYNTHETIC_REGRESSION",
                                "message": "Synthetic source adapter regression",
                                "failure_code": "DRAFT_INVALID",
                            }
                        ],
                    )
                if result["status"] == "failed":
                    result["execution"] = {
                        **result["execution"],
                        "failure": {
                            "code": "DRAFT_INVALID",
                            "origin": "script_draft",
                            "evidence_digest": "a" * 64,
                        },
                    }
                return result

        pipeline.runner = Runner()
        for _ in range(12):
            asyncio.run(advance(pipeline, "source", limit=1))
            with Session(engine) as session:
                request = session.get(RepairRequest, request_id)
                state = session.get(SourcePipeline, "source")
                assert state.stage == "active"
                assert session.get(OnboardingResult, "source").summary == frozen
                if request.status not in {"accepting", "succeeded"}:
                    assert session.get(SourceRecipeBinding, "source").active_recipe_id == original
                if request.status in {"failed", "succeeded"}:
                    break
        with Session(engine) as session:
            request = session.get(RepairRequest, request_id)
            assert request.status == ("failed" if regress else "succeeded")
            state = session.get(SourcePipeline, "source")
            assert (
                session.get(SourceRecipeBinding, "source").active_recipe_id == original
            ) == regress
            jobs = list(
                public_jobs(
                    session,
                    day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    include_partial=True,
                )
            )
            assert jobs  # Healthy jobs remain publishable, including after candidate adoption.
            if not regress:
                assert all(job["recipe_id"] == request.candidate_recipe_id for job in jobs)
        starts = [args for method, args in rpc.calls if method == "StartRepair"]
        assert len(starts) == 2 and starts[0] == starts[1]
        assert len(calls) == 3  # Saved full source, not just authoring samples.
        reviews = [args for method, args in rpc.calls if method == "ReviewRevision"]
        assert reviews[-1]["decision"] == ("reject" if regress else "accept")
        if regress:
            assert len(reviews) == 2 and reviews[0] == reviews[1]
        assert enqueue(pipeline, run, summary) == []
    finally:
        engine.dispose()


def test_changed_material_cannot_activate_stale_candidate(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        run, summary = failed_incremental(engine, store)
        request_id = enqueue(pipeline, run, summary)[0]
        with Session(engine) as session:
            request = session.get(RepairRequest, request_id)
            original = request.base_recipe_id
            base = request.base_recipe_digest
            old = json.loads(store.blobs.read(session.get(SourceRecipe, original).body_ref))
            session.expunge(request)
        context = _freeze(pipeline, request)
        candidate = copy.deepcopy(old)
        candidate["parser_py"] += "\n# Repair fixture\n"
        pipeline.recipe_rpc = FakeRPC(store, candidate, base)
        with Session(engine) as session, session.begin():
            row = session.get(RepairRequest, request_id)
            row.status = "auditing"
            row.candidate_recipe_id = original
            row.context = {
                **context,
                "bundle_ids": [],
                "task_id": "a" * 32,
                "revision_id": "b" * 32,
            }
        asyncio.run(advance(pipeline, "source", limit=50))
        with Session(engine) as session:
            assert session.get(RepairRequest, request_id).status == "superseded"
            assert session.get(SourceRecipeBinding, "source").active_recipe_id == original
    finally:
        engine.dispose()


def test_incremental_pipeline_sends_request_and_coalesces_notices(tmp_path):
    from test_onboarding import notice, table

    from youju.models import RepairIncident, ScanPolicy
    from youju.onboarding.lifecycle import set_incremental
    from youju.onboarding.recipes import RecipeError

    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session, session.begin():
            set_incremental(session, "source", True)
            for policy in session.scalars(select(ScanPolicy)):
                policy.next_scan_at = utc_now() - timedelta(seconds=1)
        body, media = transport.pages[BASE]
        transport.pages[BASE] = (
            body.replace(b"</body>", b'<a href="/new">new recruitment</a></body>'),
            media,
        )
        transport.pages[BASE + "new"] = (notice(table("新增岗位")), media)
        original = pipeline.runner.run

        async def failing(candidate, bundle, **kwargs):
            if bundle["url"] == BASE + "new":
                raise RecipeError("DRAFT_INVALID", "Synthetic new-notice parser failure")
            return await original(candidate, bundle, **kwargs)

        pipeline.runner.run = failing
        for _ in range(8):
            asyncio.run(pipeline.step("source", limit=50))
            if store.status("source")["repairs"]:
                break
        with Session(engine) as session:
            request = session.scalar(select(RepairRequest))
            assert request is not None and request.status == "queued"
            incident = session.scalar(select(RepairIncident))
            run = session.get(IncrementalRun, incident.incremental_run_id)
            assert run.summary["repair_request_ids"] == [request.id]
            assert session.get(SourcePipeline, "source").stage == "active"
        # A second saved failed notice joins the queued request, not another Agent task.
        run, summary = failed_incremental(engine, store)
        assert enqueue(pipeline, run, summary) == [request.id]
        with Session(engine) as session:
            assert len(list(session.scalars(select(RepairRequest)))) == 1
            assert len(list(session.scalars(select(RepairIncident)))) == 2
    finally:
        engine.dispose()


def test_script_exception_routing_does_not_repair_sandbox_infrastructure():
    from youju.onboarding.sandbox import SandboxError, ScriptExecutionError, child_failure

    assert isinstance(
        child_failure(
            {"operation": "script"},
            {"error_type": "KeyError", "error": "missing field"},
        ),
        ScriptExecutionError,
    )
    for operation, kind in [
        ("material", "ValueError"),
        ("script", "PermissionError"),
        ("script", "MemoryError"),
        ("probe", "NameError"),
    ]:
        error = child_failure({"operation": operation}, {"error_type": kind})
        assert type(error) is SandboxError


def test_rejected_candidate_feedback_creates_fresh_repair_and_preserves_admission(tmp_path):
    from youju.onboarding.repair import request_repair

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            binding = session.get(SourceRecipeBinding, "source")
            base_id = binding.active_recipe_id
            candidate = json.loads(store.blobs.read(session.get(SourceRecipe, base_id).body_ref))
            saved = {
                r.bundle_id: copy.deepcopy(r.result) for r in session.scalars(select(AuditResult))
            }
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
        run, summary = failed_incremental(engine, store)
        request_id = enqueue(pipeline, run, summary)[0]
        candidate["parser_py"] += "\n# Synthetic first repair\n"
        rpc = FakeRPC(store, candidate, candidate_digest(candidate))
        pipeline.recipe_rpc = rpc
        actual_validate = pipeline.runner.validate

        class Runner:
            validate = staticmethod(actual_validate)
            regress = True

            async def run(self, candidate, bundle, **kwargs):
                result = copy.deepcopy(saved[bundle["id"]])
                if self.regress and bundle["url"] == BASE + "one":
                    result.update(
                        status="failed",
                        positions=[],
                        errors=[
                            {
                                "code": "SYNTHETIC_REGRESSION",
                                "message": "Synthetic source adapter regression",
                                "failure_code": "DRAFT_INVALID",
                            }
                        ],
                    )
                if result["status"] == "failed":
                    result["execution"] = {
                        **result["execution"],
                        "failure": {
                            "code": "DRAFT_INVALID",
                            "origin": "script_draft",
                            "evidence_digest": "a" * 64,
                        },
                    }
                return result

        runner = Runner()
        pipeline.runner = runner
        for _ in range(12):
            asyncio.run(advance(pipeline, "source", limit=50))
            if store.status("source")["repairs"][0]["status"] == "failed":
                break
        previous = store.status("source")["repairs"][0]
        assert previous["id"] == request_id and previous["status"] == "failed"
        requested = request_repair(store, "source", previous_request_id=request_id)
        retry_id = requested["repairs"][0]["id"]
        assert retry_id != request_id and requested["stage"] == "active"
        with Session(engine) as session:
            retry = session.get(RepairRequest, retry_id)
            previous_row = session.get(RepairRequest, request_id)
            assert retry.base_recipe_id == previous_row.candidate_recipe_id
            assert retry.context["base_revision_id"] == previous_row.context["revision_id"]
            assert retry.context["baseline_recipe_id"] == base_id
            feedback = json.loads(store.blobs.read(retry.feedback_ref))
            assert len(feedback["baseline_parsed"]) == 3
        runner.regress = False
        next_candidate = copy.deepcopy(candidate)
        next_candidate["parser_py"] += "\n# Synthetic feedback-driven repair\n"
        next_rpc = FakeRPC(store, next_candidate, candidate_digest(candidate))
        original_call = next_rpc.call

        async def second_revision(method, **args):
            result = await original_call(method, **args)
            if method == "GetTask":
                result["revision_id"] = "c" * 32
            elif method == "GetRecipeRevision":
                result["id"] = "c" * 32
            return result

        next_rpc.call = second_revision
        pipeline.recipe_rpc = next_rpc
        for _ in range(12):
            asyncio.run(advance(pipeline, "source", limit=50))
            if store.status("source")["repairs"][0]["status"] == "succeeded":
                break
        assert store.status("source")["repairs"][0]["status"] == "succeeded"
        starts = [args for method, args in next_rpc.calls if method == "StartRepair"]
        assert starts[0]["input_revision"] == "repair-" + retry_id
        assert starts[0]["base_revision_id"] == "b" * 32
        with Session(engine) as session:
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.get(SourceRecipeBinding, "source").active_revision_id == "c" * 32
            assert list(
                public_jobs(
                    session,
                    day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    include_partial=True,
                )
            )
    finally:
        engine.dispose()


def test_failed_initial_audit_can_repair_unaccepted_revision_without_restarting_inventory(tmp_path):
    from test_onboarding import run_recipe_worker

    from youju.onboarding.recipes import RecipeError, RecipeRunner, template_candidate
    from youju.onboarding.repair import request_repair

    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        for _ in range(10):
            state = asyncio.run(pipeline.step("source", limit=50))
            if state["stage"] == "authoring" and state["state"] == "running":
                asyncio.run(run_recipe_worker(pipeline))
            if state["stage"] == "audit":
                break
        original_candidate_id = state["candidate_recipe_id"]

        async def fail_all(*args, **kwargs):
            raise RecipeError("OUTPUT_SCHEMA", "Synthetic initial full-audit failure")

        pipeline.runner.run = fail_all
        assert asyncio.run(run_onboarding(pipeline, steps=12))[-1]["state"] == "blocked"
        assert store.status("source")["active_recipe_id"] is None
        with Session(engine) as session:
            assert session.get(OnboardingResult, "source") is None
            candidate = json.loads(
                store.blobs.read(session.get(SourceRecipe, original_candidate_id).body_ref)
            )
        requested = request_repair(store, "source")
        assert requested["stage"] == "audit"  # No new inventory/onboarding authoring phase.
        old_calls = list(transport.calls)
        candidate["parser_py"] += "\n# Synthetic initial-candidate repair\n"
        pipeline.recipe_rpc = FakeRPC(store, candidate, candidate_digest(candidate))
        trusted_runner = RecipeRunner(pipeline.sandbox, pipeline.models)
        actual_validate = trusted_runner.validate

        class Runner:
            validate = staticmethod(actual_validate)

            async def run(self, candidate, bundle, **kwargs):
                return await trusted_runner.run(template_candidate(), bundle, **kwargs)

        pipeline.runner = Runner()
        for _ in range(12):
            asyncio.run(pipeline.step("source", limit=50))
            if store.status("source")["repairs"][0]["status"] == "succeeded":
                break
        state = store.status("source")
        assert state["stage"] == "active" and state["repairs"][0]["status"] == "succeeded"
        assert transport.calls == old_calls
        with Session(engine) as session:
            binding = session.get(SourceRecipeBinding, "source")
            assert binding.active_revision_id == "b" * 32
            frozen = session.get(OnboardingResult, "source")
            assert frozen.recipe_id == binding.active_recipe_id
            assert frozen.summary["audit"]["passed"] and "error" not in frozen.summary
    finally:
        engine.dispose()
