"""Recognizable saved tables/date prose, employment negation and partial captures."""

import pytest

from youju.positions.deadlines import date_fact, dates_from_text
from youju.positions.drafts import validate_draft
from youju.positions.employment import employment_from_text
from youju.positions.extraction import validate_output
from youju.positions.normalization import normalize_extraction
from youju.positions.parsing import extract_position_draft, extract_position_facts


def bundle(*texts, rows=None, status="complete"):
    return {
        "status": status,
        "page": {
            "status": "ok",
            "format": "html",
            "blocks": [{"id": f"b{n}", "text": t} for n, t in enumerate(texts)],
            "tables": [{"rows": rows or [["岗位名称", "人数"], ["助理", "2"]]}],
        },
    }


@pytest.mark.parametrize(
    "text,expected,clock",
    [
        ("提交报名材料，截止期为 202 6 年 11 月 14 日", "2026-11-14", None),
        ("报名截止2026年11月14日24:00", "2026-11-14", "24:00"),
        ("请于2026年11月14日前提交报名材料", "2026-11-14", None),
        ("报名截止2026年11月14日，面试通知2026年12月1日", "2026-11-14", None),
        ("报名截止2026年11月14日。公示时间2026年12月1日", "2026-11-14", None),
    ],
)
def test_precise_application_dates_not_later_phases(text, expected, clock):
    dates, rule, errors = dates_from_text(text)
    assert not errors
    assert dates["end"]["date"] == expected
    assert dates["end"]["time"] == clock
    assert rule == "fixed_deadline"


def test_heading_paired_until_phrase_and_no_host_year():
    result = extract_position_facts(bundle("报名时限", "自公告发布之日起，至2014年6月30日止"))
    assert result["positions"][0]["deadline_on"] == {"value": "2014-06-30", "tag": "source"}
    missing_year = extract_position_facts(bundle("报名时间", "截止11月14日"))
    job = missing_year["positions"][0]
    assert job["deadline_on"]["value"] is None
    assert job["application_dates"]["value"]["end"]["year_status"] == "unknown"


def test_same_year_range_normalizes_the_written_year_without_ai_or_host_clock():
    result = extract_position_facts(bundle("报名时间为2026年10月1日至11月14日"))
    job = result["positions"][0]
    assert job["application_start_on"] == {"value": "2026-10-01", "tag": "source"}
    assert job["deadline_on"] == {"value": "2026-11-14", "tag": "source"}
    assert job["application_dates"]["value"]["end"]["year_status"] == "explicit"
    assert not validate_output({}, result)


def test_invalid_and_conflicting_common_dates_do_not_erase_jobs():
    material = bundle("报名截止2026年11月14日", "报名截止2026年11月15日")
    result = extract_position_facts(material)
    assert len(result["positions"]) == 1
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert any(e["code"] == "APPLICATION_DATE_CONFLICT" for e in result["errors"])
    invalid = extract_position_facts(bundle("报名截止2026年2月30日"))
    assert len(invalid["positions"]) == 1
    assert invalid["positions"][0]["deadline_on"]["value"] is None


def test_per_position_temporal_overrides_and_until_filled():
    material = bundle(
        "报名截止2026年11月30日",
        rows=[["岗位名称", "报名截止时间"], ["A", "2026年11月14日"], ["B", "招满即止"], ["C", ""]],
    )
    result = extract_position_facts(material)
    a, b, c = result["positions"]
    assert a["deadline_on"]["value"] == "2026-11-14"
    assert b["deadline_on"]["value"] is None
    assert b["application_rule"]["value"] == "until_filled"
    assert c["deadline_on"]["value"] == "2026-11-30"


def test_common_endpoint_tables_and_per_position_ranges():
    material = bundle(rows=[["报名开始时间", "报名截止时间"], ["2026年10月1日", "2026年11月1日"]])
    material["page"]["tables"].append(
        {"rows": [["岗位名称", "报名时间"], ["A", "2026年10月2日至2026年11月2日"]]}
    )
    result = extract_position_facts(material)
    assert result["positions"][0]["application_start_on"]["value"] == "2026-10-02"
    assert result["positions"][0]["deadline_on"]["value"] == "2026-11-02"


def test_partial_capture_is_not_a_model_fallback_predicate():
    material = bundle("报名截止2026年11月14日", status="partial")
    material["page"]["links"] = [
        {"url": "https://example.invalid/missing.xlsx", "is_attachment": True}
    ]
    draft = extract_position_draft(material)
    assert not validate_draft(draft)
    assert draft["coverage"]["supported"]
    assert not draft["coverage"]["complete"]
    assert not draft["coverage"]["needs_detailed"]
    result = normalize_extraction(draft)
    assert result["status"] == "partial"
    assert result["positions"][0]["deadline_on"]["value"] == "2026-11-14"
    assert not validate_output(material, result)


def test_unsupported_semantics_requests_detail_and_preserves_known_values():
    material = bundle(
        rows=[["岗位名称", "学历", "人数"], ["助理", "硕士研究生（优秀者放宽）", "若干名"]]
    )
    result = extract_position_facts(material)
    assert result["coverage"]["needs_detailed"]
    job = result["positions"][0]
    assert job["headcount"]["value"] is None
    assert job["headcount_description"]["value"] == "若干名"
    assert job["requirements_text"]["value"] == "硕士研究生（优秀者放宽）"
    assert job["conditions"][0]["type"] == "other"


def test_common_conditions_and_partial_row_are_retained_without_quotes():
    result = extract_position_facts(
        bundle(
            "基本条件：全日制本科及以上",
            rows=[["岗位名称", "学位", "工作地点"], ["A", "博士学位", "合肥"], ["B", "硕士学位"]],
        )
    )
    assert len(result["positions"]) == 2
    assert result["positions"][0]["conditions"][0]["scope"] == "notice"
    assert {c["type"] for c in result["positions"][0]["conditions"]} == {
        "education",
        "study_form",
        "degree",
    }
    assert result["positions"][0]["work_location"]["value"] == "合肥"
    assert result["positions"][1]["work_location"]["value"] is None
    assert not validate_output({}, result)


@pytest.mark.parametrize(
    "text,staff,employment",
    [
        ("某大学是事业单位", None, None),
        ("本岗位不纳入事业编制，实行劳务派遣", "non_established", "labor_dispatch"),
        ("非事业编制，劳动合同制", "non_established", "labor_contract"),
        ("本岗位纳入事业编制管理", "established", None),
        ("事业编制", "established", None),
        ("不采用劳务派遣，实行人事代理", None, "personnel_agency"),
        ("劳动合同期限三年", None, None),
        ("实行备案制管理", "filing_system", None),
    ],
)
def test_employment_is_not_institution_identity_and_supports_negation(text, staff, employment):
    fields, errors = employment_from_text(text)
    assert not errors
    assert fields["staffing_status"].value == staff
    assert fields["employment_type"].value == employment


def test_explicit_employment_conflicts_are_not_keyword_first_wins():
    fields, errors = employment_from_text(
        "本岗位纳入事业编制；本岗位不纳入事业编制；人事代理；劳务派遣"
    )
    assert fields["staffing_status"].value is None
    assert fields["employment_type"].value is None
    assert len(errors) == 2


def test_structured_override_unknown_blocks_common_source_date():
    material = extract_position_draft(bundle("报名截止2026年11月14日"))
    material["positions"][0]["dates"] = {"end": date_fact(None, 12, 1)}
    result = normalize_extraction(material)
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert result["positions"][0]["application_dates"]["value"]["end"]["month"] == 12


def test_position_named_heading_is_not_notice_wide_and_round_dates_stay_scoped():
    material = bundle(
        "科研助理岗位报名时限",
        "至2026年11月14日止",
        rows=[["岗位名称"], ["科研助理"], ["技术人员"]],
    )
    result = extract_position_facts(material)
    assert result["positions"][0]["deadline_on"]["value"] == "2026-11-14"
    assert result["positions"][1]["deadline_on"]["value"] is None
    result = extract_position_facts(bundle("滚动招聘，本轮报名截止时间2026年11月14日"))
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert any(e["code"] == "SCOPED_APPLICATION_DATE" for e in result["errors"])


def test_generic_postgraduate_education_is_not_inferred_degree():
    material = bundle(rows=[["岗位名称", "学历"], ["科研助理", "全日制硕 士研究生及以上"]])
    result = extract_position_facts(material)
    assert [c["type"] for c in result["positions"][0]["conditions"]] == ["study_form", "education"]
    assert result["positions"][0]["conditions"][1]["value"] == {"level": "master"}
    assert not result["coverage"]["needs_detailed"]


def test_major_alternatives_are_not_split_into_conjunctive_requirements():
    material = bundle(rows=[["岗位名称", "专业"], ["科研助理", "物理、化学、材料"]])
    result = extract_position_facts(material)
    conditions = result["positions"][0]["conditions"]
    assert len(conditions) == 1
    assert conditions[0]["value"] == {"names": ["物理", "化学", "材料"], "unrestricted": False}


def test_specific_staffing_declaration_is_not_inherited_by_other_positions():
    material = bundle("科研助理岗位不纳入事业编制", rows=[["岗位名称"], ["科研助理"], ["技术人员"]])
    result = extract_position_facts(material)
    assert result["positions"][0]["staffing_status"]["value"] == "non_established"
    assert result["positions"][1]["staffing_status"]["value"] is None
