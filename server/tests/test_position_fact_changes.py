"""Synthetic offline reviewed-change cases: no source store, model or network."""

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

from youju.positions.changes import DEPENDENCY_CONTRACT, apply_fact_changes
from youju.positions.deadlines import date_fact
from youju.positions.drafts import ExtractionDraft, validate_draft
from youju.positions.normalization import normalize_extraction


def tagged(value):
    return {"value": value, "tag": "source"}


def draft():
    return ExtractionDraft(
        notice_facts={
            "fields": {
                "hiring_unit": tagged("合成单位"),
                "application_rule": tagged("fixed_deadline"),
            },
            "dates": {"start": date_fact(2026, 10, 1), "end": date_fact(2026, 10, 20)},
        },
        positions=[
            {"key": "row:1", "fields": {"title": tagged("助理"), "headcount": tagged(2)}},
            {
                "key": "row:2",
                "fields": {"title": tagged("助理"), "application_rule": tagged("open_ended")},
                "dates": {"start": date_fact(2026, 10, 2), "end": date_fact(2026, 10, 25)},
            },
            {"key": "row:3", "fields": {"title": tagged("技术员")}, "dates": {"end": None}},
        ],
        coverage={"supported": True, "complete": True, "needs_detailed": True, "rows": 3},
    ).as_dict()


def context(text="本次招聘报名截止时间延长至2026-11-30。", effect="extension"):
    return {
        "bundle_id": "original",
        "change": {
            "review_id": "review-1",
            "effect": effect,
            "target_bundle_id": "original",
            "event_bundle_id": "event-1",
            "status": "current",
            "supersedes": None,
        },
        "change_documents": [
            {
                "change_review_id": "review-1",
                "event_bundle_id": "event-1",
                "version_id": "v1",
                "blocks": [{"id": "p1", "text": text}],
                "tables": [],
            }
        ],
    }


def assert_unresolved(result, original):
    assert result["disposition"] is None
    assert result["dependencies"]["change"]["outcome"] == "unresolved"
    actual = result["draft"]
    assert len(actual["positions"]) == len(original["positions"])
    for old, new in zip(
        [original["notice_facts"], *original["positions"]],
        [actual["notice_facts"], *actual["positions"]],
        strict=True,
    ):
        assert new["dates"]["end"] is None
        assert new["dates"].get("start") == old.get("dates", {}).get("start")
        assert new["fields"] == old["fields"]
        assert new.get("key") == old.get("key")
    assert actual["coverage"] == {
        **original["coverage"],
        "complete": False,
        "needs_detailed": False,
    }
    issue = actual["issues"][-1]
    assert issue["code"] == "ASSOCIATED_CHANGE_UNRESOLVED"
    assert issue["owner"] == "position_domain"
    assert issue["message"]
    assert not validate_draft(actual)
    normalized = normalize_extraction(actual)
    assert len(normalized["positions"]) == len(original["positions"])
    assert all(
        p["deadline_on"] == {"value": None, "tag": "unknown"} for p in normalized["positions"]
    )


@pytest.mark.parametrize("ctx", [None, {}, {"bundle_id": "original"}])
def test_no_associated_change_is_a_deep_copy(ctx):
    data = draft()
    result = apply_fact_changes(data, ctx)
    assert result == {"draft": data, "disposition": None, "dependencies": None}
    result["draft"]["positions"][0]["fields"]["title"]["value"] = "changed"
    assert data["positions"][0]["fields"]["title"] == tagged("助理")


@pytest.mark.parametrize("scope", ["本次招聘", "本招聘", "原招聘公告", "所有岗位", "全部岗位"])
@pytest.mark.parametrize("literal", ["2026-11-30", "2026年11月30日"])
def test_extension_replaces_every_endpoint_but_not_other_facts(scope, literal):
    data, ctx = draft(), context(f"{scope}报名截止时间延长至{literal}。")
    before, saved = copy.deepcopy(data), copy.deepcopy(ctx)
    result = apply_fact_changes(data, ctx)
    assert data == before and ctx == saved
    assert set(result) == {"draft", "disposition", "dependencies"}
    assert result["disposition"] is None
    changed = result["draft"]
    for old, new in zip(
        [before["notice_facts"], *before["positions"]],
        [changed["notice_facts"], *changed["positions"]],
        strict=True,
    ):
        assert new["dates"]["end"] == date_fact(2026, 11, 30)
        assert new["dates"].get("start") == old.get("dates", {}).get("start")
        assert new["fields"] == {**old["fields"], "application_rule": tagged("fixed_deadline")}
    assert changed["coverage"] == before["coverage"]
    assert not validate_draft(changed)
    normalized = normalize_extraction(changed)
    assert len(normalized["positions"]) == 3
    assert all(p["deadline_on"] == tagged("2026-11-30") for p in normalized["positions"])
    assert not normalized["errors"]
    assert result["dependencies"] == {
        "contract": "position-fact-dependencies-v1",
        "change": {
            key: ctx["change"][key]
            for key in ("review_id", "effect", "target_bundle_id", "event_bundle_id", "status")
        }
        | {"event_version_id": "v1", "outcome": "applied"},
    }
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "text",
    [
        "本招聘取消。",
        "取消本次招聘。",
        "现决定取消本次招聘。",
        "原招聘公告终止。",
        "所有岗位撤销。",
        "全部岗位停止。",
        "本次招聘予以取消。",
    ],
)
def test_literal_cancellation_preserves_coverage_without_fabricating_jobs(text):
    data = draft()
    data["coverage"].update(supported=False, complete=False)
    data["issues"] = [{"code": "CAPTURE_WARNING", "message": "synthetic missing attachment"}]
    result = apply_fact_changes(data, context(text, "cancellation"))
    assert result["disposition"] == "cancelled"
    assert result["draft"]["positions"] == []
    assert result["draft"]["notice_facts"] == data["notice_facts"]
    assert result["draft"]["issues"] == data["issues"]
    assert result["draft"]["coverage"] == {**data["coverage"], "needs_detailed": False}
    assert result["dependencies"]["change"]["outcome"] == "cancelled"
    assert not validate_draft(result["draft"])


@pytest.mark.parametrize(
    "effect,text",
    [
        ("extension", "本次招聘报名截止时间延长至2026-11-30。"),
        ("cancellation", "本次招聘取消。"),
    ],
)
def test_readable_literals_ignore_incomplete_acquisition_bookkeeping(effect, text):
    data, ctx = draft(), context(text, effect)
    ctx.update(complete=False, missing_attachments=["unavailable"])
    ctx["change_documents"][0].update(
        complete=False, truncated=True, parse_status="error", errors=["reader warning"]
    )
    data["coverage"]["complete"] = False
    result = apply_fact_changes(data, ctx)
    assert result["dependencies"]["change"]["outcome"] in {"applied", "cancelled"}
    assert result["draft"]["coverage"]["complete"] is False


@pytest.mark.parametrize(
    "text",
    [
        "招聘报名延长至2026-11-30。",  # No explicit whole-notice scope.
        "本次招聘部分岗位报名延长至2026-11-30。",
        "本次招聘岗位A报名延长至2026-11-30。",
        "本次招聘报名延长至2026-11-30，仅适用于技术员。",
        "如果人数不足，本次招聘报名延长至2026-11-30。",
        "本次招聘报名不延期至2026-11-30。",
        "本次招聘报名拟延期至2026-11-30。",
        "本次招聘报名延长至2026-11-30，但须另行确认。",
        "本次招聘报名延长至下月。",
        "本次招聘报名延长至11月30日。",
        "本次招聘报名延长至2026-02-30。",
        "本次招聘报名延长至2026-11-30 17:00。",
        "本次招聘面试报名延长至2026-11-30。",
        "本次招聘报名延长至2026-11-30，招满即止。",
        "本次招聘报名延长至2026-11-30。面试于2026-12-01。",
        "本次招聘报名延长至2026-11-30。原截止2026-10-20。",
        "本次招聘报名延长至2026-11-30。招聘人数增加2人。",
        "本次招聘报名延长至2026-11-30。专业要求调整。",
        "本次招聘报名延长至2026-11-30。本次招聘取消。",
        "本次招聘报名延长至2026-11-30。全部岗位报名延期至2026-12-01。",
        "本次招聘报名延长至2026-11-30（以另行通知为准）。",
        "本次招聘\n报名延长至2026-11-30。",  # Cannot borrow scope.
    ],
)
def test_unsupported_extension_is_domain_uncertainty_not_job_loss(text):
    data = draft()
    assert_unresolved(apply_fact_changes(data, context(text)), data)


@pytest.mark.parametrize(
    "text",
    [
        "本次招聘未取消。",
        "本次招聘不取消。",
        "本次招聘暂不取消。",
        "本次招聘可能取消。",
        "如无合适人选，本次招聘取消。",
        "本次招聘部分岗位取消。",
        "仅取消本次招聘。",
        "本次招聘报名延长至2026-11-30。",
        "招聘结束。",
        "",
        "本次招聘取消。部分岗位继续招聘。",
        "本次招聘取消。岗位条件调整。",
    ],
)
def test_cancellation_metadata_never_deletes_jobs_without_literal_support(text):
    data = draft()
    assert_unresolved(apply_fact_changes(data, context(text, "cancellation")), data)


@pytest.mark.parametrize(
    "where,key,value",
    [
        ("change", "status", "stale"),
        ("change", "status", None),
        ("change", "effect", "other"),
        ("change", "effect", []),
        ("change", "target_bundle_id", "other"),
        ("change", "review_id", ""),
        ("change", "supersedes", "review-1"),
        ("change", "supersedes", []),
        ("document", "change_review_id", "other"),
        ("document", "event_bundle_id", "other"),
        ("document", "version_id", None),
        ("document", "blocks", "invalid"),
        ("document", "blocks", [{}]),
        ("document", "tables", [{"rows": []}]),
        ("context", "bundle_id", None),
        ("context", "change_documents", {}),
        ("context", "change", None),
    ],
)
def test_malformed_or_stale_known_association_keeps_jobs(where, key, value):
    data, ctx = draft(), context()
    target = {"change": ctx["change"], "document": ctx["change_documents"][0], "context": ctx}[
        where
    ]
    target[key] = value
    assert_unresolved(apply_fact_changes(data, ctx), data)


@pytest.mark.parametrize("kind", ["absent", "duplicate", "missing_supersedes", "non_object"])
def test_ambiguous_missing_or_malformed_context(kind):
    data, ctx = draft(), context()
    if kind == "absent":
        ctx["change_documents"] = []
    elif kind == "duplicate":
        ctx["change_documents"] *= 2
    elif kind == "missing_supersedes":
        del ctx["change"]["supersedes"]
    else:
        ctx = []
    result = apply_fact_changes(data, ctx)
    assert_unresolved(result, data)
    if kind in {"absent", "duplicate", "non_object"}:
        assert result["dependencies"]["change"]["event_version_id"] is None


def test_unresolved_receipt_is_stable_and_uses_current_event_version():
    data, ctx = draft(), context()
    ctx["change"]["status"] = "stale"
    before = copy.deepcopy((data, ctx))
    result = apply_fact_changes(data, ctx)
    assert result["dependencies"]["change"]["event_version_id"] == "v1"
    assert (data, ctx) == before
    assert apply_fact_changes(result["draft"], ctx) == result
    ctx["change_documents"][0]["version_id"] = "v2"
    assert apply_fact_changes(data, ctx)["dependencies"] != result["dependencies"]


@pytest.mark.parametrize("end", ["2026-09-30", "2026-10-21"])
def test_extension_cannot_precede_start_or_shorten_any_known_position_end(end):
    data = draft()
    assert_unresolved(apply_fact_changes(data, context(f"本次招聘报名延长至{end}。")), data)


def test_no_base_deadline_or_complete_original_notice_required():
    data = draft()
    data["positions"] = []
    data["notice_facts"] = {}
    data["coverage"].update(supported=False, complete=False)
    result = apply_fact_changes(data, context())
    assert result["dependencies"]["change"]["outcome"] == "applied"
    assert result["draft"]["positions"] == []
    assert result["draft"]["notice_facts"]["dates"]["end"] == date_fact(2026, 11, 30)


@pytest.mark.parametrize("kind", ["blocks", "text", "body", "documents"])
def test_bounded_material_abstains(kind):
    data, ctx = draft(), context()
    doc = ctx["change_documents"][0]
    if kind == "blocks":
        doc["blocks"] *= 513
    elif kind == "text":
        doc["blocks"].append({"text": "x" * 8193})
    elif kind == "body":
        doc["blocks"] += [{"text": "x" * 8192}] * 8
    else:
        ctx["change_documents"] = [doc] * 129
    assert_unresolved(apply_fact_changes(data, ctx), data)


def test_separate_block_exceptions_are_not_discarded():
    data, ctx = draft(), context()
    ctx["change_documents"][0]["blocks"].insert(0, {"text": "仅适用于部分岗位"})
    assert_unresolved(apply_fact_changes(data, ctx), data)


def test_explicit_navigation_is_not_amendment_prose():
    ctx = context()
    ctx["change_documents"][0]["blocks"].insert(
        0, {"text": "取消本次招聘", "region_kind": "navigation"}
    )
    assert apply_fact_changes(draft(), ctx)["dependencies"]["change"]["outcome"] == "applied"


def test_domain_only_imports_and_exact_interface():
    import youju.positions.changes as changes

    assert list(inspect.signature(changes.apply_fact_changes).parameters) == ["draft", "context"]
    source = Path(changes.__file__).read_text()
    tree = ast.parse(source)
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert set(modules) == {"re", "copy", "deadlines", "drafts"}
    with pytest.raises(ValueError):
        apply_fact_changes({}, None)


@pytest.mark.parametrize(
    "effect,text",
    [
        ("extension", "本次招聘报名延期至2026-11-30。"),
        ("cancellation", "本次招聘取消。"),
    ],
)
def test_runtime_base_then_final_application_is_idempotent(effect, text):
    data, ctx = draft(), context(text, effect)
    first = apply_fact_changes(data, ctx)
    assert apply_fact_changes(first["draft"], ctx) == first
    assert first["dependencies"]["contract"] == DEPENDENCY_CONTRACT
    if effect == "cancellation":
        assert first["draft"]["positions"] == []
        assert first["disposition"] == "cancelled"


@pytest.mark.parametrize(
    "suffix",
    [
        "以上延期无效。",
        "以上仅为示例。",
        "招聘数量改为3人。",
        "学历要求更正。",
    ],
)
def test_disavowal_or_other_changes_in_separate_blocks_are_unresolved(suffix):
    data, ctx = draft(), context()
    ctx["change_documents"][0]["blocks"].append({"text": suffix})
    assert_unresolved(apply_fact_changes(data, ctx), data)


def test_unknown_region_metadata_cannot_crash_readable_semantics():
    ctx = context()
    ctx["change_documents"][0]["blocks"][0]["region_kind"] = []
    assert apply_fact_changes(draft(), ctx)["dependencies"]["change"]["outcome"] == "applied"


@pytest.mark.parametrize(
    "text",
    [
        "本次招聘取消。甲岗位将保留。",
        "本次招聘取消。技术员岗位正常报名。",
        "本次招聘取消。原计划继续招聘。",
        "本次招聘取消。乙岗位不受影响。",
    ],
)
def test_cancellation_cannot_discard_position_exceptions(text):
    data = draft()
    assert_unresolved(apply_fact_changes(data, context(text, "cancellation")), data)


@pytest.mark.parametrize("scope", ["本次招聘", "本招聘", "原招聘公告", "所有岗位", "全部岗位"])
@pytest.mark.parametrize("action", ["取消", "予以取消"])
def test_all_explicit_notice_wide_scopes_can_cancel(scope, action):
    result = apply_fact_changes(draft(), context(f"{scope}{action}。", "cancellation"))
    assert result["disposition"] == "cancelled"
    assert result["draft"]["positions"] == []
    assert result["dependencies"]["change"]["outcome"] == "cancelled"


@pytest.mark.parametrize("other", ["全部岗位调整。", "所有岗位变更。"])
def test_notice_wide_scope_does_not_authorize_other_substantive_changes(other):
    data, ctx = draft(), context()
    ctx["change_documents"][0]["blocks"].append({"text": other})
    assert_unresolved(apply_fact_changes(data, ctx), data)
