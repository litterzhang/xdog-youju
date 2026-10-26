"""Declared application rules are distinct from dated expiry/current hiring."""

import asyncio
import copy

import pytest
from test_sourcekit import bundle
from test_xdog_recipes import NOW

from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, template_candidate
from youju.onboarding.toolkit import (
    application_period_hint,
    check_validity,
    material_issues,
    validate_application_period,
)
from youju.positions.extraction import validate_output
from youju.positions.parsing import extract_position_draft, extract_position_facts


@pytest.mark.parametrize(
    ("texts", "kind"),
    [
        (
            [
                "三、报名时间",
                "本招聘启事长期有效。应聘者可随时提交报名材料，学校将适时组织考核。",
                "报名结束后将进行资格审查。",
                "2025年5月20日",
            ],
            "open_ended",
        ),
        (["常年招聘。"], "open_ended"),
        (["本招聘启事长期有效，岗位招满为止。"], "until_filled"),
        (["本招聘启事长期有效，岗位招满后停止报名。"], "until_filled"),
        (["本公告并非长期有效。"], "unknown"),
        (["本招聘启事长期有效，但岗位已经招满。"], "unknown"),
        (["原招聘启事长期有效，现已停止招聘。"], "unknown"),
        (["申请人须持有长期有效的身份证。"], "not_stated"),
        (["2025年5月20日"], "not_stated"),
        (["本招聘启事长期有效。", "报名截止2026年10月31日。"], "unknown"),
        (["报名截止2026年10月31日。"], "fixed_deadline"),
    ],
)
def test_known_rules_and_negative_date_contexts(texts, kind):
    material = bundle(texts=texts)
    period = application_period_hint(material)
    assert period["kind"] == kind
    assert validate_application_period(material, period) == []
    expiry = check_validity(material, NOW)
    if kind in {"open_ended", "until_filled"}:
        assert expiry["status"] == "unknown" and expiry["deadline"] is None
        assert period["text"] and period["evidence_ids"]
    if kind in {"unknown", "not_stated"}:
        assert period["text"] is None and period["evidence_ids"] == []


def test_row_rules_incomplete_material_and_independent_evidence_checks():
    material = bundle(texts=["本招聘启事长期有效。"], rows=[["岗位名称", "人数"], ["助理", "1"]])
    period = application_period_hint(material)
    missing = copy.deepcopy(material)
    missing["status"] = "incomplete"
    missing["errors"] = [{"code": "ACQUISITION_MISSING", "message": "Fixture attachment missing"}]
    assert application_period_hint(missing)["kind"] == "open_ended"
    incomplete_page = copy.deepcopy(material)
    incomplete_page["page"]["truncated"] = True
    assert application_period_hint(incomplete_page)["kind"] == "open_ended"
    assert material_issues(incomplete_page)
    row_only = bundle(rows=[["岗位名称", "要求"], ["助理", "本岗位招满为止"]])
    assert application_period_hint(row_only)["kind"] == "not_stated"
    changed = {**period, "kind": "until_filled"}
    assert any(
        e["code"] == "APPLICATION_PERIOD_CONFLICT"
        for e in validate_application_period(material, changed)
    )
    forged = {**period, "text": "本招聘启事长期有效，保证现在可以报名。"}
    assert any(
        e["code"] == "APPLICATION_PERIOD_EVIDENCE"
        for e in validate_application_period(material, forged)
    )
    result = {
        **extract_position_facts(material),
        "validity": {
            **check_validity(material, NOW),
            "application_period": period,
            "current_hiring_status": "not_verified",
        },
    }
    assert validate_output(material, result) == []
    dated = bundle(texts=["发布时间2025年5月20日；报名截止2026年10月31日。"])
    fixed = application_period_hint(dated)
    assert fixed["kind"] == "fixed_deadline"
    fixed["text"] = "2025年5月20日"
    assert any(
        e["code"] == "APPLICATION_PERIOD_EVIDENCE"
        for e in validate_application_period(dated, fixed)
    )
    # Public hiring claims are no longer part of this domain envelope. The
    # public API, not field validation, owns current_hiring_status assertions.
    result["positions"][0]["deadline_on"] = {"value": "2026-10-31", "tag": "source"}
    assert any(e["code"] == "POSITION_V3_SCHEMA" for e in validate_output(material, result))


@pytest.mark.parametrize(
    "deadline, tag, expected_code",
    [
        ("2026-10-31", "source", None),
        ("2025-05-20", "source", "POSITION_V3_SCHEMA"),
        ("2025-01-01", "source", "POSITION_V3_SCHEMA"),
        ("2025-05-20", "ai_inferred", "POSITION_V3_SCHEMA"),
    ],
)
def test_position_deadline_source_tag_requires_material_cutoff_without_field_citations(
    deadline, tag, expected_code
):
    material = bundle(
        texts=["发布时间2025年5月20日；报名截止2026年10月31日。"],
        rows=[["岗位名称", "人数"], ["助理", "1"]],
    )
    validity = {
        **check_validity(material, NOW),
        "application_period": application_period_hint(material),
        "current_hiring_status": "not_verified",
    }
    result = {**extract_position_facts(material), "validity": copy.deepcopy(validity)}
    result["positions"][0]["deadline_on"] = {"value": deadline, "tag": tag}
    assert "evidence" not in result["positions"][0]
    errors = validate_output(material, result)
    assert {error["code"] for error in errors} == ({expected_code} if expected_code else set())
    assert result["validity"] == validity
    assert validity["deadline"] == "2026-10-31" and validity["status"] == "not_expired"


def test_native_author_gate_uses_shared_drafts_and_cannot_generate_hiring_claims():
    material = bundle(texts=["本招聘启事长期有效。"], rows=[["岗位名称", "人数"], ["助理", "1"]])
    candidate = template_candidate()

    class DraftSandbox:
        claim_open = False

        async def run_script(self, files, module, function, inputs):
            assert module in {"parser", "undated_parser"} and function == "parse"
            draft = extract_position_draft(inputs["bundle"])
            if self.claim_open:
                draft["current_hiring_status"] = "open"
            return draft

    async def scenario():
        sandbox = DraftSandbox()
        author = RecipeAuthor(None, RecipeRunner(sandbox))
        accepted = await author.validate_candidate(candidate, [material], NOW, 1)
        assert accepted["errors"] == []
        result = await author.runner.run(candidate, material, now_iso=NOW)
        assert result["positions"][0]["application_rule"]["value"] == "open_ended"
        assert "validity" not in result
        changed = copy.deepcopy(candidate)
        changed["workflow"]["edges"].pop()
        assert author.runner.validate(changed)  # No source-owned execution bypass.
        sandbox.claim_open = True
        with pytest.raises(RecipeError) as failure:
            await author.runner.run(candidate, material, now_iso=NOW)
        assert failure.value.code == "DRAFT_INVALID"

    asyncio.run(scenario())
