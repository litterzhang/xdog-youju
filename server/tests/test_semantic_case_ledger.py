"""Host-reviewed semantic cases gate actual audits, repair verdicts and public rows."""
# ruff: noqa: F811 -- imported pytest fixture names are injected as test arguments.

import copy
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_recipe_binding import DAY, saved  # noqa: F401, F811

from youju.jobs import public_jobs
from youju.models import AuditResult, SourceRecipeBinding
from youju.models.onboarding import SemanticCaseExposure, SemanticCaseLabel
from youju.onboarding.auditing import AuditLedger
from youju.onboarding.repair import repair_verdict
from youju.onboarding.semantic_labels import (
    author_visible,
    effective_split,
    expose,
    latest_label,
    register_label,
    validate_expected,
)
from youju.onboarding.store import json_bytes
from youju.positions.facts import POSITION_FIELDS


def expectation(position, *, exhaustive=True, complete=True):
    fields = (
        {key: copy.deepcopy(position[key]) for key in POSITION_FIELDS}
        if exhaustive
        else {"headcount": copy.deepcopy(position["headcount"])}
    )
    item = {
        "selector": {
            "title": position["title"]["value"],
            "hiring_unit": position["hiring_unit"]["value"],
        },
        "fields": fields,
    }
    if exhaustive:
        item.update(
            conditions=copy.deepcopy(position["conditions"]),
            condition_logic=copy.deepcopy(position["condition_logic"]),
        )
    return {
        "basis": "human-confirmed",
        "reference": "fixture-row:reviewed",
        "positions": [item],
        "complete_position_set": complete,
    }


def known(result):
    result = copy.deepcopy(result)
    result["positions"][0]["hiring_unit"] = {"value": "Fixture University", "tag": "source"}
    result["positions"][0]["headcount"] = {"value": 2, "tag": "source"}
    return result


def feedback(store, before):
    return {
        "baseline_parsed": ["bundle"],
        "baseline_position_counts": {"bundle": len(before["positions"])},
        "selected_bundle_ids": ["bundle"],
        "baseline_semantics": {
            "bundle": store.blobs.put(json_bytes({"bundle_id": "bundle", "result": before}))
        },
    }


@pytest.mark.parametrize("path", ["quick", "detailed"])
def test_reviewed_full_case_allows_false_job_removal_and_rejects_same_count_bad_fields(saved, path):
    engine, store, material, original = saved
    good = known(original)
    good["execution"]["path"] = path
    good["routing"].update(
        category="fixed_expired" if path == "quick" else "unknown",
        mode=path,
    )
    bad = copy.deepcopy(good)
    fake = copy.deepcopy(good["positions"][0])
    fake["title"]["value"] = "False extra job"
    bad["positions"].append(fake)
    ledger = AuditLedger(store)
    baseline = ledger.record(material, "active", DAY.isoformat(), bad, generation=8)
    assert baseline["status"] == "success" and baseline["position_count"] == 2
    label = register_label(
        store, "source", "bundle", expectation(good["positions"][0]), reviewer="fixture-reviewer"
    )
    candidate = ledger.record(material, "candidate", DAY.isoformat(), good, generation=9)
    verdict = repair_verdict(feedback(store, bad), [candidate], complete=True, store=store)
    assert verdict["passed"] and verdict["baseline_overrides"] == {"bundle": label["label_id"]}
    wrong = copy.deepcopy(good)
    wrong["positions"][0]["headcount"]["value"] = 9
    rejected = ledger.record(material, "candidate", DAY.isoformat(), wrong, generation=10)
    assert rejected["status"] == "review"
    assert any(
        e["code"] == "SEMANTIC_EXPECTATION" and e["path"].endswith("headcount")
        for e in rejected["errors"]
    )
    assert not repair_verdict(feedback(store, good), [rejected], complete=True, store=store)[
        "passed"
    ]
    # Agent feedback or a claimed complete result cannot forge a host receipt.
    forged = dict(candidate, semantic_validation={"status": "passed", "label_id": "forged"})
    assert (
        repair_verdict(feedback(store, bad), [forged], complete=True, store=store)[
            "baseline_overrides"
        ]["bundle"]
        == label["label_id"]
    )


def test_partial_case_only_authorizes_its_fields_and_full_membership_not_unchecked_changes(saved):
    _, store, material, original = saved
    good = known(original)
    before = copy.deepcopy(good)
    before["positions"][0]["headcount"]["value"] = 9
    ledger = AuditLedger(store)
    register_label(
        store,
        "source",
        "bundle",
        expectation(good["positions"][0], exhaustive=False, complete=False),
        reviewer="fixture-reviewer",
    )
    repaired = ledger.record(material, "candidate", DAY.isoformat(), good, generation=9)
    verdict = repair_verdict(feedback(store, before), [repaired], complete=True, store=store)
    assert verdict["passed"] and verdict["reviewed_semantic_changes"]
    assert not verdict["baseline_overrides"]
    changed = copy.deepcopy(good)
    changed["positions"][0]["employment_type"] = {"value": "labor_dispatch", "tag": "ai_inferred"}
    extra = ledger.record(material, "candidate", DAY.isoformat(), changed, generation=10)
    assert extra["status"] == "success"  # Outside the partial gold's assertions.
    assert not repair_verdict(feedback(store, before), [extra], complete=True, store=store)[
        "passed"
    ]


def test_new_semantic_labels_tag_stale_candidates_without_mutating_history(saved):
    engine, store, _, original = saved
    with Session(engine) as session:
        assert list(public_jobs(session, day=DAY))
        audits = {row.id: copy.deepcopy(row.result) for row in session.scalars(select(AuditResult))}
    expected_position = known(original)["positions"][0]
    register_label(
        store, "source", "bundle", expectation(expected_position), reviewer="fixture-reviewer"
    )
    with Session(engine) as session:
        rows = list(public_jobs(session, day=DAY))
        assert rows and all("needs_review" in row["display_tags"] for row in rows)
        assert {row.id: row.result for row in session.scalars(select(AuditResult))} == audits
        assert session.get(SourceRecipeBinding, "source").generation == 7
    cached = AuditLedger(store).cached("bundle", "active", DAY.isoformat(), generation=7)
    assert cached["status"] == "review" and cached["semantic_validation"]["status"] == "failed"
    assert cached["errors"]


def test_invalid_gold_and_retroactive_unseen_holdouts_are_rejected(saved):
    _, store, _, result = saved
    expected = expectation(known(result)["positions"][0])
    with pytest.raises(ValueError, match="model suggestion"):
        validate_expected({**expected, "basis": "suspected-model-output"})
    with pytest.raises(ValueError, match="title AND"):
        validate_expected(
            {**expected, "positions": [{"selector": {"title": "助理"}, "fields": {}}]}
        )
    with pytest.raises(ValueError, match="retroactively"):
        register_label(
            store, "source", "bundle", expected, reviewer="fixture-reviewer", split="holdout"
        )
    registered = register_label(store, "source", "bundle", expected, reviewer="fixture-reviewer")
    repeated = register_label(store, "source", "bundle", expected, reviewer="fixture-reviewer")
    assert registered["label_id"] == repeated["label_id"]
    with Session(store.engine) as session, session.begin():
        label = latest_label(session, "bundle")
        expose(session, label, "fixture-feedback")
        expose(session, label, "fixture-feedback")
        assert effective_split(session, label) == "regression"
    assert author_visible(store, "bundle")
    with Session(store.engine) as session:
        assert len(session.scalars(select(SemanticCaseExposure)).all()) == 1
        assert len(session.scalars(select(SemanticCaseLabel)).all()) == 1


def test_hidden_holdout_is_not_in_author_samples_or_catalog_and_exposure_is_durable(tmp_path):
    import asyncio
    import json

    from test_onboarding import setup_source

    from youju.models.base import utc_now
    from youju.onboarding.author_workspace import AuthorWorkspace
    from youju.onboarding.recipes import RecipeRunner, template_candidate
    from youju.poc.storage import BlobStore
    from youju.recipe_service.cases import load_catalog

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        for _ in range(6):
            state = asyncio.run(pipeline.step("source", limit=50))
            if state["stage"] == "authoring":
                break
        assert state["stage"] == "authoring" and state["state"] == "ready"
        target = store.bundles("source", classifier_key=pipeline.classifier_key)[1]
        # A declared synthetic expectation is a host fixture, not real-site gold.
        facts = asyncio.run(
            RecipeRunner(pipeline.sandbox).run(
                template_candidate(),
                target,
                now_iso=utc_now().isoformat(),
            )
        )["positions"][0]
        facts["hiring_unit"] = {"value": "Fixture University", "tag": "source"}
        registered = register_label(
            store,
            "source",
            target["id"],
            expectation(facts),
            reviewer="fixture-reviewer",
            split="holdout",
        )
        assert not author_visible(store, target["id"])
        state = asyncio.run(pipeline.step("source", limit=50))
        pending = state["summary"]["authoring_job"]
        workspace = AuthorWorkspace(store.blobs.root / "authoring" / pending["workspace_id"])
        payload = json.loads((workspace.input / "payload.json").read_bytes())
        assert target["id"] not in {sample["id"] for sample in payload["samples"]}
        catalog = load_catalog(
            BlobStore(workspace.root.parent / "objects"), payload["case_catalog_ref"], "source"
        )
        assert target["id"] not in {case["case_id"] for case in catalog["cases"]}
        with Session(engine) as session, session.begin():
            label = session.get(SemanticCaseLabel, registered["label_id"])
            expose(session, label, "explicit-fixture-feedback")
        assert author_visible(store, target["id"])
        with Session(engine) as session:
            assert effective_split(session, latest_label(session, target["id"])) == "regression"
    finally:
        engine.dispose()


def test_reviewed_semantic_review_runs_incremental_repair_through_audit_and_adoption(tmp_path):
    import asyncio
    import json

    from test_auto_repair import FakeRPC
    from test_onboarding import BASE, run_onboarding, setup_source

    from youju.models import (
        AuditRun,
        FetchAttempt,
        IncrementalRun,
        OnboardingResult,
        RepairRequest,
        SourcePipeline,
        SourceRecipe,
    )
    from youju.models.base import utc_now
    from youju.onboarding.auto_repair import advance, enqueue
    from youju.onboarding.recipes import candidate_digest

    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        bundles = store.bundles("source", classifier_key=pipeline.classifier_key)
        target = next(b for b in bundles if b["url"] == BASE + "two")
        with Session(engine) as session:
            binding = session.get(SourceRecipeBinding, "source")
            recipe_id, generation = binding.active_recipe_id, binding.generation + 1
            base = json.loads(store.blobs.read(session.get(SourceRecipe, recipe_id).body_ref))
            rows = session.scalars(
                select(AuditResult).where(AuditResult.recipe_id == recipe_id)
            ).all()
            outputs = {row.bundle_id: copy.deepcopy(row.result) for row in rows}
            summary = copy.deepcopy(session.get(SourcePipeline, "source").summary["audit"])
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
            fetches = list(session.scalars(select(FetchAttempt.id)))
        good = known(outputs[target["id"]])
        outputs[target["id"]] = good
        register_label(
            store,
            "source",
            target["id"],
            expectation(good["positions"][0]),
            reviewer="fixture-reviewer",
        )
        observed = []
        ledger = AuditLedger(store)
        for bundle in bundles:
            if bundle["id"] not in outputs:
                continue
            output = copy.deepcopy(outputs[bundle["id"]])
            if bundle["id"] == target["id"]:
                output["positions"][0]["headcount"]["value"] = 9
            observed.append(
                ledger.record(
                    bundle,
                    recipe_id,
                    utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                    output,
                    generation=generation,
                )
            )
        item = next(o for o in observed if o["bundle_id"] == target["id"])
        assert item["status"] == "review" and item["semantic_validation"]["status"] == "failed"
        with Session(engine) as session, session.begin():
            session.get(SourceRecipeBinding, "source").generation = generation
            active_run = AuditRun(
                source_id="source",
                recipe_id=recipe_id,
                kind="incremental",
                status="complete",
                finished_at=utc_now(),
                summary={**summary, "results": observed, "generation": generation},
            )
            session.add(active_run)
            run = IncrementalRun(source_id="source", baseline={})
            session.add(run)
            session.flush()
            run_id = run.id
        requested = enqueue(pipeline, run_id, {"results": observed})
        assert len(requested) == 1 and enqueue(pipeline, run_id, {"results": observed}) == []
        candidate = copy.deepcopy(base)
        candidate["parser_py"] += "\n# Synthetic Agent repair fixture\n"
        rpc = FakeRPC(store, candidate, candidate_digest(base))
        pipeline.recipe_rpc = rpc
        old_runner = pipeline.runner

        class RepairedRunner:
            validate = staticmethod(old_runner.validate)

            async def run(self, candidate, bundle, **kwargs):
                return copy.deepcopy(outputs[bundle["id"]])

        pipeline.runner = RepairedRunner()
        for _ in range(10):
            asyncio.run(advance(pipeline, "source", limit=50))
            with Session(engine) as session:
                request = session.get(RepairRequest, requested[0])
                if request.status in {"succeeded", "failed", "superseded"}:
                    break
        with Session(engine) as session:
            request = session.get(RepairRequest, requested[0])
            assert request.status == "succeeded", (request.error_code, request.context)
            assert (
                session.get(SourceRecipeBinding, "source").active_recipe_id
                == request.candidate_recipe_id
            )
            assert session.get(OnboardingResult, "source").summary == frozen
            assert list(session.scalars(select(FetchAttempt.id))) == fetches
        assert not any(method == "CreateWorkspace" for method, _ in rpc.calls)
        assert transport.calls  # Initial synthetic acquisition only, no real external fetch.
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", ["operator", "scope", "renamed_ids"])
def test_audit_semantic_conditions_preserve_all_any_and_scope_without_requiring_same_ids(
    saved, change
):
    _, store, material, original = saved
    good = known(original)
    good["positions"][0].update(
        conditions=[
            {
                "id": "education",
                "type": "education",
                "operator": "at_least",
                "scope": "notice",
                "value": {"level": "bachelor"},
                "tag": "source",
            },
            {
                "id": "degree",
                "type": "degree",
                "operator": "equals",
                "scope": "position",
                "value": {"level": "master"},
                "tag": "source",
            },
        ],
        condition_logic={
            "operator": "all",
            "items": [{"condition_id": "education"}, {"condition_id": "degree"}],
        },
    )
    register_label(
        store, "source", "bundle", expectation(good["positions"][0]), reviewer="fixture-reviewer"
    )
    actual = copy.deepcopy(good)
    position = actual["positions"][0]
    if change == "operator":
        position["condition_logic"]["operator"] = "any"
    elif change == "scope":
        position["conditions"][0]["scope"] = "position"
    else:
        position["conditions"][0]["id"] = "renamed-education"
        position["condition_logic"]["items"][0]["condition_id"] = "renamed-education"
        position["condition_logic"]["items"].reverse()
    outcome = AuditLedger(store).record(
        material, "candidate", DAY.isoformat(), actual, generation=9
    )
    assert outcome["status"] == ("success" if change == "renamed_ids" else "review")
    if change != "renamed_ids":
        assert any(e.get("semantic_code") == "EXPECTED_CONDITIONS" for e in outcome["errors"])


def test_human_label_cli_requires_confirmation_and_never_queues_a_model(saved, tmp_path):
    import json

    from youju import create_app
    from youju.models import RepairRequest

    engine, store, _, original = saved
    path = tmp_path / "reviewed.json"
    expected = expectation(known(original)["positions"][0])
    path.write_text(json.dumps(expected))
    app = create_app({"TESTING": True})
    runner = app.test_cli_runner()
    args = [
        "source-semantic-label",
        "source",
        "bundle",
        "--expected",
        str(path),
        "--reviewer",
        "fixture-reviewer",
        "--database",
        engine.url.database,
        "--storage",
        str(store.blobs.root),
    ]
    assert runner.invoke(args=args).exit_code != 0
    result = runner.invoke(args=[*args, "--confirm-reviewed"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["queued_model_tasks"] == 0 and receipt["published"] is False
    with Session(engine) as session:
        assert not session.scalars(select(RepairRequest)).all()
        assert session.get(SourceRecipeBinding, "source").generation == 7
    path.write_text(json.dumps({**expected, "basis": "suspected-model-output"}))
    assert runner.invoke(args=[*args, "--confirm-reviewed"]).exit_code != 0
    with pytest.raises(ValueError, match="empty partial"):
        validate_expected({"basis": "human-confirmed", "reference": "fixture", "positions": []})


def test_review_diagnosis_rejects_fake_semantic_claims_provider_and_material_problems(saved):
    from youju.models import ResourceBundle
    from youju.onboarding.repair_diagnostics import diagnose_row

    engine, store, _, original = saved
    with Session(engine) as session:
        row = session.scalar(select(AuditResult).where(AuditResult.generation == 7))
        row.errors = [{"code": "SEMANTIC_EXPECTATION", "expected_basis": "human-confirmed"}]
        assert not diagnose_row(session, row)["repairable"]  # A model cannot mint a host receipt.
    register_label(
        store,
        "source",
        "bundle",
        expectation(known(original)["positions"][0]),
        reviewer="fixture-reviewer",
    )
    with Session(engine) as session:
        row = session.scalar(select(AuditResult).where(AuditResult.generation == 7))
        assert diagnose_row(session, row)["repairable"]  # Independent case confirms the mismatch.
        row.errors = [{"code": "MODEL_TOKEN_LIMIT"}]
        assert diagnose_row(session, row)["category"] == "provider_or_budget"
        assert not diagnose_row(session, row)["repairable"]
        row.errors = [{"code": "SEMANTIC_EXPECTATION"}]
        session.get(ResourceBundle, "bundle").status = "partial"
        # Acquisition completeness alone cannot erase a confirmed mismatch in
        # readable saved facts. An actual blocking material error is separate.
        assert diagnose_row(session, row)["repairable"]
        assert diagnose_row(session, row)["category"] == "semantic"
        row.errors.append({"code": "MATERIAL_UNREADABLE", "message": "Synthetic material failure"})
        assert not diagnose_row(session, row)["repairable"]
        assert diagnose_row(session, row)["category"] == "material"


def test_rejected_original_is_not_replaced_by_empty_placeholder_or_mislabeled_adapter_bug(saved):
    from youju.onboarding.diagnostics import failed_result, failure_record
    from youju.onboarding.recipes import RecipeError
    from youju.onboarding.repair_diagnostics import diagnose_row

    engine, store, material, original = saved
    good = known(original)
    register_label(
        store, "source", "bundle", expectation(good["positions"][0]), reviewer="fixture-reviewer"
    )
    error = RecipeError(
        "RESULT_INVALID",
        "Synthetic host row-coverage conflict",
        errors=[{"code": "OMITTED_POSITION_ROW", "message": "Synthetic false-positive row"}],
        rejected_result=good,
        routing=good["routing"],
        execution=good["execution"],
    )
    evidence = failure_record(
        material, "candidate", None, error, bindings={"recipe_id": "candidate", "generation": 9}
    )
    reference = store.blobs.put(json_bytes(evidence))
    failed, details = failed_result(error, failure_ref=reference)
    assert failed["routing"] == good["routing"]
    assert failed["execution"] == good["execution"]
    outcome = AuditLedger(store).record(
        material, "candidate", DAY.isoformat(), failed, details, 9, failure_evidence=evidence
    )
    assert outcome["status"] == "failed" and outcome["position_count"] == 1
    assert outcome["semantic_validation"]["status"] == "passed"
    with Session(engine) as session:
        row = session.get(AuditResult, outcome["id"])
        diagnosis = diagnose_row(session, row)
        assert not diagnosis["repairable"] and diagnosis["category"] == "host_validation"
    unavailable = RecipeError(
        "MODEL_DISABLED", "No model", routing=good["routing"], execution=good["execution"]
    )
    evidence = failure_record(
        material,
        "candidate",
        None,
        unavailable,
        bindings={"recipe_id": "candidate", "generation": 10},
    )
    failed, details = failed_result(unavailable)
    outcome = AuditLedger(store).record(
        material, "candidate", DAY.isoformat(), failed, details, 10, failure_evidence=evidence
    )
    assert outcome["status"] == "failed"
    assert outcome["semantic_validation"]["status"].startswith("not_evaluated_")
    assert not any(error["code"] == "SEMANTIC_EXPECTATION" for error in outcome["errors"])


@pytest.mark.parametrize("change", ["unknown_logic", "invented_all", "lost_atom"])
def test_reviewed_unknown_logic_preserves_typed_atoms_without_inventing_and(saved, change):
    _, store, material, original = saved
    good = known(original)
    good["positions"][0].update(
        conditions=[
            {
                "id": "education",
                "type": "education",
                "operator": "at_least",
                "scope": "position",
                "value": {"level": "bachelor"},
                "tag": "source",
            },
            {
                "id": "degree",
                "type": "degree",
                "operator": "equals",
                "scope": "position",
                "value": {"level": "master"},
                "tag": "source",
            },
        ],
        condition_logic=None,
    )
    # Independent review confirms the atoms but cannot resolve their relationship.
    # A historical script's implicit AND is not gold for this case.
    register_label(
        store, "source", "bundle", expectation(good["positions"][0]), reviewer="fixture-reviewer"
    )
    actual = copy.deepcopy(good)
    if change == "invented_all":
        actual["positions"][0]["condition_logic"] = {
            "operator": "all",
            "items": [{"condition_id": "education"}, {"condition_id": "degree"}],
        }
    elif change == "lost_atom":
        actual["positions"][0]["conditions"].pop()
    outcome = AuditLedger(store).record(
        material, "candidate", DAY.isoformat(), actual, generation=9
    )
    assert outcome["position_count"] == 1
    assert outcome["status"] == ("success" if change == "unknown_logic" else "review")
    if change == "unknown_logic":
        assert outcome["semantic_validation"]["status"] == "passed"
        assert actual["positions"][0]["conditions"] == good["positions"][0]["conditions"]
        assert actual["positions"][0]["condition_logic"] is None
    else:
        assert any(e.get("semantic_code") == "EXPECTED_CONDITIONS" for e in outcome["errors"])
