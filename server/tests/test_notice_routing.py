"""Synthetic, offline routing tests; no recipes, real sources or model calls."""

import ast
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from youju.notices import RoutingCategory, RoutingDecision, RoutingPolicy, decide_route

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone(timedelta(hours=8)))


def notice(*texts):
    return {
        "page": {
            "format": "html",
            "blocks": [
                {"id": f"p:{i}", "text": text, "locator": f"p:{i}"} for i, text in enumerate(texts)
            ],
        },
        "attachments": [],
        "captured_at": "2026-10-24T12:00:00+08:00",
    }


def route(bundle, category, mode, **kwargs):
    result = decide_route(bundle, now=NOW, **kwargs)
    assert result.category == category
    assert result.mode == mode
    assert result.evaluated_at == NOW
    assert result.reason
    return result


@pytest.mark.parametrize(
    ("text", "category", "mode"),
    [
        ("报名截止日期：2026年10月23日。", "fixed_expired", "quick"),
        ("报名截止：2026年10月24日09:00。", "fixed_active", "detailed"),
        ("报名截止：2026年11月24日。", "fixed_active", "detailed"),
        ("报名时间：2014年6月1日至2014年6月30日。", "fixed_expired", "quick"),
        ("报名时间：2026年10月20日至11月30日。", "fixed_active", "detailed"),
        ("报名时间：2026年10月20日至30日。", "fixed_active", "detailed"),
        ("报名截止：2026/10/23", "fixed_expired", "quick"),
        ("报名截止：2026-10-25", "fixed_active", "detailed"),
        ("报名截止：2026.10.25", "fixed_active", "detailed"),
        ("报名时间：2014年6月1日-2014年6月30日", "fixed_expired", "quick"),
    ],
)
def test_fixed_hints_only_control_cost(text, category, mode):
    route(notice(text), category, mode)


def test_paired_historical_heading_and_following_text():
    route(notice("（三）报名时限", "至2014年6月30日止"), "fixed_expired", "quick")


@pytest.mark.parametrize("flag", ["partial", "failed", "incomplete"])
def test_available_blocks_win_over_acquisition_flags_and_missing_annexes(flag):
    value = notice("报名时限", "至2014年6月30日止")
    original = decide_route(value, now=NOW)
    value.update(status=flag, errors=[{"code": "MISSING_ANNEX"}])
    value["page"].update(
        status=flag,
        truncated=True,
        links=[{"label": "报名时间表", "is_attachment": True}],
    )
    value["attachments"].append({"status": "failed", "blocks": []})
    assert decide_route(value, now=NOW) == original


def test_readable_attachment_can_supply_cost_hint_without_page():
    value = {"attachments": [notice("报名截止2014年6月30日")["page"]]}
    route(value, "fixed_expired", "quick")


@pytest.mark.parametrize("term", ["长期有效", "常年招聘", "招满即止", "until filled", "open ended"])
def test_old_start_date_can_lower_open_ended_cost_without_a_fixed_endpoint(term):
    value = notice("报名时间：2020年1月1日起，" + term)
    route(value, "undated_low_priority", "quick")


def test_mixed_scopes_keep_open_scope_and_prefer_later_fixed_hint():
    route(
        notice("岗位甲报名截止2020年1月1日", "岗位乙报名截止2026年12月1日"),
        "fixed_active",
        "detailed",
    )
    route(
        notice("岗位甲报名截止2020年1月1日", "岗位乙长期有效"),
        "undated_low_priority",
        "quick",
    )


@pytest.mark.parametrize("label", ["发布日期", "发布时间", "公告发布日期", "更新日期", "最后更新"])
def test_labeled_publication_or_update_date_can_lower_priority(label):
    route(notice(f"{label}：2014年6月30日", "报名请提交材料"), "undated_low_priority", "quick")


def test_update_date_prevents_old_publication_from_lowering_priority():
    route(
        notice("发布日期：2014年6月30日", "更新时间：2026年10月23日", "长期有效"),
        "undated_high_priority",
        "detailed",
    )


def test_age_boundary_and_configurable_version_are_deterministic():
    value = notice("发布日期：2025年10月24日", "报名请提交材料")
    default = route(value, "undated_low_priority", "quick")
    custom = route(
        value, "undated_high_priority", "detailed", policy=RoutingPolicy(old_age_days=366)
    )
    assert default.policy_version != custom.policy_version
    assert RoutingPolicy().version == RoutingPolicy().version == default.policy_version
    assert decide_route(value, now=NOW) == default


@pytest.mark.parametrize("day", ["1900-01-01T00:00:00Z", "2026-10-24T12:00:00+08:00"])
def test_capture_and_untrusted_metadata_never_count_as_publication(day):
    value = notice("报名请提交材料")
    value.update(captured_at=day, acquired_at=day, published_at=day, updated_at=day)
    value["page"].update(captured_at=day, published_at=day, updated_at=day)
    route(value, "undated_high_priority", "detailed")


@pytest.mark.parametrize(
    "text",
    [
        "公告日期不详，2014年招聘资料",
        "出生日期：2014年6月30日",
        "发布日期：2026年2月30日",
        "发布日期：2099年1月1日",
        "网页抓取时间：2014年6月30日",
    ],
)
def test_unrelated_invalid_future_dates_do_not_lower_priority(text):
    route(notice(text, "长期有效"), "undated_high_priority", "detailed")


def test_multiple_page_dates_use_newest_approximate_age():
    route(
        notice("发布日期：2014年6月30日", "发布日期：2015年1月1日", "报名请提交材料"),
        "undated_low_priority",
        "quick",
    )


@pytest.mark.parametrize(
    "text",
    [
        "报名截止2026年2月30日",
        "报名时间2026年10月1日至2月30日",
    ],
)
def test_invalid_application_date_defaults_to_unknown_detailed(text):
    route(notice(text), "unknown", "detailed")


@pytest.mark.parametrize(
    "value", [None, {}, {"page": None}, {"attachments": None}, notice(""), notice("未识别材料")]
)
def test_unknown_defaults_to_detailed(value):
    route(value, "unknown", "detailed")


def test_classification_is_only_an_intent_hint_not_date_or_cancellation_authority():
    value = notice("未识别材料")
    route(
        value, "undated_high_priority", "detailed", classification={"business_type": "recruitment"}
    )
    route(value, "unknown", "detailed", classification={"business_type": "cancellation"})
    route(value, "unknown", "detailed", classification={"deadline": "2014-06-30", "expired": True})
    route(notice("取消本次招聘，原报名截止2014年6月30日"), "unknown", "detailed")


@pytest.mark.parametrize("region", ["navigation", "recommendation"])
def test_auxiliary_dates_and_publication_labels_are_ignored(region):
    value = notice("报名请提交材料", "报名截止2014年6月30日", "发布日期：2014年6月30日")
    for block in value["page"]["blocks"][1:]:
        block["region_kind"] = region
    route(value, "undated_high_priority", "detailed")


def test_html_link_labels_and_footer_are_not_notice_prose():
    value = notice("报名请提交材料", "报名截止2014年6月30日", "发布日期：2014年6月30日")
    value["page"]["blocks"][1]["locator"] = "div > a:1"
    value["page"]["links"] = [{"label": "报名截止2014年6月30日"}]
    value["page"]["blocks"][2]["locator"] = "footer > p:1"
    route(value, "undated_high_priority", "detailed")


def test_heading_does_not_leak_across_documents_or_unrelated_blocks():
    value = notice("报名时限", "请阅读下文", "至2014年6月30日止")
    route(value, "undated_low_priority", "quick")
    value = notice("报名时限")
    value["attachments"] = [notice("至2014年6月30日止")["page"]]
    route(value, "undated_high_priority", "detailed")


def test_old_start_and_interview_dates_only_lower_cost_never_fixed_expiry():
    route(notice("报名自2014年6月30日开始"), "undated_low_priority", "quick")
    route(notice("报名期限自2014年6月30日起"), "undated_low_priority", "quick")
    route(notice("报名须知：面试截止2014年6月30日"), "undated_low_priority", "quick")


def test_endpoint_day_is_evaluated_in_china_timezone():
    value = notice("报名截止2026年10月24日")
    before = datetime(2026, 10, 24, 15, 59, 59, tzinfo=UTC)
    assert decide_route(value, now=before).category == "fixed_active"
    assert decide_route(value, now=before + timedelta(seconds=1)).category == "fixed_expired"


def test_decision_is_immutable_and_has_no_date_or_position_export():
    result = decide_route(notice("报名截止2014年6月30日"), now=NOW)
    expected = {"category", "mode", "reason", "policy_version", "evaluated_at"}
    assert {f.name for f in fields(result)} == set(result.as_dict()) == expected
    assert len(RoutingCategory) == 5
    assert {c.value for c in RoutingCategory} == {
        "fixed_active",
        "fixed_expired",
        "undated_low_priority",
        "undated_high_priority",
        "unknown",
    }
    payload = result.as_dict()
    assert "2014" not in json.dumps(payload)
    payload["category"] = "unknown"
    assert result.category == "fixed_expired"
    with pytest.raises(FrozenInstanceError):
        result.mode = "detailed"
    with pytest.raises(FrozenInstanceError):
        RoutingPolicy().old_age_days = 1


def test_clock_required_and_aware():
    with pytest.raises(TypeError):
        decide_route({})
    with pytest.raises(ValueError, match="aware"):
        decide_route({}, now=datetime(2026, 10, 24))
    with pytest.raises(ValueError, match="aware"):
        RoutingDecision(RoutingCategory.UNKNOWN, "detailed", "unknown", "v1", datetime(2026, 1, 1))


@pytest.mark.parametrize("age", [0, -1, True, 1.5, "365"])
def test_policy_rejects_invalid_age(age):
    with pytest.raises(ValueError):
        RoutingPolicy(old_age_days=age)


def test_pure_inputs_unchanged_and_no_io_during_routing(monkeypatch):
    value = notice("报名时限", "至2014年6月30日止")
    classification = {"business_type": "recruitment"}
    original, original_classification = deepcopy(value), deepcopy(classification)

    def forbidden(*args, **kwargs):
        pytest.fail("Routing must not do IO")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    route(value, "fixed_expired", "quick", classification=classification)
    assert value == original and classification == original_classification


def test_modules_have_only_stdlib_and_local_contract_imports():
    root = Path(__file__).parents[1] / "src/youju/notices"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(item.name in {"re"} for item in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module in {
                    "collections.abc",
                    "calendar",
                    "email.utils",
                    "datetime",
                    "dataclasses",
                    "enum",
                    "typing",
                    "contracts",
                    "routing",
                }


@pytest.mark.parametrize(
    ("publication", "application", "category", "mode"),
    [
        ("2014年1月1日", "报名截止2026年11月1日", "fixed_active", "detailed"),
        ("2026年10月24日", "报名截止2014年6月30日", "fixed_expired", "quick"),
    ],
)
def test_fixed_endpoint_hints_take_precedence_over_publication_age(
    publication,
    application,
    category,
    mode,
):
    route(notice("发布日期：" + publication, application), category, mode)


def test_capture_alone_does_not_turn_unknown_into_low_priority():
    value = notice("未识别材料")
    value["captured_at"] = "1900-01-01T00:00:00Z"
    route(value, "unknown", "detailed")


def test_trusted_recent_date_alone_keeps_undated_notice_high_priority():
    route(notice("发布日期：2026年10月23日"), "undated_high_priority", "detailed")


@pytest.mark.parametrize(
    "texts",
    [
        ("1. 提交报名材料，截止期为 202 5 年 4 月 21 日；",),
        ("四、报名截止时间", "2026 年 3 月 15 日。"),
        ("四、报名截止时间", "2022 年 12 月 10 日。"),
        ("滚动招聘，招满即止，本轮报名截止时间：2026 年 4 月 25 日。",),
        ("报名时间：2025年1月—2025年12月，招满为止。",),
    ],
)
def test_saved_ustc_layout_regressions_have_fixed_endpoints(texts):
    route(notice(*texts), "fixed_expired", "quick")


def test_unlabeled_signature_and_newer_update_are_approximate_age_only():
    value = notice("长期有效", "安徽中科人力资源有限公司", "2025年3月21日")
    result = route(value, "undated_low_priority", "quick")
    assert result.reason == "old_approximate_page_date"
    value["page"]["date_hints"] = [{"kind": "html_meta", "value": "2026-10-23T12:00:00+08:00"}]
    route(value, "undated_high_priority", "detailed")


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("html_meta", "2025-03-21"),
        ("html_time", "2025-03-21T12:00:00+08:00"),
        ("http_last_modified", "Fri, 21 Mar 2025 08:00:00 GMT"),
    ],
)
def test_observed_non_body_dates_can_supply_page_age(kind, value):
    bundle = notice("长期有效")
    bundle["page"]["date_hints"] = [{"kind": kind, "value": value}]
    route(bundle, "undated_low_priority", "quick")


def test_future_invalid_capture_and_navigation_dates_are_not_age_hints():
    bundle = notice("长期有效", "版权所有2020年1月1日", "出生日期1990年1月1日")
    bundle["page"]["date_hints"] = [
        {"kind": "http_last_modified", "value": "invalid"},
        {"kind": "html_meta", "value": "2099-01-01"},
        {"kind": "captured_at", "value": "2020-01-01"},
    ]
    route(bundle, "undated_high_priority", "detailed")


def test_deadline_heading_does_not_turn_following_start_only_date_into_endpoint():
    route(notice("报名期限", "2014年6月30日起"), "undated_low_priority", "quick")
