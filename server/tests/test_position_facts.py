"""Three model-free contract groups: tags, atomic logic and complete output shape."""

import copy

import pytest

from youju.positions import Condition, FieldTag, PositionFacts, TaggedField
from youju.positions.facts import POSITION_FIELDS


def example():
    fields = {name: TaggedField() for name in POSITION_FIELDS}
    fields.update(
        title=TaggedField("科研助理", "source"),
        hiring_unit=TaggedField("合成大学", "source"),
        headcount=TaggedField(2, "source"),
        employment_type=TaggedField("personnel_agency", "ai_inferred"),
        application_rule=TaggedField("open_ended", "source"),
    )
    return PositionFacts(
        fields,
        (
            Condition("c1", "degree", "equals", TaggedField({"level": "doctorate"}, "source")),
            Condition(
                "c2",
                "work_experience",
                "equals",
                TaggedField({"minimum_years": None, "kind": "博士后出站"}, "source"),
            ),
        ),
        {"operator": "any", "items": [{"condition_id": "c1"}, {"condition_id": "c2"}]},
    )


def test_each_field_has_a_tag_without_quote_or_evidence_requirements():
    data = example().as_dict()
    assert data["employment_type"] == {"value": "personnel_agency", "tag": "ai_inferred"}
    assert data["deadline_on"] == {"value": None, "tag": "unknown"}
    assert all(set(data[key]) == {"value", "tag"} for key in POSITION_FIELDS)
    assert "evidence" not in str(data) and "raw_text" not in str(data)
    assert PositionFacts.from_dict(data).as_dict() == data
    assert TaggedField("不限", FieldTag.SOURCE).tag == FieldTag.SOURCE
    for value, tag in [
        (None, "source"),
        ("博士", "unknown"),
        (None, "ai_inferred"),
        ("x", "made_up"),
    ]:
        with pytest.raises(ValueError):
            TaggedField(value, tag)
    with pytest.raises(ValueError):
        TaggedField(float("nan"), "ai_inferred")


def test_conditions_are_independent_and_alternatives_cannot_be_lost():
    data = example().as_dict()
    assert isinstance(data["conditions"], list) and len(data["conditions"]) == 2
    assert data["condition_logic"]["operator"] == "any"
    assert {item["type"] for item in data["conditions"]} == {"degree", "work_experience"}
    assert not any(item["type"] == "education" for item in data["conditions"])
    reused = copy.deepcopy(data)
    reused["condition_logic"] = {
        "operator": "all",
        "items": [data["condition_logic"], {"condition_id": "c1"}],
    }
    assert PositionFacts.from_dict(reused).as_dict() == reused
    unknown = copy.deepcopy(data)
    unknown["condition_logic"] = None
    assert PositionFacts.from_dict(unknown).condition_logic is None
    for logic in [
        {"condition_id": "missing"},
        {"operator": "all", "items": [{"condition_id": "c1"}]},
        {"operator": "any", "items": [{"condition_id": "c1"}] * 2},
    ]:
        changed = copy.deepcopy(data)
        changed["condition_logic"] = logic
        with pytest.raises(ValueError):
            PositionFacts.from_dict(changed)
    changed = copy.deepcopy(data)
    changed["conditions"] = "博士学位"
    with pytest.raises(ValueError):
        PositionFacts.from_dict(changed)


def test_complete_shape_unknowns_and_scalar_semantics_are_enforced():
    data = example().as_dict()
    for key in POSITION_FIELDS:
        changed = copy.deepcopy(data)
        changed.pop(key)
        with pytest.raises(ValueError):
            PositionFacts.from_dict(changed)
    for field, value in [
        ("headcount", True),
        ("headcount", -1),
        ("deadline_on", "2026-02-30"),
        ("employment_type", "guaranteed_public_staff"),
        ("employment_type", "fixed_term"),
        ("employment_type", "temporary"),
        ("employment_type", "direct_employment"),
        ("title", " "),
    ]:
        changed = copy.deepcopy(data)
        changed[field] = {"value": value, "tag": "source"}
        with pytest.raises(ValueError):
            PositionFacts.from_dict(changed)
    for category in (
        "personnel_agency",
        "labor_dispatch",
        "labor_contract",
        "other",
    ):
        changed = copy.deepcopy(data)
        changed["employment_type"] = {"value": category, "tag": "source"}
        assert PositionFacts.from_dict(changed).as_dict() == changed
    assert "staffing_type" not in data
    fields = {name: TaggedField() for name in POSITION_FIELDS}
    # 未说明/若干人数 can be captured as text without making up a numeric count.
    fields["headcount_description"] = TaggedField("若干名", "source")
    facts = PositionFacts(fields)
    assert facts.as_dict()["headcount"]["tag"] == "unknown"
    # An institution's identity alone does not establish a position's staffing entitlement.
    fields["hiring_unit"] = TaggedField("合成事业单位", "source")
    assert PositionFacts(fields).as_dict()["employment_type"] == {"value": None, "tag": "unknown"}
    assert facts.as_dict()["condition_logic"] is None
    fields["headcount"] = TaggedField(0, "source")
    assert PositionFacts(fields).as_dict()["headcount"]["value"] == 0
