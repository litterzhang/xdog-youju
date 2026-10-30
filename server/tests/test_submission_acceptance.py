"""Host-selected semantic submission checks, using existing saved-case infrastructure."""

import asyncio
import json

import pytest
from test_recipe_accuracy import NOW, SandboxDouble, material

from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.recipe_bridge import prepare_input
from youju.onboarding.recipes import RecipeAuthor, RecipeRunner, template_candidate
from youju.poc.storage import BlobStore
from youju.recipe_service.acceptance import (
    MAX_CASES,
    freeze_acceptance,
    load_acceptance,
    select_cases,
    validate_submission,
)
from youju.recipe_service.cases import CaseAccess


def case(identity):
    value = material()
    value["id"] = identity
    table = value["page"]["tables"][0]
    table["rows"][0].append("用人单位")
    table["rows"][1].append("Example Institute")
    table["cell_evidence"][0].append("unit-header")
    table["cell_evidence"][1].append("unit-cell")
    return value


def label(count=2):
    return {
        "label_id": "host-reviewed-case",
        "expected": {
            "basis": "independent-rule",
            "reference": "frozen table[0] row[1] count cell",
            "positions": [
                {
                    "selector": {"title": "管理助理", "hiring_unit": "Example Institute"},
                    "fields": {"headcount": {"value": count, "tag": "source"}},
                }
            ],
        },
    }


def test_submission_catches_same_count_semantic_error_and_separates_unlabeled_replay(tmp_path):
    async def scenario():
        blobs = BlobStore(tmp_path / "objects")
        samples = [case("startup"), case("regression"), case("unlabeled")]
        ref = freeze_acceptance(blobs, "source-1", samples, ["startup"], {"regression": label(9)})
        plan, frozen, expected = load_acceptance(blobs, ref, "source-1")
        assert {b["id"] for b in frozen} == {"regression", "unlabeled"}
        author = RecipeAuthor(None, RecipeRunner(SandboxDouble()))
        result = await validate_submission(
            blobs, ref, "source-1", author, template_candidate(), NOW, {}
        )
        assert result["status"] == "failed" and result["semantic_cases"] == 1
        assert result["execution_cases"] == 2 and result["semantic_passed"] == 0
        assert any(
            e["semantic_code"] == "EXPECTED_FIELD" and e["case_id"] == "regression"
            for e in result["errors"]
        )
        assert result["model_calls"] == 0 and result["monetary_cost"] is None
        assert any(
            c["semantic"]["status"] == "unlabeled_not_accuracy_evidence" for c in result["checks"]
        )
        good_ref = freeze_acceptance(
            blobs, "source-1", samples, ["startup"], {"regression": label()}
        )
        good = await validate_submission(
            blobs, good_ref, "source-1", author, template_candidate(), NOW, {}
        )
        assert good["status"] == "passed" and good["semantic_passed"] == 1
        assert plan["labels"]["regression"]["label_id"] == "host-reviewed-case"
        assert expected["regression"]["positions"][0]["fields"]["headcount"]["value"] == 9
        with pytest.raises(ValueError):
            load_acceptance(blobs, ref, "another-source")

    asyncio.run(scenario())


def test_selection_is_bounded_deterministic_and_prioritizes_regressions_and_formats():
    cases = [case(str(n)) for n in range(24)]
    cases[11]["attachments"] = [{"format": "xlsx"}]
    cases[12]["attachments"] = [{"format": "docx"}]
    selected = select_cases(cases, {"0"}, {"23": label()})
    assert len(selected) == MAX_CASES
    ids = {b["id"] for b in selected}
    assert {"23", "11", "12"} <= ids and "0" not in ids
    assert selected == select_cases(list(reversed(cases)), {"0"}, {"23": label()})


def test_private_labels_are_not_author_inputs_or_case_tools_and_files_cannot_change_selection(
    tmp_path,
):
    async def scenario():
        source = "source-1"
        samples = [case("startup"), case("independent")]
        path = tmp_path / "authoring" / "workspace"
        prepare_input(
            path,
            source,
            samples[:1],
            {"now_iso": NOW},
            None,
            cases=samples,
            semantic_labels={"independent": label()},
        )
        payload = json.loads((path / "input/payload.json").read_text())
        assert "expected_ref" not in json.dumps(payload) and "host-reviewed-case" not in json.dumps(
            payload
        )
        blobs = BlobStore(path.parent / "objects")
        workspace = AuthorWorkspace(path, sandbox=SandboxDouble())
        access = CaseAccess(
            blobs,
            catalog_ref=payload["case_catalog_ref"],
            source_id=source,
            input_revision="frozen-input",
            now_iso=NOW,
            model_profile_digest="p",
            author=RecipeAuthor(None, RecipeRunner(SandboxDouble())),
            workspace=workspace,
        )
        assert "expected" not in json.dumps(access.list_cases())
        assert "host-reviewed-case" not in json.dumps(access.read_case("independent"))
        before = load_acceptance(blobs, payload["acceptance_ref"], source)
        # Author files are not host policy. Input snapshots/blobs still choose the cases.
        (workspace.workspace / "acceptance.json").write_text('{"cases": []}')
        assert load_acceptance(blobs, payload["acceptance_ref"], source) == before

    asyncio.run(scenario())


def test_no_extra_cases_is_explicit_not_accuracy_evidence(tmp_path):
    async def scenario():
        blobs = BlobStore(tmp_path)
        ref = freeze_acceptance(blobs, "source-1", [case("startup")], ["startup"])
        report = await validate_submission(
            blobs,
            ref,
            "source-1",
            RecipeAuthor(None, RecipeRunner(SandboxDouble())),
            template_candidate(),
            NOW,
            {},
        )
        assert report["status"] == "no_independent_cases"
        assert report["execution_cases"] == report["semantic_cases"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [2, 9])
def test_native_submit_enforces_private_semantics_not_just_startup_samples(tmp_path, count):
    import hashlib

    from test_recipe_native_runtime import ScriptedStream, ToolStub, ack, init_actions, run

    from youju.recipe_service import RecipeService
    from youju.serialization import digest, json_bytes

    service = RecipeService(tmp_path)
    try:
        workspace_id = service.create_workspace("source")["workspace_id"]
        workspace = service.workspace(workspace_id)
        startup, regression = case("startup"), case("regression")
        startup["source_id"] = regression["source_id"] = "source"
        # Expired cases stay script-only; acceptance must not silently enable AI.
        for b in (startup, regression):
            b["page"]["blocks"][-1]["text"] = "报名截止日期：2020年10月31日"
        reference = freeze_acceptance(
            service.blobs,
            "source",
            [startup, regression],
            ["startup"],
            {"regression": label(count)},
        )
        payload = {
            "source_id": "source",
            "objective": "Synthetic acceptance test",
            "samples": [startup],
            "now_iso": NOW,
            "acceptance_ref": reference,
        }
        body = json_bytes(payload)
        (workspace.input / "payload.json").write_bytes(body)
        manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
        (workspace.input / "manifest.json").write_bytes(json_bytes(manifest))
        inputs = service.input_completed(
            workspace_id, revision="v1", manifest_digest=digest(manifest), request_id="input"
        )
        build = service.start_build(
            workspace_id, input_revision="v1", prompt="Build", request_id="build"
        )
        sandbox = ToolStub()
        run(service, inputs["init_task_id"], ScriptedStream(init_actions()), sandbox=sandbox)
        actions = [ack("build")]
        if count == 9:
            actions.append(
                (
                    "report_progress",
                    {"phase": "blocked", "message": "Independent semantic check failed"},
                )
            )
        stream = ScriptedStream(actions)
        run(service, build["id"], stream, sandbox=sandbox)
        task = service.status(build["id"])
        assert task["status"] == ("blocked" if count == 9 else "candidate_ready")
        assert bool(task["revision_id"]) is (count == 2)
        result = service.result(build["id"])
        acceptance = result["attempts"][0]["acceptance"]
        assert acceptance["status"] == ("failed" if count == 9 else "passed")
        assert acceptance["semantic_cases"] == 1 and acceptance["model_calls"] == 0
        assert bool(acceptance["errors"]) is (count == 9)
    finally:
        service.close()


def test_source_exports_existing_regression_labels_without_exposing_holdouts(monkeypatch):
    import test_route_fact_admission as admission
    from sqlalchemy.orm import Session

    from youju.models import ResourceBundle
    from youju.models.onboarding import SemanticCaseLabel
    from youju.onboarding.semantic_labels import author_visible, export_regressions, register_label
    from youju.serialization import json_bytes

    fixture = admission.store.__wrapped__(monkeypatch)
    store = next(fixture)
    try:
        regression = store.add_notice("regression")
        holdout = store.add_notice("holdout")
        expected = {
            "basis": "independent-rule",
            "reference": "synthetic empty prose case",
            "positions": [],
            "complete_position_set": True,
        }
        receipt = register_label(
            store,
            "source",
            regression["id"],
            expected,
            reviewer="synthetic-test",
            split="regression",
        )
        # Simulate a pre-existing, never-exposed holdout; do not retroactively label
        # an already-authored source as unseen through the public registration API.
        with Session(store.engine) as session, session.begin():
            bundle = session.get(ResourceBundle, holdout["id"])
            session.add(
                SemanticCaseLabel(
                    id="held-out-label",
                    source_id="source",
                    bundle_id=bundle.id,
                    bundle_digest=bundle.digest,
                    expected_ref=store.blobs.put(json_bytes(expected)),
                    reviewer="synthetic-test",
                    split="holdout",
                )
            )
        exported = export_regressions(store, "source")
        assert set(exported) == {regression["id"]}
        assert exported[regression["id"]]["label_id"] == receipt["label_id"]
        assert exported[regression["id"]]["expected"] == expected
        assert not author_visible(store, holdout["id"])
    finally:
        fixture.close()
