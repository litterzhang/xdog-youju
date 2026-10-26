"""Source-wide archived admission and later reopening, using actual SDK Flow and SQLite."""

import asyncio
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_onboarding import (
    BASE,
    SyntheticSandbox,
    fixture_pages,
    notice,
    run_onboarding,
    setup_source,
    table,
)

from youju.jobs import public_jobs
from youju.models import (
    AuditResult,
    IncrementalControl,
    OnboardingResult,
    Position,
    ScanPolicy,
    SourcePipeline,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.models.onboarding import SemanticAuditCheck, SemanticCaseLabel
from youju.onboarding.auditing import AuditLedger, admission_quality
from youju.onboarding.recipes import RecipeError, RecipeRunner, template_candidate


class AccuracySandbox(SyntheticSandbox):
    def __init__(self):
        self.empty = True

    async def run_script(self, files, module, function, inputs):
        draft = await super().run_script(files, module, function, inputs)
        if self.empty:
            draft["positions"] = []
            draft["coverage"].update(supported=False, complete=False, needs_detailed=True)
            draft["issues"].append(
                {
                    "code": "UNSUPPORTED_POSITION_LAYOUT",
                    "message": "Synthetic unsupported table layout",
                }
            )
        return draft


def accuracy_author(task, payload, schema, tier):
    assert task in {"author_source_recipe", "repair_source_recipe"}, (
        "Archived notices must not call detailed AI"
    )
    return template_candidate(accuracy=True)


def test_archived_source_with_zero_positions_can_onboard_reassess_and_reopen(tmp_path):
    pages = fixture_pages()
    for suffix in ("one", "two", "three"):
        pages[BASE + suffix] = (notice(table(), expired=True), "text/html; charset=utf-8")
    engine, store, transport, pipeline = setup_source(
        tmp_path, pages=pages, sandbox=AccuracySandbox(), stub=accuracy_author
    )
    pipeline.recipe_coordinator.accuracy = True
    try:
        states = asyncio.run(run_onboarding(pipeline))
        assert states[-1]["stage"] == "active", states[-1]
        summary = states[-1]["summary"]["audit"]
        assert summary["passed"] and summary["position_count"] == 0
        assert summary["archived_announcements"] == 3
        assert summary["admission"]["mode"] == "quick_archives_only"
        assert summary["admission"]["successful_announcements"] == 0
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Position)) == 0
            assert not list(
                public_jobs(session, day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date())
            )
            frozen = session.get(OnboardingResult, "source").summary
            old_generation = session.get(SourceRecipeBinding, "source").generation
        calls = list(transport.calls)
        reassessed = AuditLedger(store).reassess("source")
        assert reassessed["binding"]["generation"] > old_generation
        assert reassessed["summary"]["audit"]["admission"]["mode"] == "quick_archives_only"
        assert transport.calls == calls
        with Session(engine) as session:
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.scalar(select(func.count()).select_from(Position)) == 0
        from datetime import timedelta

        from youju.models import IncrementalRun
        from youju.onboarding.incremental_view import run_detail

        transport.pages[BASE + "two"] = (
            notice(table("Changed historical role"), expired=True),
            "text/html; charset=utf-8",
        )
        with Session(engine) as session, session.begin():
            for policy in session.scalars(select(ScanPolicy)):
                policy.next_scan_at = utc_now() - timedelta(seconds=1)
        asyncio.run(pipeline.incremental("source", limit=50, scheduled=False))
        with Session(engine) as session:
            run = session.scalar(select(IncrementalRun).order_by(IncrementalRun.started_at.desc()))
            view = run_detail(session, run)
            assert view["summary"]["delta"]["position_count"] == 0
            assert view["notices"]["items"]
            assert all(item["extraction_path"] == "quick" for item in view["notices"]["items"])
        # Replace saved material through the existing async acquisition path, not by
        # relabeling an archived result. No detailed provider is available.
        transport.pages[BASE + "two"] = (notice(table(), expired=False), "text/html; charset=utf-8")
        with Session(engine) as session, session.begin():
            for policy in session.scalars(select(ScanPolicy)):
                policy.next_scan_at = utc_now() - timedelta(seconds=1)
        store.queue_due("source")
        asyncio.run(pipeline.worker.run_once("source", 50))
        store.build_bundles("source")
        reopened = next(b for b in store.bundles("source") if b["url"] == BASE + "two")
        runner = RecipeRunner(pipeline.sandbox)
        with pytest.raises(RecipeError) as failure:
            asyncio.run(
                runner.run(
                    template_candidate(accuracy=True), reopened, now_iso=utc_now().isoformat()
                )
            )
        # Empty full scripts must reach a real detailed boundary, not inherit coarse.
        assert failure.value.code == "MODEL_DISABLED"
        with Session(engine) as session:
            assert not list(
                public_jobs(session, day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date())
            )
            assert all(
                row.result["execution"]["path"] == "quick"
                for row in session.scalars(select(AuditResult))
            )
        # A genuinely supported full path and a NEW full-source audit can publish
        # reopened jobs. Neither changing a label nor reusing coarse output can.
        pipeline.sandbox.empty = False
        pipeline.runner.models = None
        asyncio.run(pipeline._classify("source", 50))
        with Session(engine) as session, session.begin():
            binding = session.get(SourceRecipeBinding, "source")
            state = session.get(SourcePipeline, "source")
            state.summary = dict(state.summary, incremental_generation=binding.generation + 1)
        fresh = asyncio.run(pipeline._audit("source", limit=50, activate=False))
        assert fresh["passed"] and fresh["position_count"] == 3
        assert fresh["extraction_paths"] == {"quick": 2, "detailed": 1}
        with Session(engine) as session:
            visible = list(
                public_jobs(session, day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date())
            )
            assert len(visible) == 3
            assert sum(row["extraction_path"] == "detailed" for row in visible) == 1
            assert sum("expired" in row["display_tags"] for row in visible) == 2
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.get(IncrementalControl, "source").enabled is False
            # Optional independent labels must not become a per-source admission
            # prerequisite: archived onboarding and reopened full publication above
            # both complete automatically, without inventing semantic gold/receipts.
            assert session.scalar(select(func.count()).select_from(SemanticCaseLabel)) == 0
            assert session.scalar(select(func.count()).select_from(SemanticAuditCheck)) == 0
    finally:
        engine.dispose()


def test_archival_admission_never_excuses_failed_unknown_route_or_unvalidated_results():
    row = {
        "schema_version": "position-facts-v3",
        "runtime_contract": "recipe-runtime-v3",
        "status": "partial",
        "errors": [],
        "position_count": 0,
        "extraction_path": "quick",
        "routing_category": "fixed_expired",
    }
    assert admission_quality([row], 1)[2]
    for change in (
        {"status": "failed"},
        {"routing_category": "unknown"},
        {"runtime_contract": "old-contract"},
        {"errors": [{"code": "FAILURE"}]},
        {"extraction_path": "detailed"},
    ):
        assert not admission_quality([{**row, **change}], 1)[2]
    assert not admission_quality([], 0)[2]
    assert not admission_quality([row], 2)[2]
