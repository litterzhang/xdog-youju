"""Independent synthetic clock/counting expectations; never import real holiday claims."""

import copy
from datetime import date, timedelta

import pytest
from test_recipe_accuracy import material
from test_recipe_binding import saved  # noqa: F401

from youju.onboarding.periods import evaluate_period, extract_application_period, validate_period
from youju.onboarding.relative_periods import validate_calendar


def relative(text="报名截止日期为公告发布后第7个自然日", publication="2026-10-20"):
    bundle = material()
    bundle["captured_at"] = "1900-01-01T00:00:00+08:00"
    bundle["page"]["blocks"][-1]["text"] = text
    if publication:
        bundle["page"]["blocks"].append(
            {"id": "pub", "text": f"发布时间：{publication}", "locator": "p[9]"}
        )
    return bundle


def test_relative_natural_days_use_literal_publication_and_preserve_boundary():
    bundle = relative()
    period = extract_application_period(bundle)
    assert period["kind"] == "relative" and period["derivation"][0]["anchor"]["on"] == "2026-10-20"
    assert period["windows"][0]["end"] == "2026-10-27"
    before, errors = evaluate_period(bundle, period, "2026-10-27T23:59:59+08:00")
    assert not errors and before["status"] == "not_expired" and not before["coarse_eligible"]
    after, errors = evaluate_period(bundle, period, "2026-10-28T00:00:00+08:00")
    assert not errors and after["coarse_eligible"] and after["basis"] == "derived_deadline"
    changed = copy.deepcopy(period)
    changed["derivation"][0]["anchor"]["on"] = "1900-01-01"
    assert "PERIOD_DERIVATION" in {e["code"] for e in validate_period(bundle, changed)}
    hidden = copy.deepcopy(period)
    hidden.pop("derivation")
    assert "PERIOD_DERIVATION" in {e["code"] for e in validate_period(bundle, hidden)}


@pytest.mark.parametrize(
    "text,publication,extra",
    [
        ("报名截止日期为公告发布后第7个自然日", None, None),
        ("报名截止日期为公告发布之日起7日内", "2026-10-20", None),
        ("面试报名截止日期为公告发布后第7个自然日", "2026-10-20", None),
        ("报名截止日期为公告发布后第7个自然日，或以附件时间为准", "2026-10-20", None),
        ("报名截止日期为公告发布后第7个自然日", "2026-10-20", "第二批报名另行通知"),
        ("报名截止日期为公告发布后第7个自然日", "2026-10-20", "发布日期：2026-10-21"),
        ("报名截止日期为公告发布后第3个工作日", "2026-10-20", None),
    ],
)
def test_unresolved_anchors_counting_or_scopes_never_authorize_coarse(text, publication, extra):
    bundle = relative(text, publication)
    if extra:
        bundle["page"]["blocks"].append({"id": "extra", "text": extra, "locator": "p[10]"})
    period = extract_application_period(bundle)
    value, errors = evaluate_period(bundle, period, "2027-01-01T00:00:00+08:00")
    assert not errors and value["status"] == "unknown" and not value["coarse_eligible"]


def calendar():
    days = {(date(2026, 10, 20) + timedelta(days=n)).isoformat(): False for n in range(12)}
    for day in ("2026-10-22", "2026-10-24", "2026-10-26"):
        days[day] = True  # Explicit synthetic adjusted weekend, not a real holiday assertion.
    return {"name": "synthetic-reviewed-calendar", "timezone": "Asia/Shanghai", "days": days}


def test_workdays_require_complete_reviewed_calendar_not_weekday_approximation():
    bundle = relative("报名截止日期为公告发布后第3个工作日")
    bundle["period_context"] = {"calendar": {"review_id": "synthetic-review", "value": calendar()}}
    period = extract_application_period(bundle)
    assert period["windows"][0]["end"] == "2026-10-26"
    assert period["context_refs"]["calendar_ref"] == "synthetic-review"
    assert not validate_period(bundle, period)
    incomplete = copy.deepcopy(bundle)
    incomplete["period_context"]["calendar"]["value"]["days"].pop("2026-10-24")
    result, errors = evaluate_period(
        incomplete, extract_application_period(incomplete), "2027-01-01T00:00:00+08:00"
    )
    assert not errors and not result["coarse_eligible"]
    with pytest.raises(ValueError, match="contiguous"):
        validate_calendar(incomplete["period_context"]["calendar"]["value"])


def test_reviewed_calendar_change_invalidates_public_output_and_cached_rule(saved):  # noqa: F811
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from youju.jobs import public_jobs
    from youju.models import AuditRun, SourceRecipeBinding
    from youju.onboarding.auditing import AuditLedger
    from youju.onboarding.period_cache import RuleCache
    from youju.onboarding.temporal_context import attach_context, current, register_calendar

    engine, store, bundle, original = saved
    bundle["page"]["blocks"].extend(
        [
            {"id": "pub", "text": "发布日期：2026-10-10", "locator": "p[2]"},
            {"id": "relative", "text": "报名截止日期为公告发布后第3个工作日", "locator": "p[3]"},
        ]
    )
    reviewed = calendar()
    reviewed["days"] = {
        (date.fromisoformat(k) - timedelta(days=10)).isoformat(): v
        for k, v in reviewed["days"].items()
    }
    first = register_calendar(
        store,
        "source",
        reviewed,
        reviewer="fixture-reviewer",
        reference="synthetic calendar, not a real holiday claim",
    )
    with Session(engine) as session:
        attached = attach_context(store, session, copy.deepcopy(bundle))
    period = extract_application_period(attached)
    value, errors = evaluate_period(attached, period, "2026-10-15T12:00:00+08:00")
    assert not errors and value["deadline"] == "2026-10-16"
    import asyncio

    from youju.extraction_service import RecipeRunner
    from youju.recipe_runtime.contracts import template_candidate

    class StoredFactsScript:
        async def run_script(self, files, module, function, inputs):
            from youju.positions.drafts import ExtractionDraft

            rows = []
            for index, facts in enumerate(original["positions"]):
                rows.append(
                    {
                        "key": f"stored:{index}",
                        "fields": {
                            key: value
                            for key, value in facts.items()
                            if key
                            not in {
                                "schema_version",
                                "conditions",
                                "condition_logic",
                                "deadline_on",
                                "application_start_on",
                                "application_dates",
                            }
                        },
                        "dates": {},
                        "conditions": facts["conditions"],
                        "condition_logic": facts["condition_logic"],
                    }
                )
            return ExtractionDraft(
                positions=rows,
                coverage={"supported": True, "complete": True, "needs_detailed": False},
            ).as_dict()

    output = asyncio.run(
        RecipeRunner(StoredFactsScript()).run(
            template_candidate(),
            attached,
            now_iso="2026-10-15T12:00:00+08:00",
            routing={
                "category": "fixed_expired",
                "mode": "quick",
                "reason": "Deliberately wrong cost hint",
                "policy_version": "fixture",
                "evaluated_at": "2026-10-15T12:00:00+08:00",
            },
        )
    )
    assert "validity" not in output and "quality_tier" not in output
    assert output["positions"][0]["deadline_on"] == {"value": "2026-10-16", "tag": "source"}
    assert output["fact_dependencies"]["calendar"]["review_id"] == first["review_id"]
    outcome = AuditLedger(store).record(attached, "active", "2026-10-15", output, generation=8)
    assert outcome["status"] in {"success", "partial"} and not outcome["errors"], outcome
    RuleCache(store).put(attached, "adapter", period)
    with Session(engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        previous = session.scalar(select(AuditRun).where(AuditRun.status == "complete"))
        summary = {**copy.deepcopy(previous.summary), "generation": 8, "results": [outcome]}
        session.add(
            AuditRun(
                source_id="source",
                recipe_id="active",
                kind="incremental",
                status="complete",
                finished_at=previous.finished_at + timedelta(seconds=1),
                summary=summary,
            )
        )
        binding.generation = 8
    with Session(engine) as session:
        assert list(public_jobs(session, day=date(2026, 10, 15)))
    changed = copy.deepcopy(reviewed)
    changed["days"]["2026-10-14"] = False
    second = register_calendar(
        store, "source", changed, reviewer="fixture-reviewer", reference="synthetic policy revision"
    )
    assert first["review_id"] != second["review_id"]
    with Session(engine) as session:
        from youju.models import AuditResult

        assert not current(session, session.get(AuditResult, outcome["id"]))
        rows = list(public_jobs(session, day=date(2026, 10, 15)))
        assert rows and all("needs_review" in row["display_tags"] for row in rows)
        new_bundle = attach_context(store, session, copy.deepcopy(bundle))
    assert RuleCache(store).get(new_bundle, "adapter") is None
    with pytest.raises(ValueError, match="Temporal policy changed"):
        AuditLedger(store).cached("bundle", "active", "2026-10-15", generation=8)
    assert json.dumps(
        period
    )  # Persisted rules remain independently serializable; no new model call.


def test_relative_rule_under_separate_position_scope_heading_cannot_expire_whole_notice():
    bundle = relative()
    bundle["page"]["blocks"].insert(
        -2, {"id": "scope-heading", "text": "以下报名时间仅适用于岗位甲。", "locator": "h2[2]"}
    )
    value, _ = evaluate_period(
        bundle, extract_application_period(bundle), "2027-01-01T00:00:00+08:00"
    )
    assert not value["coarse_eligible"]
