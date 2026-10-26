"""Bound rejected outputs to actual files/material and keep tool replies paginated."""

import asyncio
import copy
import json

import pytest
from test_recipe_accuracy import NOW, SandboxDouble, material
from test_xdog_recipes import cost_route

from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.diagnostics import failure_record, failure_summary, page_record
from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, template_candidate
from youju.onboarding.xdog import ModelError, XdogModels
from youju.poc.storage import BlobStore
from youju.positions.drafts import DRAFT_SCHEMA
from youju.positions.parsing import extract_position_draft, extract_position_facts
from youju.recipe_service.cases import (
    CaseAccess,
    freeze_cases,
    load_catalog,
    validate_failure_inputs,
)
from youju.recipe_service.validation_progress import ValidationProgress


def test_failed_full_script_is_preserved_before_disabled_detailed_fallback(tmp_path):
    async def scenario():
        class IncompleteScript(SandboxDouble):
            async def run_script(self, files, module, function, inputs):
                value = await super().run_script(files, module, function, inputs)
                value["positions"][0]["fields"]["title"]["value"] = "Retained script title"
                value["coverage"]["needs_detailed"] = True
                self.draft = copy.deepcopy(value)
                return value

        blobs = BlobStore(tmp_path / "objects")
        bundle = material()
        catalog = freeze_cases(blobs, "source-1", [bundle])
        sandbox = IncompleteScript()
        workspace = AuthorWorkspace(tmp_path / "workspace", sandbox=sandbox)
        candidate = template_candidate()
        workspace.prepare({}, skill_text="skill", guide_text="guide", template=candidate)
        access = CaseAccess(
            blobs,
            catalog_ref=catalog,
            source_id="source-1",
            input_revision="r1",
            now_iso=NOW,
            model_profile_digest="profile",
            workspace=workspace,
            author=RecipeAuthor(None, RecipeRunner(sandbox)),
        )
        tested = await access.test_recipe([bundle["id"]])
        report = access.inspect_failure(tested["test_id"])
        assert report["issues"][0]["code"] == "MODEL_DISABLED"
        failure = report["failures"][0]
        assert failure["rejected_position_count"] == 1 and failure["rejected_output_available"]
        reference = "failure:" + failure["failure_id"]
        retained = access.inspect_failure(reference, section="rejected_output")
        assert json.loads(retained["untrusted_text"]) == sandbox.draft
        assert retained["rejected_origin"] == "script_draft"
        assert (
            retained["bindings"]["case_version"]
            == load_catalog(blobs, catalog, "source-1")["cases"][0]["body_ref"]
        )
        record = workspace.load_failure(failure["failure_id"])
        assert record["rejected_origin"] == "script_draft"
        assert record["rejected_output"]["value"]["schema_version"] == "position-draft-v1"
        assert record["routing"]["mode"] == "detailed"
        assert "validated_validity" not in record
        # A new task cannot claim this diagnostic as one of its own tests.
        other = CaseAccess(
            blobs,
            catalog_ref=catalog,
            source_id="source-1",
            input_revision="r2",
            now_iso=NOW,
            model_profile_digest="profile",
            workspace=workspace,
            author=RecipeAuthor(None, RecipeRunner(sandbox)),
        )
        with pytest.raises(ValueError, match="not bound"):
            other.inspect_failure(reference, section="rejected_output")
        assert (await access.test_recipe([bundle["id"]]))["cached"]

    asyncio.run(scenario())


def test_failure_pages_retain_large_fields_and_show_absent_output_honestly(tmp_path):
    bundle = material()
    result = extract_position_facts(bundle)
    result["positions"][0]["requirements_text"] = {"value": "条件" * 9000, "tag": "source"}
    error = RecipeError(
        "RESULT_INVALID",
        "Fixture failure",
        rejected_result=result,
        errors=[{"code": "FIELD", "message": "detail", "actual": "字" * 80000}],
    )
    record = failure_record(bundle, "candidate", NOW, error)
    first = page_record(record)
    assert first["issues"][0]["issue_index"] == 0
    text, cursor = "", 0
    while True:
        page = page_record(record, section="issue", issue_index=0, cursor=cursor)
        text += page["untrusted_text"]
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert json.loads(text)["actual"] == "字" * 80000
    output, cursor = "", 0
    while True:
        page = page_record(record, section="rejected_output", cursor=cursor)
        output += page["untrusted_text"]
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert json.loads(output) == result
    assert not record["rejected_output"]["truncated"]
    absent = failure_record(bundle, "candidate", NOW, RecipeError("MODEL_DISABLED", "No model"))
    assert page_record(absent, section="rejected_output")["available"] is False


def test_imported_source_failures_are_case_scoped_and_not_arbitrary_blob_reads(tmp_path):
    bundle = material()
    blobs = BlobStore(tmp_path / "objects")
    catalog_ref = freeze_cases(blobs, "source-1", [bundle])
    catalog = load_catalog(blobs, catalog_ref, "source-1")
    record = failure_record(
        bundle,
        "candidate",
        NOW,
        RecipeError("RESULT_INVALID", "Fixture", rejected_result=extract_position_facts(bundle)),
    )
    reference = blobs.put(json.dumps(record).encode())
    assert validate_failure_inputs(blobs, catalog, {bundle["id"]: [reference]})
    changed = copy.deepcopy(record)
    changed["bindings"]["source_id"] = "other-source"
    wrong = blobs.put(json.dumps(changed).encode())
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_failure_inputs(blobs, catalog, {bundle["id"]: [wrong]})
    with pytest.raises(ValueError, match="authorized case"):
        validate_failure_inputs(blobs, catalog, {"another-case": [reference]})


def test_progress_uses_defects_and_row_coverage_not_file_edits_or_total_turns():
    def attempt(rows=1, issue="fixed-defect"):
        return {
            "errors": [{"code": "POSITION_DRAFT_SCHEMA", "path": "positions"}],
            "samples": [],
            "failures": [
                {
                    "case_id": "case",
                    "error_code": "DRAFT_INVALID",
                    "stage": "extraction_validation",
                    "issue_digest": issue,
                    "rejected_position_count": rows,
                }
            ],
        }

    tracker = ValidationProgress()
    assert not tracker.observe(attempt())["stop"]
    assert not tracker.observe(attempt(2))["stop"]  # More known rows is observable progress.
    assert tracker.observe(attempt(2))["reason"] == "unchanged_confirmed_defects"
    restored = ValidationProgress(copy.deepcopy(tracker.state))
    assert restored.observe(attempt())["reason"] == "oscillating_defects"
    fresh = ValidationProgress()
    for n in range(60):
        assert not fresh.observe(attempt(n, f"new-confirmed-diagnostic-{n}"))["stop"]
    assert fresh.state["observations"] == 60 and len(fresh.state["recent"]) == 32


def test_malformed_draft_is_untrusted_diagnostic_with_routing_separate():
    async def scenario():
        class InvalidDraft(SandboxDouble):
            async def run_script(self, files, module, function, inputs):
                result = await super().run_script(files, module, function, inputs)
                # Inconsistent structured date, not disagreement with an extractor.
                result["notice_facts"]["dates"]["end"]["date"] = "2000-01-01"
                self.draft = copy.deepcopy(result)
                return result

        bundle, routing, sandbox = material(), cost_route("detailed"), InvalidDraft()
        with pytest.raises(RecipeError) as failure:
            await RecipeRunner(sandbox).run(
                template_candidate(), bundle, now_iso=NOW, routing=routing
            )
        error = failure.value
        assert error.code == "DRAFT_INVALID"
        assert error.rejected_origin == "script_draft"
        assert error.rejected_result == sandbox.draft
        assert error.rejected_result["schema_version"] == "position-draft-v1"
        assert error.rejected_result["notice_facts"]["dates"]["end"]["date"] == "2000-01-01"
        record = failure_record(bundle, "candidate", NOW, error)
        assert record["routing"] == routing
        assert "validated_validity" not in record
        assert "routing" not in record["rejected_output"]["value"]
        assert failure_summary(record)["rejected_position_count"] == 1
        assert json.loads(page_record(record, section="rejected_output")["untrusted_text"]) == (
            sandbox.draft
        )
        routing["mode"] = "quick"
        assert record["routing"]["mode"] == error.routing["mode"] == "detailed"

    asyncio.run(scenario())


def test_model_rejected_payload_is_private_not_generic_error_metadata():
    async def scenario():
        sentinel = {"private_fixture_result": "Do not put this in generic errors"}
        models = XdogModels(stub=lambda *_: sentinel)
        with pytest.raises(ModelError) as failure:
            await models.complete("extract_position_draft", {}, DRAFT_SCHEMA)
        assert failure.value.code == "OUTPUT_SCHEMA"
        assert failure.value.rejected_output == sentinel
        assert "private_fixture_result" not in json.dumps(failure.value.as_dict())

    asyncio.run(scenario())


def test_model_schema_rejection_keeps_actual_submission_and_raw_position_count():
    async def scenario():
        bundle = material()
        submitted = extract_position_draft(bundle)
        submitted["private_fixture_result"] = "Actual rejected model data, not generic errors"
        submitted["positions"][0]["fields"]["title"]["value"] = "Rejected model fixture title"
        snapshot = copy.deepcopy(submitted)
        calls = []

        def complete(task, payload, schema, tier):
            calls.append(task)
            assert task == "extract_position_draft"
            assert schema == DRAFT_SCHEMA and tier == "flow"
            assert payload["previous_draft"]["schema_version"] == "position-draft-v1"
            assert payload["previous_draft"]["coverage"]["needs_detailed"] is True
            return submitted

        runner = RecipeRunner(SandboxDouble("unsupported"), XdogModels(stub=complete))
        with pytest.raises(RecipeError) as failure:
            await runner.run(
                template_candidate(), bundle, now_iso=NOW, routing=cost_route("detailed")
            )
        error = failure.value
        assert calls == ["extract_position_draft"]
        assert error.code == "OUTPUT_SCHEMA"
        assert error.rejected_origin == "model_submission"
        assert error.rejected_result == snapshot
        assert "private_fixture_result" not in json.dumps(error.as_dict())
        assert "Rejected model fixture title" not in json.dumps(error.as_dict())
        record = failure_record(bundle, "candidate", NOW, error)
        assert "validated_validity" not in record
        assert "routing" not in record["rejected_output"]["value"]
        assert record["rejected_origin"] == "model_submission"
        summary = failure_summary(record)
        assert summary["rejected_position_count"] == 1
        assert summary["rejected_output_available"] is True
        page = page_record(record, section="rejected_output")
        assert json.loads(page["untrusted_text"]) == snapshot
        # Diagnostics own a copy, rather than a mutable provider object.
        submitted["positions"].clear()
        assert error.rejected_result == record["rejected_output"]["value"] == snapshot

    asyncio.run(scenario())


def test_native_progress_stops_changed_files_with_same_defect_and_reuses_receipt_on_restore(
    tmp_path,
):
    from test_recipe_native_runtime import (
        ScriptedStream,
        ToolStub,
        ack,
        init_actions,
        run,
        staged,
        write,
    )

    from youju.recipe_service import RecipeService

    class InvalidParser(ToolStub):
        def __init__(self):
            super().__init__()
            self.parser_calls = 0

        async def run_script(self, files, module, function, inputs):
            result = await super().run_script(files, module, function, inputs)
            assert module == "parser"
            self.parser_calls += 1
            result["positions"][0]["fields"]["headcount"] = {
                "value": "not an integer",
                "tag": "source",
            }
            return result

    service = RecipeService(tmp_path)
    sandbox = InvalidParser()
    try:
        workspace_id = service.create_workspace("source")["workspace_id"]
        prepared = staged(service, workspace_id)
        assert (
            run(service, prepared["init_task_id"], ScriptedStream(init_actions()), sandbox=sandbox)[
                "status"
            ]
            == "succeeded"
        )
        build = service.start_build(
            workspace_id, input_revision="v1", request_id="build-1", prompt="Build recipes."
        )
        script = template_candidate()["parser_py"] + "\n# Different files; unchanged defect.\n"
        result = run(
            service,
            build["id"],
            ScriptedStream([ack("build"), write("/work/parser.py", script), ack("build")]),
            sandbox=sandbox,
        )
        assert result["status"] == "blocked" and result["error_code"] == "AUTHOR_NO_IMPROVEMENT"
        assert sandbox.parser_calls == 2
        attempts = service.result(build["id"])["attempts"]
        assert len(attempts) == 2
        assert attempts[0]["digest"] != attempts[1]["digest"]
        assert (
            attempts[0]["failures"][0]["issue_digest"] == attempts[1]["failures"][0]["issue_digest"]
        )
        attempt = attempts[-1]
        assert attempt["failures"][0]["error_code"] == "DRAFT_INVALID"
        assert attempt["failures"][0]["rejected_origin"] == "script_draft"
        assert attempt["failures"][0]["rejected_position_count"] == 1
        assert attempt["progress"]["reason"] == "unchanged_confirmed_defects"
        retry = service.start_build(
            workspace_id, input_revision="v1", request_id="build-2", prompt="Read last failure."
        )
        result = run(service, retry["id"], ScriptedStream([ack("build")]), sandbox=sandbox)
        assert result["status"] == "blocked" and sandbox.parser_calls == 2
        assert service.result(retry["id"])["attempts"][0]["validation_cache_hit"]
    finally:
        service.close()


def test_source_audit_exports_actual_rejection_into_frozen_recipe_inputs(tmp_path):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_onboarding import BASE, run_onboarding, setup_source

    from youju.models import AuditRun, SourcePipeline, SourceRecipeBinding
    from youju.models.base import utc_now
    from youju.onboarding.recipe_bridge import prepare_input
    from youju.onboarding.repair import audit_feedback
    from youju.onboarding.store import digest
    from youju.recipe_service import RecipeService

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        target = next(
            b
            for b in store.bundles("source", classifier_key=pipeline.classifier_key)
            if b["url"] == BASE + "two"
        )
        with Session(engine) as session:
            binding = session.get(SourceRecipeBinding, "source")
            recipe_id, generation = binding.active_recipe_id, binding.generation + 1
        original = pipeline.runner

        class RejectingRunner:
            validate = original.validate

            async def run(self, candidate, bundle, **kwargs):
                if bundle["id"] == target["id"]:
                    rejected = extract_position_draft(bundle)
                    rejected["positions"][0]["fields"]["title"]["value"] = (
                        "Actual rejected fixture output"
                    )
                    rejected["coverage"]["needs_detailed"] = True
                    raise RecipeError(
                        "MODEL_DISABLED",
                        "No provider after insufficient script parsing",
                        errors=[
                            {
                                "code": "MODEL_DISABLED",
                                "message": "Fixture detailed provider disabled",
                            }
                        ],
                        rejected_result=rejected,
                        rejected_origin="script_draft",
                        routing=cost_route("detailed"),
                    )
                return await original.run(candidate, bundle, **kwargs)

        pipeline.runner = RejectingRunner()
        with Session(engine) as session, session.begin():
            row = session.get(SourcePipeline, "source")
            row.summary = dict(row.summary, incremental_generation=generation)
        summary = asyncio.run(pipeline._audit("source", limit=50, activate=False))
        with Session(engine) as session:
            audit = session.scalar(select(AuditRun).where(AuditRun.kind == "incremental"))
            feedback = audit_feedback(store, "source", audit.id)
        issue = next(i for i in feedback["issues"] if i["bundle_id"] == target["id"])
        reference = next(e["failure_ref"] for e in issue["errors"] if e.get("failure_ref"))
        assert "Actual rejected fixture output" in store.blobs.read(reference).decode()
        workspace_id = digest({"source_id": "source"})
        path = store.blobs.root / "authoring" / workspace_id
        cases = store.bundles("source", classifier_key=pipeline.classifier_key)
        manifest = prepare_input(
            path, "source", [target], {"now_iso": utc_now().isoformat()}, feedback, cases=cases
        )
        service = RecipeService(store.blobs.root)
        try:
            receipt = service.input_completed(
                workspace_id,
                revision="failure-view",
                manifest_digest=manifest,
                request_id="failure-view-input",
            )
            inputs = service.input_snapshot(receipt["input_revision_id"])
            payload = json.loads(inputs["files"]["payload.json"])
            workspace = service.workspace(workspace_id)
            access = CaseAccess(
                service.blobs,
                catalog_ref=payload["case_catalog_ref"],
                source_id="source",
                input_revision=receipt["input_revision_id"],
                now_iso=payload["now_iso"],
                model_profile_digest="fixture",
                author=RecipeAuthor(None, original),
                workspace=workspace,
                failure_refs=payload["case_failure_refs"],
            )
            imported = "source-failure:" + payload["case_failure_refs"][target["id"]][0]
            page = access.inspect_failure(imported, section="rejected_output")
            assert "Actual rejected fixture output" in page["untrusted_text"]
            assert page["rejected_origin"] == "script_draft"
            assert json.loads(page["untrusted_text"])["schema_version"] == "position-draft-v1"
            assert page["bindings"]["recipe_id"] == recipe_id
            assert summary["errors_by_code"]
        finally:
            service.close()
    finally:
        engine.dispose()


def test_schema_diagnostics_report_fields_without_echoing_unknown_material_keys():
    from youju.onboarding.xdog import check_schema

    schema = {
        "type": "object",
        "properties": {
            "positions": {
                "type": "array",
                "items": {"type": "object", "properties": {"headcount": {"type": "integer"}}},
            }
        },
    }
    with pytest.raises(ModelError) as failed:
        check_schema({"positions": [{"headcount": "invalid"}]}, schema)
    assert failed.value.as_dict()["path"] == "result.positions[0].headcount"
    with pytest.raises(ModelError) as unknown:
        check_schema(
            {"untrusted-private-fixture": 1},
            {"type": "object", "additionalProperties": {"type": "string"}},
        )
    assert unknown.value.as_dict()["path"] == "result.*"
    assert "untrusted-private-fixture" not in json.dumps(unknown.value.as_dict())
