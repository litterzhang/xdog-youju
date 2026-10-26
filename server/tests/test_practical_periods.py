"""Application evaluator v3 acceptance cases; intentionally red before the host patch.

Only synthetic saved material and explicit clocks: no model, network or recipe execution.
The v2 rule shape remains unchanged; hiring verification is a separate responsibility.
"""

from copy import deepcopy

import pytest
from test_sourcekit import bundle, document

from youju.onboarding import periods
from youju.onboarding.periods import evaluate_period, extract_application_period, validate_period
from youju.onboarding.toolkit import material_issues
from youju.positions.extraction import validate_output
from youju.positions.parsing import extract_position_facts

NOW = "2026-10-24T12:00:00+08:00"
CUTOFF = "本次招聘统一报名截止日期：2026年10月23日。"


def notice(*texts, rows=None):
    value = bundle(texts=texts, rows=rows)
    value["page"]["version_id"] = "synthetic-page-v1"
    return value


def missing_attachment(value, label, *, failed=False):
    url = "https://example.org/synthetic-annex.docx"
    value["status"] = "partial"
    value["page"]["links"].append({"url": url, "label": label, "is_attachment": True})
    if failed:
        annex = document("synthetic-annex", title=label, format="docx")
        annex.update(
            url=url,
            status="failed",
            blocks=[],
            errors=[{"code": "FETCH_FAILED", "message": "Synthetic missing annex"}],
        )
        value["attachments"].append(annex)


def checked(value, clock=NOW):
    period = extract_application_period(value)
    assert period["schema_version"] == "application-period-v2"
    assert validate_period(value, period) == []
    result, errors = evaluate_period(value, period, clock)
    assert errors == []
    assert result["application_period"] == period
    assert result["current_hiring_status"] == "not_verified"
    assert result["material_issues"] == material_issues(value)
    assert result["material_timing_impact"] in {"none", "non_blocking"}
    return period, result


def timing_metadata(result, impact, *, blocked=False):
    assert result["material_timing_impact"] == impact
    assert isinstance(result["timing_blockers"], list)
    assert bool(result["timing_blockers"]) is blocked
    assert result["verification_scope"] == "saved_notice"
    assert result["evaluator_version"] == "application-evaluator-v3.0"


@pytest.mark.parametrize(
    "case",
    ["missing-form-minute", "missing-jobs-headings", "missing-timetable-label", "range-notices"],
)
def test_common_saved_deadline_survives_non_timing_material_gaps(case):
    if case == "missing-form-minute":
        value = notice("本次招聘所有岗位统一报名截止：2026年10月23日14:00。")
        missing_attachment(value, "附件：报名表", failed=True)
        before = "2026-10-23T14:00:59+08:00"
        after = "2026-10-23T14:01:00+08:00"
        expected_end, precision = "2026-10-23T14:00", "minute"
    elif case == "missing-jobs-headings":
        value = notice("本次招聘所有岗位统一报名时间", "报名截止日期", "2026年10月23日")
        missing_attachment(value, "附件：岗位表")
        before = "2026-10-23T23:59:59+08:00"
        after = "2026-10-24T00:00:00+08:00"
        expected_end, precision = "2026-10-23", "day"
    elif case == "missing-timetable-label":
        value = notice(CUTOFF)
        missing_attachment(value, "附件：报名时间表")
        before = "2026-10-23T23:59:59+08:00"
        after = "2026-10-24T00:00:00+08:00"
        expected_end, precision = "2026-10-23", "day"
    else:
        value = notice(
            "本次招聘所有岗位统一报名时间：2026年10月20日至2026年10月23日。",
            "报名截止后另行通知面试安排。",
            "面试时间：2026年10月28日。",
            "资格审查时间：2026年10月25日。",
        )
        before = "2026-10-23T23:59:59+08:00"
        after = "2026-10-24T00:00:00+08:00"
        expected_end, precision = "2026-10-23", "day"
    period, active = checked(value, before)
    _, expired = checked(value, after)
    assert period["kind"] == "fixed_deadline"
    assert period["coverage"] == {"all_scopes": True, "unresolved": []}
    assert {(w["end"], w["precision"]) for w in period["windows"]} == {(expected_end, precision)}
    assert all(w["scope"] == "notice" for w in period["windows"])
    assert active["status"] == "not_expired" and not active["coarse_eligible"]
    assert expired["status"] == "expired" and expired["coarse_eligible"]
    assert expired["deadline"] == "2026-10-23"
    assert expired["basis"] == "explicit_deadline"
    impact = "non_blocking" if material_issues(value) else "none"
    timing_metadata(active, impact)
    timing_metadata(expired, impact)


@pytest.mark.parametrize("flags", ["status-flags", "errors-and-truncation"])
def test_acquisition_flags_preserve_available_dates_positions_and_unknown_fields(flags):
    full = notice(CUTOFF, rows=[["岗位名称", "人数"], ["岗位甲", ""]])
    full_period, full_validity = checked(full)
    full_output = extract_position_facts(full)
    assert full_validity["status"] == "expired" and full_validity["coarse_eligible"]
    assert len(full_output["positions"]) == 1
    timing_metadata(full_validity, "none")
    variants = []
    if flags == "status-flags":
        for status in ("partial", "incomplete", "failed"):
            partial = deepcopy(full)
            partial["status"] = "partial"
            partial["page"].update(status=status, partial=True)
            variants.append(partial)
    else:
        partial = deepcopy(full)
        partial.update(
            status="partial",
            errors=[{"code": "ACQUISITION_MISSING", "message": "Synthetic acquisition warning"}],
        )
        partial["page"].update(
            truncated=True,
            errors=[{"code": "PARSE_PARTIAL", "message": "Synthetic truncation warning"}],
        )
        variants.append(partial)
    for partial in variants:
        period, validity = checked(partial)
        assert period == full_period
        assert validity["status"] == full_validity["status"]
        assert validity["deadline"] == full_validity["deadline"] == "2026-10-23"
        assert validity["coarse_eligible"] and validity["material_issues"]
        timing_metadata(validity, "non_blocking")
        output = extract_position_facts(partial)
        assert output["positions"] == full_output["positions"]
        assert output["status"] in {"success", "partial"}
        position = output["positions"][0]
        assert position["title"] == {"value": "岗位甲", "tag": "source"}
        assert position["deadline_on"] == {"value": "2026-10-23", "tag": "source"}
        for field in ("headcount", "employer", "employment_type", "work_location"):
            assert position[field] == {"value": None, "tag": "unknown"}
        assert (
            validate_output(partial, {**output, "quality_tier": "full", "validity": validity}) == []
        )
    # Missing source facts are not invented just because acquisition warnings are ignored.
    undated = notice("报名提交材料。", rows=[["岗位名称", "人数"], ["岗位甲", ""]])
    undated["page"].update(truncated=True, status="partial")
    undated["status"] = "partial"
    period, validity = checked(undated)
    assert period["windows"] == []
    assert validity["status"] == "unknown" and validity["deadline"] is None
    assert not validity["coarse_eligible"]
    output = extract_position_facts(undated)
    assert output["positions"][0]["deadline_on"] == {"value": None, "tag": "unknown"}
    assert validate_output(undated, {**output, "quality_tier": "full", "validity": validity}) == []


@pytest.mark.parametrize(
    "case", ["delegating-annex", "extension", "conflict", "partial-position-scope"]
)
def test_only_actual_temporal_uncertainty_blocks_whole_notice_expiry(case):
    if case == "delegating-annex":
        value = notice(CUTOFF, "其他岗位报名时间以附件为准。")
        missing_attachment(value, "附件：岗位表")
    elif case == "extension":
        value = notice(CUTOFF, "报名时间延长至2026年10月31日。")
    elif case == "conflict":
        value = notice(CUTOFF, "本次招聘所有岗位统一报名截止日期：2026年10月31日。")
    else:
        value = notice(
            "本次招聘各岗位报名截止时间见岗位表。",
            rows=[["岗位名称", "报名截止日期"], ["岗位甲", "2026-10-23"], ["岗位乙", ""]],
        )
    period, result = checked(value)
    assert result["status"] == "unknown"
    assert result["deadline"] is None and not result["coarse_eligible"]
    assert not period["coverage"]["all_scopes"] or period["conflicts"]
    timing_metadata(result, "non_blocking" if material_issues(value) else "none", blocked=True)
    if case == "delegating-annex":
        # The explicit other scope, NOT the absent annex or its label, is the blocker.
        unflagged = deepcopy(value)
        unflagged.update(status="complete", attachments=[])
        unflagged["page"]["links"] = []
        unflagged_period, unflagged_result = checked(unflagged)
        assert unflagged_period == period
        assert unflagged_result["status"] == "unknown" and not unflagged_result["coarse_eligible"]
        assert unflagged_result["timing_blockers"] == result["timing_blockers"]
        timing_metadata(unflagged_result, "none", blocked=True)
    forged = deepcopy(period)
    forged["coverage"] = {"all_scopes": True, "unresolved": []}
    forged["conflicts"] = []
    assert validate_period(value, forged), "Adapters cannot erase timing uncertainty"
    rejected, errors = evaluate_period(value, forged, NOW)
    assert errors and not rejected["coarse_eligible"]


@pytest.mark.parametrize(
    "texts",
    [
        (
            "应聘人员面试截止日期：2026年10月20日。",
            "报名人员资格审查截止日期：2026年10月21日。",
        ),
        ("报名截止日期：10月23日。", "发布时间：2026年10月01日。"),
        ("报名时间：2026年10月20日、2026年10月23日。",),
    ],
    ids=["interview-and-credentials", "literal-year-missing", "two-dates-not-a-range"],
)
def test_non_cutoffs_and_ambiguous_dates_do_not_get_promoted(texts):
    value = notice(*texts)
    period, result = checked(value)
    assert period["windows"] == []
    assert result["status"] == "unknown" and result["deadline"] is None
    assert not result["coarse_eligible"]
    assert result["basis"] != "explicit_deadline"
    assert result["verification_scope"] == "saved_notice"


@pytest.mark.parametrize(
    "text,kind",
    [
        ("本招聘启事长期有效。", "open_ended"),
        ("本招聘岗位招满为止。", "until_filled"),
    ],
    ids=["open-ended", "until-filled"],
)
def test_rolling_is_a_known_rule_not_an_unsupported_clock_or_verified_vacancy(text, kind):
    value = notice(text)
    period, result = checked(value)
    assert period["kind"] == kind and period["text"] and period["evidence_ids"]
    assert result["status"] == "unknown" and result["deadline"] is None
    assert not result["coarse_eligible"] and result["basis"] == "rolling"
    assert result["verification_scope"] == "saved_notice"
    # Adding a common historic cutoff must not silently discard the rolling scope.
    mixed = notice(CUTOFF, text)
    _, mixed_result = checked(mixed)
    assert mixed_result["status"] == "unknown" and not mixed_result["coarse_eligible"]
    timing_metadata(mixed_result, "none", blocked=True)


def test_v3_recomputes_old_evaluated_clock_and_changes_rule_cache_identity(monkeypatch):
    value = notice(CUTOFF)
    period = extract_application_period(value)
    frozen_period = deepcopy(period)
    old_clock, errors = evaluate_period(value, period, "2026-10-23T23:59:59+08:00")
    assert not errors and old_clock["status"] == "not_expired"
    old_snapshot = deepcopy(old_clock)
    new_clock, errors = evaluate_period(value, period, "2026-10-24T00:00:00+08:00")
    assert not errors and new_clock["status"] == "expired"
    assert new_clock["evaluated_at"] == "2026-10-24T00:00:00+08:00"
    assert old_clock == old_snapshot and period == frozen_period
    key = periods.rule_key(value, adapter_digest="synthetic-adapter")
    assert periods.rule_key(value, adapter_digest="synthetic-adapter") == key
    assert periods.rule_key(value, adapter_digest="changed-adapter") != key
    version = periods.EVALUATOR_VERSION
    with monkeypatch.context() as patch:
        patch.setattr(periods, "EVALUATOR_VERSION", "application-evaluator-v2.2")
        legacy_key = periods.rule_key(value, adapter_digest="synthetic-adapter")
    assert version == "application-evaluator-v3.0"
    assert key != legacy_key
    timing_metadata(new_clock, "none")
