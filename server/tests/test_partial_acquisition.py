"""Focused partial-material checks using synthetic scripts, models and temporary SQLite only."""

import asyncio
import copy
import json
from datetime import datetime

import pytest
import xdog.ai as ai
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, setup_source, table
from test_xdog_recipes import FakeSandbox, bundle, cost_route

from youju.models import (
    AuditResult,
    Position,
    ResourceScan,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.onboarding.auditing import current_source_positions, material_warnings
from youju.onboarding.materials import parse_material
from youju.onboarding.pipeline import Pipeline
from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, template_candidate
from youju.onboarding.sampling import select_samples
from youju.onboarding.store import PipelineStore
from youju.onboarding.xdog import ModelError, XdogModels
from youju.positions.contracts import CONTRACT
from youju.positions.drafts import DRAFT_SCHEMA, ExtractionDraft
from youju.positions.extraction import validate_output
from youju.positions.facts import PositionFacts
from youju.positions.normalization import normalize_extraction
from youju.positions.parsing import extract_position_draft, extract_position_facts

NOW = "2026-10-12T12:00:00+08:00"
MISSING_URL = "https://attachments.example.org/positions.xlsx"
DENIAL = "attachment_outside_approved_scope"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("Unexpected live model provider"))


def partial_bundle(*, prose=False):
    notice = bundle("2026-10-31")
    notice["status"] = "partial"
    attachment = PipelineStore._missing(MISSING_URL, DENIAL)
    notice["attachments"] = [attachment]
    notice["errors"] = copy.deepcopy(attachment["errors"])
    notice["page"]["links"] = [{"url": MISSING_URL, "label": "岗位表", "is_attachment": True}]
    if prose:
        notice["page"]["tables"] = []
        notice["page"]["blocks"] = [
            {
                "id": "body:role",
                "text": "现招聘管理助理，要求本科学历。",
                "locator": "body > main > p:1",
            },
            notice["page"]["blocks"][-1],
        ]
    return notice


def prose_extraction(task, payload, schema, tier):
    assert task == "extract_position_draft" and tier == "flow" and schema == DRAFT_SCHEMA
    assert payload["bundle"]["status"] == "partial"
    assert payload["bundle"]["attachments"][0]["errors"][0]["code"] == DENIAL
    assert any(
        "现招聘管理助理，要求本科学历。" in b["text"] for b in payload["bundle"]["page"]["blocks"]
    )
    previous = payload["previous_draft"]
    return ExtractionDraft(
        notice_facts=copy.deepcopy(previous["notice_facts"]),
        positions=[
            {
                "key": "prose:role",
                "fields": {
                    "title": {"value": "管理助理", "tag": "source"},
                    "requirements_text": {"value": "本科学历", "tag": "source"},
                },
                "conditions": [
                    {
                        "id": "education",
                        "type": "education",
                        "operator": "equals",
                        "scope": "position",
                        "value": {"level": "bachelor"},
                        "tag": "source",
                    }
                ],
                "condition_logic": {"condition_id": "education"},
            }
        ],
        # A model cannot certify absent attachments merely by claiming completeness.
        coverage={"supported": True, "complete": True, "needs_detailed": False},
        issues=[
            copy.deepcopy(i)
            for i in previous["issues"]
            if i["code"] != "UNSUPPORTED_POSITION_LAYOUT"
        ],
    ).as_dict()


def test_partial_body_reaches_model_and_keeps_literal_rows_and_capture_diagnostics():
    notice = partial_bundle(prose=True)
    before = copy.deepcopy(notice)
    sandbox, models = FakeSandbox(), XdogModels(stub=prose_extraction)
    result = asyncio.run(
        RecipeRunner(sandbox, models).run(template_candidate(), notice, now_iso=NOW)
    )
    assert len(models.calls) == 1
    assert [call[1] for call in sandbox.calls] == ["parser"]
    assert result["status"] == "partial" and result["schema_version"] == CONTRACT
    assert len(result["positions"]) == 1
    facts = PositionFacts.from_dict(result["positions"][0])
    assert facts.fields["title"].as_dict() == {"value": "管理助理", "tag": "source"}
    assert facts.conditions[0].as_dict() == {
        "id": "p:education",
        "type": "education",
        "operator": "equals",
        "scope": "position",
        "value": {"level": "bachelor"},
        "tag": "source",
    }
    assert facts.condition_logic == {"condition_id": "p:education"}
    assert facts.fields["deadline_on"].as_dict() == {"value": "2026-10-31", "tag": "source"}
    assert "evidence" not in result["positions"][0]
    assert "validity" not in result and "quality_tier" not in result
    assert validate_output(notice, result) == []
    assert {e["code"] for e in result["errors"]} >= {"INCOMPLETE_CAPTURE", "CAPTURE_DIAGNOSTICS"}
    assert result["coverage"]["complete"] is False and result["execution"]["model_used"]
    # Runtime retains draft capture issues; source audit owns full URL-level warnings.
    assert any(w.get("url") == MISSING_URL for w in material_warnings(notice))
    assert notice == before


def test_missing_only_material_and_navigation_on_quick_path_do_not_call_models():
    for blocks in (
        [],
        [
            {"id": "title", "text": "招聘公告", "locator": "title"},
            {"id": "nav", "text": "招聘信息", "locator": "body > nav > p:1"},
        ],
    ):
        notice = partial_bundle(prose=True)
        notice["page"]["blocks"] = blocks
        models = XdogModels(stub=lambda *_: pytest.fail("Quick material reached a model"))
        result = asyncio.run(
            RecipeRunner(FakeSandbox(), models).run(
                template_candidate(), notice, now_iso=NOW, routing=cost_route("quick")
            )
        )
        assert models.calls == []
        assert result["status"] == "review" and result["positions"] == []
        assert result["coverage"]["complete"] is False
        assert result["coverage"]["needs_detailed"] is True
        assert {e["code"] for e in result["errors"]} >= {
            "INCOMPLETE_CAPTURE",
            "UNSUPPORTED_POSITION_LAYOUT",
        }
        assert not result["execution"]["model_used"]
        assert validate_output(notice, result) == []


@pytest.mark.parametrize(
    "models",
    [
        None,
        XdogModels(),
        XdogModels(stub=lambda *_: pytest.fail("Capture warnings alone bought a model call")),
    ],
)
def test_partial_table_without_semantic_gap_needs_no_model_and_discloses_capture(models):
    notice, sandbox = partial_bundle(), FakeSandbox()
    result = asyncio.run(
        RecipeRunner(sandbox, models).run(template_candidate(), notice, now_iso=NOW)
    )
    assert result["status"] == "partial" and len(result["positions"]) == 1
    assert [call[1] for call in sandbox.calls] == ["parser"]
    assert result["coverage"]["supported"] and not result["coverage"]["complete"]
    assert not result["coverage"]["needs_detailed"]
    assert not result["execution"]["model_used"] and result["execution"]["path"] == "detailed"
    assert result["positions"][0]["deadline_on"] == {"value": "2026-10-31", "tag": "source"}
    assert {e["code"] for e in result["errors"]} >= {"INCOMPLETE_CAPTURE", "CAPTURE_DIAGNOSTICS"}
    assert validate_output(notice, result) == []
    if models is not None:
        assert models.calls == []


@pytest.mark.parametrize("corruption", ["tag", "structured_date", "schema"])
def test_partial_model_output_keeps_actual_draft_schema_failure_ownership(corruption):
    notice = partial_bundle(prose=True)
    rejected = []

    def stub(*args):
        draft = prose_extraction(*args)
        row = draft["positions"][0]
        if corruption == "tag":
            row["fields"]["title"]["tag"] = "unknown"
        elif corruption == "structured_date":
            # A genuinely inconsistent date, not an invented notice-level oracle veto.
            draft["notice_facts"]["dates"]["end"]["date"] = "2025-01-01"
        else:
            row["unsupported_field"] = "not allowed"
        rejected.append(copy.deepcopy(draft))
        return draft

    with pytest.raises(RecipeError) as caught:
        asyncio.run(
            RecipeRunner(FakeSandbox(), XdogModels(stub=stub)).run(
                template_candidate(), notice, now_iso=NOW
            )
        )
    error = caught.value
    assert error.code == ("OUTPUT_SCHEMA" if corruption == "schema" else "DRAFT_INVALID")
    expected_code = "OUTPUT_SCHEMA" if corruption == "schema" else "POSITION_DRAFT_SCHEMA"
    assert {e["code"] for e in error.errors} == {expected_code}
    assert error.rejected_result == rejected[0]
    assert error.rejected_origin == (
        "model_submission" if corruption == "schema" else "detailed_draft"
    )
    assert "rejected_result" not in error.as_dict()
    assert "unsupported_field" not in json.dumps(error.as_dict())
    if corruption != "schema":
        assert error.routing["mode"] == "detailed"
    # Error receipts copy nested metadata and never expose raw rejected model data.
    route = {**cost_route("detailed"), "diagnostics": [{"code": DENIAL}]}
    propagated = RecipeError(
        "TEST", "Synthetic failure", routing=route, rejected_result=rejected[0]
    )
    route["diagnostics"].clear()
    serialized = propagated.as_dict()
    serialized["routing"]["diagnostics"].clear()
    assert propagated.routing["diagnostics"] == [{"code": DENIAL}]


@pytest.mark.parametrize("now_iso", [NOW, "2026-11-01T12:00:00+08:00"])
@pytest.mark.parametrize("artifact", ["validity_py", "fast_parse_py"])
def test_partial_notice_legacy_authority_is_rejected_before_extraction(now_iso, artifact):
    candidate = template_candidate()
    candidate[artifact] = "raise AssertionError('obsolete adapter must never execute')"
    sandbox = FakeSandbox()
    models = XdogModels(stub=lambda *_: pytest.fail("Obsolete artifact reached a model"))
    with pytest.raises(RecipeError) as caught:
        asyncio.run(
            RecipeRunner(sandbox, models).run(
                candidate, partial_bundle(prose=True), now_iso=now_iso
            )
        )
    assert caught.value.code == "RECIPE_INVALID"
    assert not sandbox.calls and not models.calls


class RecordingSandbox(FakeSandbox):
    def __init__(self):
        super().__init__()
        self.fail_parser = False

    async def parse(self, body, **kwargs):
        return await asyncio.to_thread(parse_material, body, **kwargs)

    async def run_script(self, files, module, function, inputs):
        if self.fail_parser:
            raise ValueError("Synthetic extraction failure")
        return await super().run_script(files, module, function, inputs)


@pytest.mark.parametrize("failure", [None, "model", "fast"])
def test_pipeline_persists_partial_rows_or_validated_failure_without_parent_redownload(
    tmp_path, monkeypatch, failure
):
    # This fixture acquires only a synthetic transport page into a fresh temporary DB.
    # All audit retry dispatch is replaced with a recorder; no live state is touched.
    html = (
        "<html><head><title>公开招聘公告</title></head><body><main>"
        "<h1>公开招聘公告</h1><p>报名截止日期：2026年10月31日。</p>"
        + table()
        + (f'<a href="{MISSING_URL}">岗位表</a>' if failure is None else "")
        + "</main></body></html>"
    ).encode()
    sandbox = RecordingSandbox()

    def stub(task, payload, schema, tier):
        assert task == "extract_position_draft" and schema == DRAFT_SCHEMA
        if failure == "model":
            raise ModelError("MODEL_OUTPUT_LIMIT", "Synthetic extraction limit")
        return extract_position_draft(payload["bundle"])

    engine, store, transport, pipeline = setup_source(
        tmp_path, pages={BASE: (html, "text/html; charset=utf-8")}, sandbox=sandbox, stub=stub
    )
    clock = datetime.fromisoformat("2026-11-01T12:00:00+08:00" if failure == "fast" else NOW)
    monkeypatch.setattr("youju.onboarding.pipeline.utc_now", lambda: clock)
    retries = []
    monkeypatch.setattr(pipeline, "_render_retries", lambda source, ids: retries.append(ids) or 0)
    sandbox.fail_parser = failure == "fast"
    sandbox.needs_detailed = failure == "model"

    async def scenario():
        await pipeline.worker.run_once("source", 50)
        store.build_bundles("source")
        await pipeline._classify("source", 50)
        with Session(engine) as session, session.begin():
            recipe = SourceRecipe(
                source_id="source",
                digest="synthetic-recipe",
                body_ref="fixture",
                status="validated",
                validation={"toolkit_key": pipeline.toolkit_key(), "errors": []},
            )
            session.add(recipe)
            session.flush()
            state = session.get(SourcePipeline, "source")
            session.get(SourceRecipeBinding, "source").candidate_recipe_id = recipe.id
            state.stage = "audit"
            recipe_id = recipe.id
        monkeypatch.setattr(pipeline, "_recipe", lambda *a, **k: (recipe_id, template_candidate()))
        return await pipeline._audit("source", limit=50, activate=True)

    try:
        summary = asyncio.run(scenario())
        saved_bundle = store.bundles("source")[0]
        with Session(engine) as session:
            audit = session.scalar(select(AuditResult))
            assert audit.result["schema_version"] == CONTRACT
            assert "validity" not in audit.result and "quality_tier" not in audit.result
            assert audit.result["routing"]["mode"] == ("quick" if failure == "fast" else "detailed")
            assert audit.status != "success"
            if failure is None:
                assert audit.status == "partial" and audit.errors == []
                assert audit.parse_id is not None and session.scalar(select(Position)) is not None
                assert any(error.get("url") == MISSING_URL for error in audit.result["errors"])
                assert audit.result["status"] == "partial"
                assert len(audit.result["positions"]) == 1
                assert validate_output(saved_bundle, audit.result) == []
                assert retries == [set()]
                assert summary["candidate_position_count"] == 1
            elif failure == "model":
                # Refinement failure remains blocking, but cannot erase the
                # canonical facts already produced by the isolated script.
                assert audit.status == "review" and audit.result["status"] == "partial"
                assert audit.parse_id is not None and len(audit.result["positions"]) == 1
                assert audit.errors and any(
                    e.get("failure_code") == "MODEL_OUTPUT_LIMIT" for e in audit.errors
                )
                assert validate_output(saved_bundle, audit.result) == []
                assert summary["position_count"] == 0 and not summary["passed"]
                assert summary["candidate_position_count"] == 1
            else:
                assert audit.parse_id is None and session.scalar(select(Position)) is None
                assert audit.status == "failed" and audit.result["positions"] == []
            scan = session.scalar(select(ResourceScan))
            assert (scan.mode, scan.state) == ("static", "done")
        if failure is None:
            # Acquisition warnings do not prevent available facts from passing
            # source audit/admission. Partial visibility remains an explicit query
            # choice, separate from extraction and its successful row count.
            assert summary["position_count"] == 1 and summary["passed"]
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    "youju.onboarding.auditing.evaluation_day",
                    lambda stamp=None: clock.date().isoformat(),
                )
                assert len(current_source_positions(store, "source", include_partial=True)) == 1
                assert current_source_positions(store, "source") == []
        else:
            assert summary["position_count"] == 0 and not summary["passed"]
        assert current_source_positions(store, "source") == []
        assert transport.calls == [("static", BASE)]
        assert [call[1] for call in sandbox.calls] == ([] if failure == "fast" else ["parser"])
        assert len(pipeline.models.calls) == (1 if failure == "model" else 0)
    finally:
        engine.dispose()


def test_policy_only_retry_exclusion_does_not_hide_other_failures():
    notice = partial_bundle()
    audit = {"status": "review", "position_count": 1, "errors": material_warnings(notice)}
    assert not Pipeline._needs_parent_render(notice, audit)
    # A familiar capture code is not authority: only exact pinned-input warnings
    # may be excluded. Parser/model diagnostics must not impersonate acquisition.
    for forged in (
        {"code": "INCOMPLETE_CAPTURE", "message": "Untrusted parser assertion"},
        {
            "code": "CAPTURE_DIAGNOSTICS",
            "message": "Bundle contains acquisition diagnostics",
            "document": 99,
        },
    ):
        assert Pipeline._needs_parent_render(notice, audit | {"errors": [*audit["errors"], forged]})
    for changes in (
        {"status": "failed"},
        {"position_count": 0},
        {
            "errors": [
                *audit["errors"],
                {"code": "POSITION_V3_SCHEMA", "message": "Malformed field tag"},
            ]
        },
        {"errors": [*audit["errors"], {"code": "CLASSIFICATION_REVIEW", "message": "Unresolved"}]},
    ):
        assert Pipeline._needs_parent_render(notice, audit | changes)
    for code in ("attachment_unavailable", "network", "material_parser_failed"):
        changed = copy.deepcopy(notice)
        changed["attachments"][0]["errors"][0]["code"] = code
        assert Pipeline._needs_parent_render(changed, audit)
    changed = copy.deepcopy(notice)
    changed["page"]["truncated"] = True
    assert Pipeline._needs_parent_render(changed, audit)
    changed = copy.deepcopy(notice)
    changed["attachments"] = []
    assert Pipeline._needs_parent_render(changed, audit)


def test_sampling_includes_confident_partial_prose_without_claiming_missing_format_coverage():
    prose = partial_bundle(prose=True)
    prose["id"] = "partial-prose"
    prose["assessment"] = {
        "page_roles": ["article"],
        "business_type": "recruitment",
        "requires_review": False,
        "template_key": "shared-html",
    }
    inputs = []
    for number in range(5):
        complete = bundle("2026-10-31")
        complete["id"] = f"complete-table-{number}"
        complete["assessment"] = dict(prose["assessment"])
        inputs.append(complete)
    inputs.append(prose)
    before = copy.deepcopy(inputs)
    selected = select_samples(inputs, limit=20)
    assert len(selected["samples"]) == 3 and prose in selected["samples"]
    assert selected["covered_structures"] == ["position_table", "prose"]
    assert selected["uncovered_formats"] == ["xlsx"]
    assert selected["incomplete_samples"] == [
        {"bundle_id": prose["id"], "warnings": material_warnings(prose)}
    ]
    for change in ("unreadable", "unresolved", "parser_failure"):
        bad = copy.deepcopy(prose)
        if change == "unreadable":
            bad["page"]["blocks"] = []
        elif change == "unresolved":
            bad["assessment"]["requires_review"] = True
        else:
            bad["attachments"][0]["errors"][0]["code"] = "material_parser_failed"
        selected_bad = select_samples([bad])
        if change == "parser_failure":
            assert selected_bad["samples"] == [bad]
            assert selected_bad["incomplete_samples"][0]["warnings"] == material_warnings(bad)
        else:
            assert not selected_bad["samples"]
    assert inputs == before


@pytest.mark.parametrize("second_title", ["研究助理", "管理助理"])
def test_author_partial_sample_preflight_and_detailed_merge_preserve_available_rows(second_title):
    sample = partial_bundle(prose=True)
    models = XdogModels(stub=prose_extraction)
    author = RecipeAuthor(models, RecipeRunner(FakeSandbox(), models))
    accepted = asyncio.run(author.validate_candidate(template_candidate(), [sample], NOW, 1))
    assert accepted["status"] == "validated" and not accepted["errors"]
    assert len(models.calls) == 1
    receipt = accepted["samples"][0]
    assert receipt["status"] == "partial" and receipt["workflow_tested"]
    assert not receipt["coverage"]["complete"] and receipt["routing"]["mode"] == "detailed"
    result = extract_position_facts(partial_bundle())
    assert RecipeAuthor.sample_errors(sample, result) == []
    unsupported = copy.deepcopy(result)
    unsupported["positions"][0]["title"]["tag"] = "unknown"
    assert {e["code"] for e in RecipeAuthor.sample_errors(sample, unsupported)} == {
        "POSITION_V3_SCHEMA"
    }
    # A false complete/success assertion remains invalid, even for available rows.
    invalid_success = {**copy.deepcopy(result), "status": "success"}
    assert {e["code"] for e in RecipeAuthor.sample_errors(sample, invalid_success)} == {
        "POSITION_V3_SCHEMA"
    }
    tabular = partial_bundle()
    grid = tabular["page"]["tables"][0]
    grid["rows"].append([second_title, "3"])
    grid["cell_evidence"].append(["second:title", "second:count"])
    tabular["page"]["blocks"].extend(
        [
            {"id": "second:title", "text": second_title, "locator": "table row 2 col 0"},
            {"id": "second:count", "text": "3", "locator": "table row 2 col 1"},
        ]
    )

    # The domain validator is not a re-extraction oracle. Preservation belongs to
    # the actual draft merge by material-local key, including equal job titles.
    def omit_first(task, payload, schema, tier):
        assert task == "extract_position_draft" and schema == DRAFT_SCHEMA
        refined = copy.deepcopy(payload["previous_draft"])
        refined["positions"] = refined["positions"][1:]
        refined["coverage"]["needs_detailed"] = False
        return refined

    sandbox = FakeSandbox()
    sandbox.needs_detailed = True
    runner = RecipeRunner(sandbox, XdogModels(stub=omit_first))
    merged = asyncio.run(runner.run(template_candidate(), tabular, now_iso=NOW))
    assert [p["title"]["value"] for p in merged["positions"]] == ["管理助理", second_title]
    assert [p["headcount"]["value"] for p in merged["positions"]] == [2, 3]
    assert not validate_output(tabular, merged)
    grid["rows"].append(["Unsupported short row"])
    grid["cell_evidence"].append([])
    draft = extract_position_draft(tabular)
    assert "ROW_SHAPE" in {i["code"] for i in draft["issues"]}
    assert draft["coverage"]["needs_detailed"]
    normalized = normalize_extraction(draft)
    assert "ROW_SHAPE" in {i["code"] for i in normalized["errors"]}
    assert len(normalized["positions"]) == 3 and normalized["status"] == "partial"


@pytest.mark.parametrize("missing", ["supported", "complete", "needs_detailed"])
def test_draft_coverage_metadata_cannot_be_omitted_even_when_rows_are_known(missing):
    class MissingMetadata(FakeSandbox):
        async def run_script(self, files, module, function, inputs):
            value = await super().run_script(files, module, function, inputs)
            value["coverage"].pop(missing)
            return value

    models = XdogModels(stub=lambda *_: pytest.fail("Invalid draft bought model fallback"))
    with pytest.raises(RecipeError) as caught:
        asyncio.run(
            RecipeRunner(MissingMetadata(), models).run(
                template_candidate(), partial_bundle(), now_iso=NOW
            )
        )
    assert caught.value.code == "DRAFT_INVALID"
    assert {e["code"] for e in caught.value.errors} == {"POSITION_DRAFT_SCHEMA"}
    assert caught.value.rejected_origin == "script_draft"
    assert missing not in caught.value.rejected_result["coverage"]
    assert not models.calls


@pytest.mark.parametrize("status", ["partial", "incomplete", "failed"])
def test_capture_flags_do_not_disqualify_readable_authoring_examples(status):
    value = partial_bundle(prose=True)
    value["status"] = status
    value["page"]["status"] = "partial"
    value["page"]["truncated"] = True
    value["assessment"] = {
        "page_roles": ["article"],
        "business_type": "recruitment",
        "requires_review": False,
        "template_key": "same-readable-layout",
    }
    result = select_samples([value])
    assert result["samples"] == [value]
    assert result["incomplete_samples"][0]["warnings"] == material_warnings(value)
