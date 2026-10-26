"""Source-neutral v3 business fields: values, provenance tags and typed conditions.

This is the sole position output contract, shared by Recipe, ORM and HTTP reads.
A source tag describes the parser's declared origin, not human verification.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from .conditions import validate_condition_value
from .deadlines import validate_dates
from .employment import EMPLOYMENT_TYPES, STAFFING_STATUSES


class FieldTag(StrEnum):
    SOURCE = "source"
    AI_INFERRED = "ai_inferred"
    UNKNOWN = "unknown"


def _json_value(value):
    # Reject non-JSON runtime objects and nonfinite model values without dispatching a model.
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("A field must contain a JSON value") from error


@dataclass(frozen=True, slots=True)
class TaggedField:
    value: Any = None
    tag: FieldTag = FieldTag.UNKNOWN

    def __post_init__(self):
        object.__setattr__(self, "tag", FieldTag(self.tag))
        if (self.tag == FieldTag.UNKNOWN) != (self.value is None):
            raise ValueError("Unknown fields require null; other tags require a value")
        _json_value(self.value)

    def as_dict(self):
        return {"value": self.value, "tag": self.tag.value}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"value", "tag"}:
            raise ValueError("Each business field requires exactly value and tag")
        return cls(value["value"], value["tag"])


CONDITION_TYPES = frozenset(
    {
        "education",
        "degree",
        "study_form",
        "major",
        "age",
        "work_experience",
        "graduation_status",
        "graduation_year",
        "political_status",
        "license",
        "language",
        "other",
    }
)
CONDITION_OPERATORS = frozenset({"equals", "at_least", "at_most", "in", "contains", "unspecified"})


@dataclass(frozen=True, slots=True)
class Condition:
    """One atomic assertion; the tag applies to its requirement value, not IDs."""

    id: str
    type: str
    operator: str
    fact: TaggedField
    scope: str = "position"

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 128:
            raise ValueError("A version-local condition ID is required")
        if self.type not in CONDITION_TYPES or self.operator not in CONDITION_OPERATORS:
            raise ValueError("Unsupported condition type or operator")
        if not isinstance(self.fact, TaggedField) or self.scope not in {"notice", "position"}:
            raise ValueError("A condition requires a tagged field and declared scope")
        validate_condition_value(self.type, self.fact.value)

    def as_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "operator": self.operator,
            "scope": self.scope,
            **self.fact.as_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        keys = {"id", "type", "operator", "scope", "value", "tag"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("Invalid atomic condition")
        return cls(
            value["id"],
            value["type"],
            value["operator"],
            TaggedField(value["value"], value["tag"]),
            value["scope"],
        )


POSITION_FIELDS = (
    "hiring_unit",
    "employer",
    "title",
    "position_code",
    "headcount",
    "headcount_description",
    "deadline_on",
    "application_start_on",
    "application_dates",
    "application_rule",
    "employment_type",
    "staffing_status",
    "work_location",
    "requirements_text",
)
APPLICATION_RULES = frozenset({"fixed_deadline", "open_ended", "until_filled", "not_stated"})


def _check_logic(logic, condition_ids):
    """Reference-only ALL/ANY data, never an expression evaluator."""
    visited = []
    count = 0

    def walk(node, depth):
        nonlocal count
        count += 1
        if count > 500 or depth > 12 or not isinstance(node, dict):
            raise ValueError("Invalid or excessive condition logic")
        if set(node) == {"condition_id"}:
            identity = node["condition_id"]
            if not isinstance(identity, str) or identity not in condition_ids:
                raise ValueError("Logic references an unknown condition")
            visited.append(identity)
            return
        if set(node) != {"operator", "items"} or node["operator"] not in {"all", "any"}:
            raise ValueError("Invalid condition group")
        if not isinstance(node["items"], list) or not node["items"]:
            raise ValueError("Condition groups require children")
        for child in node["items"]:
            walk(child, depth + 1)

    walk(logic, 0)
    if set(visited) != condition_ids:
        raise ValueError("Logic must cover every declared condition")


@dataclass(frozen=True, slots=True)
class PositionFacts:
    """The v3 position body; every listed business field exists even if unknown."""

    fields: Mapping[str, TaggedField]
    conditions: tuple[Condition, ...] = ()
    condition_logic: dict | None = None
    schema_version: str = field(default="position-facts-v3", init=False)

    def __post_init__(self):
        if set(self.fields) != set(POSITION_FIELDS) or any(
            not isinstance(value, TaggedField) for value in self.fields.values()
        ):
            raise ValueError("Every declared position field must exist and have a tag")
        if (
            not isinstance(self.conditions, tuple)
            or len(self.conditions) > 200
            or any(not isinstance(item, Condition) for item in self.conditions)
        ):
            raise ValueError("Conditions must be bounded independent objects")
        identities = [item.id for item in self.conditions]
        if len(identities) != len(set(identities)):
            raise ValueError("Condition IDs must be unique within a position version")
        if self.conditions:
            if self.condition_logic is not None:
                _check_logic(self.condition_logic, set(identities))
        elif self.condition_logic is not None:
            raise ValueError("An empty condition array requires null logic")
        for name, tagged in self.fields.items():
            if tagged.tag == FieldTag.UNKNOWN:
                continue
            value = tagged.value
            if name == "application_dates":
                validate_dates(value)
                continue
            if name == "headcount":
                if type(value) is not int or value < 0:
                    raise ValueError(
                        "Headcount is a nonnegative announced count, not remaining vacancies"
                    )
            elif not isinstance(value, str) or not value.strip() or len(value) > 32000:
                raise ValueError("Known textual fields require nonblank strings")
            if name == "title" and len(value) > 256:
                raise ValueError("Position titles must fit the 256-character display limit")
            if name == "position_code" and len(value) > 128:
                raise ValueError("Official position codes must fit 128 characters")
            if name in {"deadline_on", "application_start_on"}:
                try:
                    parsed = date.fromisoformat(value)
                except (TypeError, ValueError) as error:
                    raise ValueError("Deadline requires an ISO date") from error
                if parsed.isoformat() != value:
                    raise ValueError("Deadline requires canonical YYYY-MM-DD")
            if name == "application_rule" and value not in APPLICATION_RULES:
                raise ValueError("Unsupported declared application rule")
            if name == "staffing_status" and value not in STAFFING_STATUSES:
                raise ValueError("Unsupported staffing status")
            if name == "employment_type" and value not in EMPLOYMENT_TYPES:
                raise ValueError(
                    "Employment type must describe hiring identity, not contract duration"
                )

        dates = self.fields["application_dates"].value
        if not dates and any(
            self.fields[name].value is not None for name in ("deadline_on", "application_start_on")
        ):
            raise ValueError("Date projections require structured application_dates")
        if dates:
            for endpoint, field_name in (("start", "application_start_on"), ("end", "deadline_on")):
                part, projected = dates[endpoint], self.fields[field_name]
                if projected.value is not None and (
                    not part or projected.value != part["date"] or projected.tag != part["tag"]
                ):
                    raise ValueError("Date projection conflicts with structured dates")
        start, end = self.fields["application_start_on"], self.fields["deadline_on"]
        if start.value and end.value and start.value > end.value:
            raise ValueError("Application start follows deadline")

    def as_dict(self):
        return {
            "schema_version": self.schema_version,
            **{key: self.fields[key].as_dict() for key in POSITION_FIELDS},
            "conditions": [item.as_dict() for item in self.conditions],
            "condition_logic": self.condition_logic,
        }

    @classmethod
    def from_dict(cls, value):
        keys = {*POSITION_FIELDS, "schema_version", "conditions", "condition_logic"}
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or (value["schema_version"] != "position-facts-v3")
        ):
            raise ValueError("Unsupported position output contract")
        if not isinstance(value["conditions"], list):
            raise ValueError("Conditions must be an array")
        return cls(
            {name: TaggedField.from_dict(value[name]) for name in POSITION_FIELDS},
            tuple(Condition.from_dict(item) for item in value["conditions"]),
            value["condition_logic"],
        )
