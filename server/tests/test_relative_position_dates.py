"""Shared relative job-date derivation; no calendar guess, provider or notice oracle."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from youju.positions.drafts import ExtractionDraft
from youju.positions.normalization import normalize_extraction
from youju.positions.relative_dates import apply_relative_dates


def example(text="报名截止日期为公告发布后第7个自然日"):
    material = {
        "captured_at": "1900-01-01T00:00:00+08:00",
        "page": {
            "version_id": "v1",
            "blocks": [
                {"id": "rule", "text": text},
                {"id": "pub", "text": "发布日期：2026年10月10日"},
            ],
        },
    }
    draft = ExtractionDraft(
        positions=[
            {
                "key": "row:1",
                "fields": {"title": {"value": "研发岗", "tag": "source"}},
                "dates": {},
                "conditions": [],
                "condition_logic": None,
            }
        ],
        coverage={"supported": True, "complete": True, "needs_detailed": False},
    ).as_dict()
    return draft, material


def test_literal_natural_days_derive_source_date_without_capture_clock():
    draft, material = example()
    before = deepcopy((draft, material))
    result = apply_relative_dates(draft, material)
    assert (draft, material) == before
    facts = normalize_extraction(result["draft"])["positions"][0]
    assert facts["deadline_on"] == {"value": "2026-10-17", "tag": "source"}
    assert result["dependencies"]["relative"]["anchor"]["version_id"] == "v1"


def test_workdays_use_reviewed_day_flags_including_weekends():
    draft, material = example("报名截止日期为公告发布后第3个工作日")
    days = {
        (date(2026, 10, 10) + timedelta(days=n)).isoformat(): n in {1, 3, 4, 7} for n in range(10)
    }
    result = apply_relative_dates(
        draft,
        material,
        {
            "calendar": {
                "review_id": "calendar-v1",
                "value": {"name": "synthetic", "timezone": "Asia/Shanghai", "days": days},
            }
        },
    )
    assert (
        normalize_extraction(result["draft"])["positions"][0]["deadline_on"]["value"]
        == "2026-10-14"
    )
    assert result["dependencies"]["calendar"]["review_id"] == "calendar-v1"


@pytest.mark.parametrize(
    "problem", ["missing_anchor", "conflicting_anchor", "no_calendar", "partial_scope"]
)
def test_unresolved_relative_rule_does_not_invent_a_date_or_discard_jobs(problem):
    draft, material = example("报名截止日期为公告发布后第3个工作日")
    if problem == "missing_anchor":
        material["page"]["blocks"].pop()
    elif problem == "conflicting_anchor":
        material["page"]["blocks"].append({"id": "pub2", "text": "发布日期：2026-10-11"})
    elif problem == "partial_scope":
        material["page"]["blocks"].append({"id": "scope", "text": "仅适用于部分岗位"})
    result = apply_relative_dates(draft, material)
    facts = normalize_extraction(result["draft"])["positions"]
    assert len(facts) == 1 and facts[0]["deadline_on"]["value"] is None
    assert result["dependencies"]["relative"]["outcome"] == "unresolved"
