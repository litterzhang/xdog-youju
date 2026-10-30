"""Pure host-owned semantic assertion contract; no source storage or Agent authority."""

from copy import deepcopy

from youju.serialization import digest, json_bytes

from .facts import POSITION_FIELDS, PositionFacts, TaggedField

MAX_EXPECTATION_BYTES = 1024 * 1024
MAX_EXPECTATIONS = 1000


def validate_expected(value):
    """Omitted fields are not assertions of absence; complete assertions are explicit."""
    if (
        not isinstance(value, dict)
        or set(value) - {"basis", "reference", "positions", "complete_position_set"}
        or not {"basis", "reference", "positions"} <= value.keys()
    ):
        raise ValueError("Invalid reviewed semantic expectation")
    if value["basis"] not in {"human-confirmed", "independent-rule"}:
        raise ValueError("A model suggestion cannot be registered as semantic gold")
    if not isinstance(value["reference"], str) or not 0 < len(value["reference"]) <= 2048:
        raise ValueError("A reviewed case requires an independent reference")
    if type(value.get("complete_position_set", False)) is not bool:
        raise ValueError("Completeness must be explicit boolean")
    if not isinstance(value["positions"], list) or len(value["positions"]) > MAX_EXPECTATIONS:
        raise ValueError("Too many semantic position expectations")
    if not value["positions"] and value.get("complete_position_set") is not True:
        raise ValueError(
            "A reviewed semantic case needs assertions, not an empty partial expectation"
        )
    seen = set()
    for item in value["positions"]:
        if not isinstance(item, dict) or set(item) - {
            "selector",
            "fields",
            "conditions",
            "condition_logic",
        }:
            raise ValueError("Invalid semantic position properties")
        selector = item.get("selector")
        if (
            not isinstance(selector, dict)
            or "title" not in selector
            or not ({"hiring_unit", "employer"} & selector.keys())
            or set(selector) - {"title", "hiring_unit", "employer", "work_location"}
            or any(not isinstance(v, str) or not 0 < len(v) <= 2048 for v in selector.values())
        ):
            raise ValueError("A semantic identity needs a title AND unit/employer, not title alone")
        identity = digest(selector)
        if identity in seen:
            raise ValueError("Ambiguous duplicate semantic selector")
        seen.add(identity)
        fields = item.get("fields", {})
        if not isinstance(fields, dict) or set(fields) - set(POSITION_FIELDS):
            raise ValueError("Unsupported semantic field")
        for field, fact in fields.items():
            TaggedField.from_dict(fact)
            if field in selector and fact["value"] != selector[field]:
                raise ValueError("Semantic selector conflicts with its field assertion")
        # Validate field types and condition grammar with the existing sole contract.
        facts = {name: {"value": None, "tag": "unknown"} for name in POSITION_FIELDS}
        facts.update(fields)
        if "title" not in fields:
            facts["title"] = {"value": selector["title"], "tag": "source"}
        has_conditions = "conditions" in item or "condition_logic" in item
        if has_conditions and not {"conditions", "condition_logic"} <= item.keys():
            raise ValueError("Conditions require explicit logic as well")
        PositionFacts.from_dict(
            {
                "schema_version": "position-facts-v3",
                **facts,
                "conditions": item.get("conditions", []),
                "condition_logic": item.get("condition_logic"),
            }
        )
    if len(json_bytes(value)) > MAX_EXPECTATION_BYTES:
        raise ValueError("Semantic expectation exceeds byte limit")
    return deepcopy(value)


def full_semantics(expected):
    """Only exhaustive independently reviewed facts can supersede every old count/delta."""
    return expected.get("complete_position_set") is True and all(
        set(item.get("fields", {})) == set(POSITION_FIELDS)
        and {"conditions", "condition_logic"} <= item.keys()
        for item in expected["positions"]
    )
