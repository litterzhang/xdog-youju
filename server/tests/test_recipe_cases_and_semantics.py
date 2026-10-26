"""Pinned catalogs/receipts and independent semantic cases; no provider calls."""

import asyncio
import json
from copy import deepcopy

import pytest
from test_recipe_accuracy import NOW, SandboxDouble, material

from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.recipes import RecipeAuthor, RecipeRunner, template_candidate
from youju.onboarding.semantics import check_expected, compare_outputs
from youju.poc.storage import BlobStore
from youju.positions.parsing import extract_position_facts
from youju.recipe_service.cases import CaseAccess, freeze_cases, load_catalog


def test_catalog_is_task_scoped_paginated_and_receipts_bind_files(tmp_path):
    async def scenario():
        blobs = BlobStore(tmp_path / "objects")
        bundles = []
        for n in range(24):
            value = material()
            value["id"] = f"case-{n}"
            bundles.append(value)
        frozen_case = deepcopy(bundles[0])
        reference = freeze_cases(blobs, "source-1", bundles)
        bundles[0]["page"]["blocks"][0]["text"] = "Changed staging material, not frozen input"
        workspace = AuthorWorkspace(tmp_path / "workspace", sandbox=SandboxDouble())
        candidate = template_candidate()
        workspace.prepare({}, skill_text="skill", guide_text="guide", template=candidate)
        author = RecipeAuthor(None, RecipeRunner(SandboxDouble()))
        kwargs = dict(
            catalog_ref=reference,
            source_id="source-1",
            input_revision="r1",
            now_iso=NOW,
            model_profile_digest="profile",
            author=author,
            workspace=workspace,
        )
        access = CaseAccess(blobs, **kwargs)
        assert access.list_cases()["next_cursor"] == 20
        assert len(access.list_cases(20)["cases"]) == 4
        assert access.read_case("case-0")["material_digest"]
        assert json.loads(access.read_case("case-0")["untrusted_material"]) == frozen_case
        with pytest.raises(ValueError):
            access.read_case("another-task-case")
        first = await access.test_recipe(["case-0"])
        assert first["status"] == "validated" and not first["cached"]
        assert first["semantic_status"] == "unlabeled_not_accuracy_evidence"
        receipt = workspace.load_test(first["test_id"])
        assert receipt["input_revision"] == "r1"
        assert receipt["catalog_digest"] == reference
        assert receipt["case_versions"] == [
            ["case-0", load_catalog(blobs, reference, "source-1")["cases"][0]["body_ref"]]
        ]
        assert (await access.test_recipe(["case-0"]))["cached"]
        restored = CaseAccess(blobs, **kwargs)
        assert (await restored.test_recipe(["case-0"]))["cached"]
        other_revision = CaseAccess(blobs, **{**kwargs, "input_revision": "r2"})
        with pytest.raises(ValueError, match="not from this assigned task"):
            other_revision.inspect_failure(first["test_id"])
        separate = await other_revision.test_recipe(["case-0"])
        assert not separate["cached"] and separate["test_id"] != first["test_id"]
        (workspace.workspace / "parser.py").write_text(candidate["parser_py"] + "\n# changed\n")
        changed = await access.test_recipe(["case-0"])
        assert changed["test_id"] != first["test_id"]
        with pytest.raises(ValueError):
            load_catalog(blobs, reference, "other-source")
        with pytest.raises(ValueError):
            freeze_cases(blobs, "other-source", bundles)

    asyncio.run(scenario())


def test_same_count_field_change_is_detected_and_gold_allows_false_job_removal():
    before = extract_position_facts(material())
    before["positions"][0]["hiring_unit"] = {"value": "Example University", "tag": "source"}
    after = deepcopy(before)
    after["positions"][0]["headcount"] = {"value": 9, "tag": "source"}
    delta = compare_outputs(before, after)
    assert delta["status"] == "needs_review"
    assert any(e["path"].endswith("headcount") for e in delta["issues"])
    assert not delta["accuracy_verified"]
    expected = {
        "basis": "independent-rule",
        "reference": "fixture-table-row-1",
        "complete_position_set": True,
        "positions": [
            {
                "selector": {"title": "管理助理", "hiring_unit": "Example University"},
                "fields": {"headcount": {"value": 2, "tag": "source"}},
            }
        ],
    }
    assert check_expected(before, expected)["status"] == "passed"
    assert check_expected(after, expected)["status"] == "failed"
    # Old false positives are a comparison baseline, not a truth/count floor.
    duplicated = deepcopy(before)
    duplicated["positions"].append(deepcopy(before["positions"][0]))
    assert check_expected(duplicated, expected)["status"] == "failed"
    assert check_expected(before, expected)["status"] == "passed"
    assert (
        check_expected(before, {**expected, "basis": "suspected-model-output"})["status"]
        == "unlabeled"
    )
    title_only = deepcopy(expected)
    title_only["positions"][0]["selector"] = {"title": "管理助理"}
    unresolved_gold = check_expected(before, title_only)
    assert unresolved_gold["status"] == "failed" and not unresolved_gold["accuracy_verified"]
    assert unresolved_gold["issues"][0]["code"] == "EXPECTED_IDENTITY_UNRESOLVED"
    before["positions"][0]["hiring_unit"] = {"value": None, "tag": "unknown"}
    assert (
        compare_outputs(before, after)["issues"][0]["code"] == "POSITION_CORRESPONDENCE_UNRESOLVED"
    )


def test_native_restored_agent_uses_catalog_tools_and_submits_runtime_files(tmp_path):
    import hashlib

    from test_recipe_native_runtime import ScriptedStream, ToolStub, ack, init_actions, run

    from youju.onboarding.store import digest, json_bytes
    from youju.recipe_service import RecipeService

    service = RecipeService(tmp_path)
    try:
        workspace_id = service.create_workspace("source")["workspace_id"]
        workspace = service.workspace(workspace_id)
        sample = material("2026-10-22")
        sample["source_id"] = "source"
        catalog = freeze_cases(service.blobs, "source", [sample])
        payload = {
            "source_id": "source",
            "objective": "Build accuracy recipes.",
            "samples": [sample],
            "now_iso": NOW,
            "case_catalog_ref": catalog,
            "template": template_candidate(),
        }
        body = json_bytes(payload)
        (workspace.input / "payload.json").write_bytes(body)
        manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
        (workspace.input / "manifest.json").write_text(json.dumps(manifest))
        receipt = service.input_completed(
            workspace_id,
            revision="accuracy-input",
            request_id="input",
            manifest_digest=digest(manifest),
        )
        init = run(
            service,
            receipt["init_task_id"],
            ScriptedStream(init_actions()),
            sandbox=ToolStub(),
        )
        assert init["status"] == "succeeded"
        build = service.start_build(
            workspace_id,
            input_revision="accuracy-input",
            request_id="build",
            prompt="Build the pinned accuracy recipe.",
        )
        stream = ScriptedStream(
            [
                ("list_cases", {}),
                ("read_case", {"case_id": sample["id"]}),
                ("test_recipe", {"case_ids": [sample["id"]]}),
                ack("build"),
            ]
        )
        outcome = run(service, build["id"], stream, sandbox=ToolStub())
        assert outcome["status"] == "candidate_ready", outcome
        assert {"list_cases", "read_case", "test_recipe", "inspect_failure"} <= {
            tool.name for tool in stream.calls[0].tools
        }
        result = service.result(build["id"])
        assert set(result["candidate"]) == {"workflow", "parser_py", "undated_parser_py", "config"}
        assert result["candidate"]["config"]["runtime_contract"] == "recipe-runtime-v3"
        assert result["candidate"]["config"]["position_contract"] == "position-facts-v3"
        assert set(workspace.load_candidate()) == {
            "workflow",
            "parser_py",
            "undated_parser_py",
            "config",
        }
        assert (workspace.workspace / "parser.py").read_text() == result["candidate"]["parser_py"]
        assert list((workspace.history / "tests").glob("*.json"))
    finally:
        service.close()


def test_schema_valid_wrong_title_is_unlabeled_without_independent_expected_case(tmp_path):
    async def scenario():
        class WrongTitle(SandboxDouble):
            async def run_script(self, files, module, function, inputs):
                raw = await super().run_script(files, module, function, inputs)
                raw["positions"][0]["fields"].update(
                    title={"value": "Wrong fixture title", "tag": "source"},
                    hiring_unit={"value": "Example University", "tag": "source"},
                )
                return raw

        bundle = material()
        runner = RecipeRunner(WrongTitle())
        result = await runner.run(template_candidate(), bundle, now_iso=NOW)
        assert set(result) == {
            "schema_version",
            "status",
            "positions",
            "errors",
            "coverage",
            "routing",
            "execution",
        }
        assert result["schema_version"] == "position-facts-v3"
        assert result["status"] == "success" and not result["errors"]
        assert result["positions"][0]["title"] == {"value": "Wrong fixture title", "tag": "source"}
        assert result["execution"]["runtime_contract"] == "recipe-runtime-v3"
        assert not result["execution"]["model_used"]
        blobs = BlobStore(tmp_path / "objects")
        workspace = AuthorWorkspace(tmp_path / "workspace", sandbox=WrongTitle())
        workspace.prepare({}, skill_text="skill", guide_text="guide", template=template_candidate())
        access = CaseAccess(
            blobs,
            catalog_ref=freeze_cases(blobs, "source-1", [bundle]),
            source_id="source-1",
            input_revision="r1",
            now_iso=NOW,
            model_profile_digest="fixture",
            author=RecipeAuthor(None, runner),
            workspace=workspace,
        )
        tested = await access.test_recipe([bundle["id"]])
        # No comparison with the same bootstrap extractor can establish a defect.
        assert tested["status"] == "validated" and tested["error_count"] == 0
        assert tested["semantic_status"] == "unlabeled_not_accuracy_evidence"
        # Separately held literal fixture expectations really can identify the error.
        expected = {
            "basis": "independent-rule",
            "reference": "synthetic-reviewed-table-row-1",
            "complete_position_set": True,
            "positions": [
                {
                    "selector": {"title": "管理助理", "hiring_unit": "Example University"},
                    "fields": {"headcount": {"value": 2, "tag": "source"}},
                }
            ],
        }
        checked = check_expected(result, expected)
        assert checked["status"] == "failed" and not checked["accuracy_verified"]
        assert checked["issues"][0]["code"] == "EXPECTED_CORRESPONDENCE"
        assert (
            check_expected(result, {**expected, "basis": "model-output"})["status"] == "unlabeled"
        )

    asyncio.run(scenario())
