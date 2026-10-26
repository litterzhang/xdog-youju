"""JSON-native v3 business schemas; orchestration metadata belongs to callers."""

from .conditions import value_schema
from .employment import STAFFING_STATUSES
from .facts import (
    APPLICATION_RULES,
    CONDITION_OPERATORS,
    CONDITION_TYPES,
    EMPLOYMENT_TYPES,
    POSITION_FIELDS,
)

CONTRACT = "position-facts-v3"


def _tagged(value):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["value", "tag"],
        "properties": {
            "value": {"anyOf": [value, {"type": "null"}]},
            "tag": {"type": "string", "enum": ["source", "ai_inferred", "unknown"]},
        },
    }


_TEXT = {"type": "string", "minLength": 1, "maxLength": 32000}
_ISO = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
DATE_FACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "year", "month", "day", "year_status", "tag", "time"],
    "properties": {
        "date": {"anyOf": [_ISO, {"type": "null"}]},
        "year": {"type": ["integer", "null"], "minimum": 1, "maximum": 9999},
        "month": {"type": "integer", "minimum": 1, "maximum": 12},
        "day": {"type": "integer", "minimum": 1, "maximum": 31},
        "year_status": {"enum": ["explicit", "inferred", "unknown"]},
        "tag": {"enum": ["source", "ai_inferred", "unknown"]},
        "time": {"type": ["string", "null"], "pattern": r"^\d{2}:\d{2}$"},
    },
}
DATES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["start", "end"],
    "properties": {
        key: {"anyOf": [DATE_FACT_SCHEMA, {"type": "null"}]} for key in ("start", "end")
    },
}
CONDITION_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "operator", "scope", "value", "tag"],
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 128},
                "type": {"type": "string", "const": kind},
                "operator": {"type": "string", "enum": sorted(CONDITION_OPERATORS)},
                "scope": {"type": "string", "enum": ["notice", "position"]},
                "value": {"anyOf": [value_schema(kind), {"type": "null"}]},
                "tag": {"type": "string", "enum": ["source", "ai_inferred", "unknown"]},
            },
        }
        for kind in sorted(CONDITION_TYPES)
    ]
}
_FIELDS = {name: _tagged(_TEXT) for name in POSITION_FIELDS}
_FIELDS.update(
    title=_tagged({**_TEXT, "maxLength": 256}),
    position_code=_tagged({**_TEXT, "maxLength": 128}),
    headcount=_tagged({"type": "integer", "minimum": 0}),
    deadline_on=_tagged(_ISO),
    application_start_on=_tagged(_ISO),
    application_dates=_tagged(DATES_SCHEMA),
    application_rule=_tagged({"type": "string", "enum": sorted(APPLICATION_RULES)}),
    employment_type=_tagged({"type": "string", "enum": sorted(EMPLOYMENT_TYPES)}),
    staffing_status=_tagged({"type": "string", "enum": sorted(STAFFING_STATUSES)}),
)
POSITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", *POSITION_FIELDS, "conditions", "condition_logic"],
    "properties": {
        "schema_version": {"type": "string", "const": CONTRACT},
        **_FIELDS,
        "conditions": {"type": "array", "maxItems": 200, "items": CONDITION_SCHEMA},
        "condition_logic": {"type": ["object", "null"]},
    },
}
COVERAGE_SCHEMA = {
    "type": "object",
    "required": ["supported", "complete", "needs_detailed"],
    "properties": {
        **{name: {"type": "boolean"} for name in ("supported", "complete", "needs_detailed")},
        "empty_capture": {
            "type": "object",
            "additionalProperties": False,
            "required": ["table_index"],
            "properties": {"table_index": {"type": "integer", "minimum": 0}},
        },
    },
}
ISSUE_SCHEMA = {
    "type": "object",
    "required": ["code", "message"],
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
}
V3_EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "status", "positions", "errors", "coverage"],
    "properties": {
        "schema_version": {"type": "string", "const": CONTRACT},
        "status": {
            "type": "string",
            "enum": ["success", "partial", "empty", "review", "cancelled", "failed"],
        },
        "positions": {"type": "array", "maxItems": 1000, "items": POSITION_SCHEMA},
        "errors": {"type": "array", "items": ISSUE_SCHEMA},
        "coverage": COVERAGE_SCHEMA,
    },
}
V3_RESULT_SCHEMA = V3_EXTRACTION_SCHEMA


def recipe_schemas(config):
    if config.get("position_contract", CONTRACT) != CONTRACT:
        raise ValueError("Only position-facts-v3 is supported; regenerate the source recipe")
    return V3_EXTRACTION_SCHEMA, V3_RESULT_SCHEMA


V3_INSTRUCTIONS = """Return position-facts-v3 tagged business facts and typed conditions.
Separate staffing_status from employment_type. An institution's identity never
implies established staffing. Preserve unknowns, exceptions and logical alternatives.
No per-field quotations are mandatory. Application dates must retain year certainty;
never turn an inferred year into a source deadline. Do not decide notice validity,
model routing or eligibility. Incomplete captures must not erase available facts.
"""
