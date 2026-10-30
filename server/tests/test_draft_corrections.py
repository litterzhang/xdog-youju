"""Evidence-bound refinement operations, all synthetic and provider-free."""

from copy import deepcopy

import pytest
from test_recipe_runtime_v3 import material
from test_runtime_field_completeness import execute, script_draft

from youju.positions.drafts import merge_drafts, validate_draft, validate_model_draft


def inputs(operation="replace_field"):
    bundle = material()
    bundle["id"] = "frozen-bundle"
    bundle["page"]["version_id"] = "frozen-v1"
    bundle["page"]["blocks"].append(
        {"id": "b2", "text": "招聘岗位：研究助理。报名方式：网上报名。"}
    )
    script = script_draft()
    old = script["positions"][0]
    old["fields"]["title"] = {"value": "报名方式", "tag": "source"}
    refined = deepcopy(script)
    proposal = {
        "operation": operation,
        "position_key": old["key"],
        "field": "title",
        "before": deepcopy(old["fields"]["title"]),
        "after": {"value": "研究助理", "tag": "source"},
        "bundle_id": bundle["id"],
        "reason": "A section label was mistaken for a position",
        "evidence": [
            {
                "version_id": "frozen-v1",
                "pointer": "/blocks/1/text",
                "start": 0,
                "end": 9,
                "quote": "招聘岗位：研究助理",
            }
        ],
    }
    if operation == "withdraw_position":
        proposal.update(field=None, before=deepcopy(old), after=None)
        refined["positions"] = []
    else:
        refined["positions"][0]["fields"]["title"] = deepcopy(proposal["after"])
    refined["corrections"] = [proposal]
    return bundle, script, refined


def test_source_title_can_be_corrected_without_changing_key_or_duplicating_job():
    bundle, script, refined = inputs()
    assert validate_draft(refined) == validate_model_draft(refined, script) == []
    result, calls = execute(
        script,
        refined,
        bundle={
            **bundle,
            "page": {
                **bundle["page"],
                "blocks": [*bundle["page"]["blocks"], {"text": "工作地点：合肥"}],
            },
        },
    )
    assert calls and len(result["positions"]) == 1
    assert result["positions"][0]["title"]["value"] == "研究助理"
    receipt = result["execution"]["corrections"][0]
    assert receipt["before"]["value"] == "报名方式" and receipt["status"] == "applied"
    merged = result["execution"]["stages"]["merged_draft"]["value"]
    assert merged["positions"][0]["key"] == script["positions"][0]["key"]
    assert merged["positions"][0]["conditions"] == script["positions"][0]["conditions"]


def test_only_explicit_verified_withdrawal_removes_script_position():
    bundle, script, refined = inputs("withdraw_position")
    receipts = []
    result = merge_drafts(script, refined, bundle=bundle, correction_receipts=receipts)
    assert result["positions"] == [] and receipts[0]["status"] == "applied"
    refined.pop("corrections")
    assert merge_drafts(script, refined)["positions"] == script["positions"]


@pytest.mark.parametrize(
    "bad", ["quote", "version", "key", "old_value", "bundle", "conflict", "pointer"]
)
def test_invalid_correction_never_silently_overwrites_or_deletes(bad):
    bundle, script, refined = inputs()
    proposal = refined["corrections"][0]
    if bad == "quote":
        proposal["evidence"][0]["quote"] = "invented"
    elif bad == "version":
        proposal["evidence"][0]["version_id"] = "old-version"
    elif bad == "key":
        proposal["position_key"] = "missing"
        # Ordinary attempted source replacement remains a conflict, not a bypass.
        refined["positions"] = []
    elif bad == "old_value":
        proposal["before"]["value"] = "not-the-old-title"
    elif bad == "bundle":
        proposal["bundle_id"] = "other-bundle"
    elif bad == "pointer":
        proposal["evidence"][0]["pointer"] = "/blocks/99/text"
    else:
        refined["corrections"].append(deepcopy(proposal))
    receipts = []
    result = merge_drafts(script, refined, bundle=bundle, correction_receipts=receipts)
    assert result["positions"] == script["positions"]
    assert receipts and all(r["status"] == "rejected" for r in receipts)
    assert any(i["code"] == "DRAFT_CORRECTION_REJECTED" for i in result["issues"])


def test_unverified_source_title_replacement_still_rejected_and_inputs_immutable():
    bundle, script, refined = inputs()
    originals = deepcopy((bundle, script, refined))
    refined_without_proposal = deepcopy(refined)
    refined_without_proposal.pop("corrections")
    assert validate_model_draft(refined_without_proposal, script)
    merge_drafts(script, refined, bundle=bundle)
    assert (bundle, script, refined) == originals


def test_invalid_typed_replacement_cannot_destroy_healthy_business_fact():
    _, _, refined = inputs()
    proposal = refined["corrections"][0]
    proposal.update(
        field="headcount",
        before={"value": 1, "tag": "source"},
        after={"value": "bad-count", "tag": "source"},
    )
    assert validate_draft(refined)


def test_pending_proposal_is_not_a_normalized_fact_or_script_permission():
    from youju.positions.normalization import normalize_extraction

    bundle, script, refined = inputs()
    assert normalize_extraction(refined)["status"] == "failed"
    with pytest.raises(ValueError, match="Only model refinement"):
        merge_drafts(refined, script, bundle=bundle)


def test_runtime_withdraws_false_section_but_preserves_real_job_and_valid_receipt():
    from youju.onboarding.auditing import runtime_issues

    bundle, script, refined = inputs("withdraw_position")
    real = script_draft()["positions"][0]
    real["key"] = "real-position"
    script["positions"].append(deepcopy(real))
    refined["positions"] = [deepcopy(real)]
    bundle["page"]["blocks"].append({"text": "工作地点：合肥"})
    result, calls = execute(script, refined, bundle=bundle)
    assert calls and len(result["positions"]) == 1
    assert result["positions"][0]["title"] == real["fields"]["title"]
    assert not runtime_issues(result)
    assert result["execution"]["corrections"][0]["operation"] == "withdraw_position"
    assert result["execution"]["corrections"][0]["status"] == "applied"


def test_table_cell_references_bind_to_exact_frozen_text():
    bundle, script, refined = inputs()
    bundle["page"]["tables"][0]["rows"][1][0] = "研究助理"
    proposal = refined["corrections"][0]
    proposal["evidence"] = [
        {
            "version_id": "frozen-v1",
            "pointer": "/tables/0/rows/1/0",
            "start": 0,
            "end": 4,
            "quote": "研究助理",
        }
    ]
    receipts = []
    merged = merge_drafts(script, refined, bundle=bundle, correction_receipts=receipts)
    assert merged["positions"][0]["fields"]["title"]["value"] == "研究助理"
    assert receipts[0]["status"] == "applied"
    proposal["evidence"][0]["pointer"] = "/tables/0/rows/0/0"
    rejected = merge_drafts(script, refined, bundle=bundle)
    assert rejected["positions"][0]["fields"]["title"] == script["positions"][0]["fields"]["title"]
