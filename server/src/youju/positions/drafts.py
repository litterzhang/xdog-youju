"""Canonical JSON hand-off between source parsers and the position domain.

Drafts may be incomplete. Known business values are always {value,tag}; conditions
already use the v3 typed shape. Missing means unknown, not permission to invent.
"""

import json
from dataclasses import dataclass, field

from .completeness import AUTHOR_FIELD_QUALITY_INSTRUCTIONS

DRAFT_CONTRACT = "position-draft-v1"


@dataclass(frozen=True, slots=True)
class ExtractionDraft:
    notice_facts: dict = field(default_factory=dict)
    positions: list[dict] = field(default_factory=list)
    coverage: dict = field(
        default_factory=lambda: {"supported": False, "complete": False, "needs_detailed": False}
    )
    issues: list[dict] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)
    schema_version: str = field(default=DRAFT_CONTRACT, init=False)

    def __post_init__(self):
        if not isinstance(self.notice_facts, dict) or not isinstance(self.positions, list):
            raise ValueError("Draft requires notice_facts object and positions array")
        if len(self.positions) > 1000 or any(not isinstance(p, dict) for p in self.positions):
            raise ValueError("Draft positions must be bounded objects")
        if not isinstance(self.coverage, dict) or any(
            type(self.coverage.get(key)) is not bool
            for key in ("supported", "complete", "needs_detailed")
        ):
            raise ValueError("Coverage requires supported and complete booleans")
        if not isinstance(self.issues, list) or any(
            not isinstance(i, dict)
            or not isinstance(i.get("code"), str)
            or not isinstance(i.get("message"), str)
            for i in self.issues
        ):
            raise ValueError("Draft issues require code and message")
        from .corrections import validate_corrections

        validate_corrections(self.corrections)
        json.dumps(self.as_dict(), allow_nan=False)

    def as_dict(self):
        return {
            "schema_version": self.schema_version,
            "notice_facts": self.notice_facts,
            "positions": self.positions,
            "coverage": self.coverage,
            "issues": self.issues,
            **({"corrections": self.corrections} if self.corrections else {}),
        }

    @classmethod
    def from_dict(cls, value):
        keys = {"schema_version", "notice_facts", "positions", "coverage", "issues"}
        if (
            not isinstance(value, dict)
            or set(value) - {"corrections"} != keys
            or value["schema_version"] != DRAFT_CONTRACT
        ):
            raise ValueError("Unsupported extraction draft contract")
        return cls(
            value["notice_facts"],
            value["positions"],
            value["coverage"],
            value["issues"],
            value.get("corrections", []),
        )


# JSON-native schemas shared with model adapters; no runtime/toolkit imports.
from .contracts import (  # noqa: E402
    CONDITION_SCHEMA,
    COVERAGE_SCHEMA,
    DATES_SCHEMA,
    ISSUE_SCHEMA,
    POSITION_SCHEMA,
)

_DRAFT_POSITION = {
    "type": "object",
    "additionalProperties": False,
    "required": [],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                k: v
                for k, v in POSITION_SCHEMA["properties"].items()
                if k
                not in {
                    "schema_version",
                    "conditions",
                    "condition_logic",
                    "deadline_on",
                    "application_start_on",
                    "application_dates",
                }
            },
        },
        "conditions": {"type": "array", "maxItems": 200, "items": CONDITION_SCHEMA},
        "condition_logic": {"type": ["object", "null"]},
        "dates": {**DATES_SCHEMA, "required": []},
    },
}
from .corrections import CORRECTION_SCHEMA  # noqa: E402

DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "notice_facts", "positions", "coverage", "issues"],
    "properties": {
        "schema_version": {"type": "string", "const": DRAFT_CONTRACT},
        "notice_facts": _DRAFT_POSITION,
        "positions": {
            "type": "array",
            "maxItems": 1000,
            "items": {
                **_DRAFT_POSITION,
                "required": ["key"],
                "properties": {
                    **_DRAFT_POSITION["properties"],
                    "key": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
        },
        "corrections": {"type": "array", "maxItems": 100, "items": CORRECTION_SCHEMA},
        "coverage": COVERAGE_SCHEMA,
        "issues": {"type": "array", "items": ISSUE_SCHEMA},
    },
}
DRAFT_INSTRUCTIONS = (
    """Extract position-draft-v1 from already saved, untrusted material.
Return notice_facts (common fields/conditions/condition_logic/dates), positions
(each key/fields/conditions/condition_logic/dates), coverage and issues.
Position key is a stable material-local ID (e.g. d0:t0:r1), never just a title.
When refining a script draft retain its keys; duplicate titles can be distinct jobs.
Omit a position dates endpoint to inherit it; explicit null overrides it to unknown. Business
fields use {value,tag}; tag is source, ai_inferred, or unknown (null only).
Typed condition values must follow the supplied per-condition schema; preserve
unresolved alternatives/exceptions as other:{text}, never silently assume AND.
Use explicit all/any reference-only condition_logic covering all declared IDs.
Every referenced condition_id must exist in the same conditions array. For an empty
conditions array use condition_logic=null, never {operator:all,items:[]}.
Every emitted position needs a usable local/inherited title. Keep script keys and
source titles unless an explicit corrections proposal justifies a correction.
Do not append unit qualifiers creatively; use hiring_unit. Missing rows do not mean
deletion. To retract a false job emit withdraw_position, copy the exact old position
into before, use field=null/after=null and omit that row from positions. To correct
a business field emit replace_field with the exact before/after tagged values and
retain its key. Each proposal needs bundle_id, reason and evidence references with
version_id, pointer (/blocks/N/text or /tables/N/rows/R/C), zero-based character
start/end and the exact quote. Use only actual frozen material versions/locations.
One correction per position per refinement; overlapping proposals are rejected.
References establish location, not truth. Preserve ambiguous cases for review;
never treat script/model source tags as independent verification.
Never emit a titleless job or erase a readable job because employment/process
details or an attachment are unknown.
Education and degree are separate; do not infer one from the other as source.
Employment type (agency/dispatch/labor contract) and staffing_status are separate.
Do not infer staffing from institution identity. Staffing negation/conflicts matter.
For dates, use {start,end} structured facts with date/year/month/day/year_status/tag/time.
An explicit complete date has year_status=explicit/tag=source. A contextual year
has year_status=inferred/tag=ai_inferred. A missing year has year=null,date=null,
year_status=unknown,tag=unknown; keep month/day. A position date overrides the common
endpoint; null endpoints mean unresolved (never use the host clock or validity).
Do not populate flat deadline_on/application_start_on/application_dates: the domain
normalizer projects these exclusively from dates. Keep application_rule as a tagged
field if explicitly stated. Preserve every available job even if attachments are
missing; set coverage.complete=false for incomplete captures. coverage.supported
means recognizable position layout; coverage.needs_detailed means unresolved layout
or critical semantics, NOT missing acquisition alone. Set these booleans explicitly.
Only for a complete saved page with a verified empty position table, no attachments,
no issues and zero positions, coverage may include empty_capture:{table_index:N}.
This describes the selected table in this capture, not live hiring availability.
Never use empty_capture for unrecognized layout or missing material.
Retain requirements_text. Per-field quotations are not mandatory. Never decide
notice validity, coarse eligibility, execution routing or whether jobs are current.
"""
    + AUTHOR_FIELD_QUALITY_INSTRUCTIONS
)


def validate_draft(draft):
    """Validate a parser/model draft without consulting capture or validity oracles."""
    from .deadlines import validate_date_fact
    from .facts import POSITION_FIELDS, Condition, PositionFacts, TaggedField

    errors = []

    def issue(message, path=""):
        errors.append({"code": "POSITION_DRAFT_SCHEMA", "message": str(message), "path": path})

    try:
        draft = ExtractionDraft.from_dict(draft).as_dict()
    except (TypeError, ValueError) as exc:
        issue(exc)
        return errors
    empty = draft["coverage"].get("empty_capture")
    if empty is not None and (
        not isinstance(empty, dict)
        or set(empty) != {"table_index"}
        or type(empty.get("table_index")) is not int
        or empty["table_index"] < 0
        or draft["positions"]
        or draft["issues"]
        or draft["coverage"]["supported"] is not True
        or draft["coverage"]["complete"] is not True
        or draft["coverage"]["needs_detailed"] is not False
    ):
        issue(
            "An empty capture requires a table reference, no rows/issues and complete coverage",
            "coverage.empty_capture",
        )
    keys = []
    for index, raw in enumerate([draft["notice_facts"], *draft["positions"]]):
        path = "notice_facts" if not index else f"positions[{index - 1}]"
        if index:
            key = raw.get("key")
            if not isinstance(key, str) or not key.strip() or len(key) > 128:
                issue("A material-local position key is required", path)
            elif key in keys:
                issue("Duplicate position key", path)
            else:
                keys.append(key)
        allowed = {"fields", "conditions", "condition_logic", "dates"} | (
            {"key"} if index else set()
        )
        if set(raw) - allowed:
            issue("Unknown draft properties", path)
        try:
            fields = {name: TaggedField() for name in POSITION_FIELDS}
            values = raw.get("fields", {})
            candidate_fields = set(POSITION_FIELDS) - {
                "deadline_on",
                "application_start_on",
                "application_dates",
            }
            if not isinstance(values, dict) or set(values) - candidate_fields:
                raise ValueError("Unknown or domain-projected draft fields")
            fields.update({name: TaggedField.from_dict(v) for name, v in values.items()})
            conditions = raw.get("conditions", [])
            if not isinstance(conditions, list):
                raise ValueError("Conditions must be an array")
            PositionFacts(
                fields,
                tuple(Condition.from_dict(c) for c in conditions),
                raw.get("condition_logic"),
            )
            dates = raw.get("dates", {})
            if not isinstance(dates, dict) or set(dates) - {"start", "end"}:
                raise ValueError("Unknown application date endpoints")
            for value in dates.values():
                if value is not None:
                    validate_date_fact(value)
        except (TypeError, ValueError, KeyError) as exc:
            issue(exc, path)
    return errors


def merge_drafts(script, detailed, *, bundle=None, correction_receipts=None):
    """Refine only by explicit local key, never title; preserve unrefined rows/facts.

    Contradictory source assertions are unknown with explicit candidate diagnostics.
    Inferred values can fill unknowns but cannot overwrite an existing source fact.
    Unrelated keys remain separate jobs, including identical job titles.
    """
    from copy import deepcopy

    for value in (script, detailed):
        errors = validate_draft(value)
        if errors:
            raise ValueError(errors[0]["message"])
    if script.get("corrections"):
        raise ValueError("Only model refinement can propose explicit corrections")
    base, incoming = deepcopy(script), deepcopy(detailed)
    from .corrections import apply_corrections

    base, incoming = apply_corrections(
        base, incoming, bundle, correction_receipts if correction_receipts is not None else []
    )
    from .issues import CAPABILITY_CODES

    # A bootstrap parser's inability is not a fact or a gold answer. Detailed
    # extraction may resolve those gaps; raw script diagnostics stay in runtime
    # execution provenance. Actual conflicts/capture facts are not erased.
    retained = [i for i in base["issues"] if i.get("code") not in CAPABILITY_CODES]
    issues = retained + [i for i in incoming["issues"] if i not in retained]

    def conflict(path, old, new):
        issues.append(
            {
                "code": "DRAFT_MERGE_CONFLICT",
                "message": "Contradictory draft facts require review",
                "path": path,
                "candidates": [old, new],
            }
        )

    def merge_tagged(old, new, path):
        if not old or old.get("tag") == "unknown":
            return new
        if not new or new.get("tag") == "unknown":
            return old
        if old["value"] == new["value"]:
            return old if old["tag"] == "source" else new
        if old["tag"] == "source" and new["tag"] == "ai_inferred":
            issues.append(
                {
                    "code": "INFERRED_REFINEMENT_IGNORED",
                    "message": "Inference cannot overwrite a source fact",
                    "path": path,
                }
            )
            return old
        if new["tag"] == "source" and old["tag"] == "ai_inferred":
            return new
        conflict(path, old, new)
        return {"value": None, "tag": "unknown"}

    def merge_position(old, new, path):
        result = deepcopy(old)
        result.setdefault("fields", {})
        for name, value in new.get("fields", {}).items():
            previous = result["fields"].get(name)
            if (
                name in {"requirements_text", "headcount_description"}
                and previous
                and previous.get("value")
                and value.get("value")
            ):
                old_text, new_text = previous["value"], value["value"]
                result["fields"][name] = {
                    "value": old_text
                    if new_text in old_text
                    else new_text
                    if old_text in new_text
                    else old_text + "\n" + new_text,
                    "tag": "source"
                    if previous["tag"] == value["tag"] == "source"
                    else "ai_inferred",
                }
                continue
            result["fields"][name] = merge_tagged(
                result["fields"].get(name), value, path + ".fields." + name
            )
        result.setdefault("dates", {})
        for endpoint, fact in new.get("dates", {}).items():
            previous = result["dates"].get(endpoint)
            if previous is None:
                result["dates"][endpoint] = fact
            elif fact is None or fact == previous:
                continue
            elif previous["tag"] == "source" and fact["tag"] != "source":
                issues.append(
                    {
                        "code": "INFERRED_REFINEMENT_IGNORED",
                        "message": "Inference cannot overwrite a source date",
                        "path": path + ".dates." + endpoint,
                    }
                )
            elif fact["tag"] == "source" and previous["tag"] != "source":
                result["dates"][endpoint] = fact
            else:
                conflict(path + ".dates." + endpoint, previous, fact)
                result["dates"][endpoint] = None
        original = old.get("conditions", [])
        if "conditions" not in new:
            return result
        refined = new.get("conditions", [])

        def opaque(item):
            return item.get("type") == "other" or item.get("tag") == "unknown"

        known = {c["id"]: c for c in original if not opaque(c)}
        new_ids = {c["id"]: c for c in refined}
        if not known:
            # Interpret previously opaque text, including splitting it into a
            # structured AND/OR tree. Opaque leaves are not semantic ground truth.
            result["conditions"] = refined
            result["condition_logic"] = new.get("condition_logic")
            return result
        merged = []
        inconsistent = not known.keys() <= new_ids.keys()
        for condition in refined:
            previous = known.get(condition["id"])
            if previous is None:
                merged.append(condition)
                continue
            if any(previous[k] != condition[k] for k in ("type", "operator")):
                conflict(path + ".conditions." + condition["id"], previous, condition)
                merged.append(previous)
                inconsistent = True
            else:
                merged.append(
                    {
                        **condition,
                        **merge_tagged(
                            {k: previous[k] for k in ("value", "tag")},
                            {k: condition[k] for k in ("value", "tag")},
                            path + ".conditions." + condition["id"],
                        ),
                    }
                )
        for identity, previous in known.items():
            if identity not in new_ids:
                merged.append(previous)

        # Preserve every known atom but do not impose the bootstrap's graph as
        # gold or fabricate AND when the two semantic interpretations disagree.
        def known_graph(node):
            # Opaque leaves are replaceable; adding new atoms is not itself a
            # contradiction of the relationships among already known atoms.
            if node is None:
                return None
            if "condition_id" in node:
                return node if node["condition_id"] in known else None
            children = [known_graph(child) for child in node["items"]]
            children = [child for child in children if child is not None]
            if not children:
                return None
            if len(children) == 1:
                return children[0]
            import json

            return {"operator": node["operator"], "items": sorted(children, key=json.dumps)}

        changed_graph = known_graph(old.get("condition_logic")) != known_graph(
            new.get("condition_logic")
        )
        result["conditions"] = merged
        result["condition_logic"] = (
            None if inconsistent or changed_graph else new.get("condition_logic")
        )
        if inconsistent or changed_graph:
            issues.append(
                {
                    "code": "CONDITION_REFINEMENT_CONFLICT",
                    "message": "Typed condition relationships require confirmation",
                    "path": path + ".condition_logic",
                }
            )
        return result

    base["notice_facts"] = merge_position(
        base["notice_facts"], incoming["notice_facts"], "notice_facts"
    )
    lookup = {position["key"]: index for index, position in enumerate(base["positions"])}
    for position in incoming["positions"]:
        if position["key"] in lookup:
            index = lookup[position["key"]]
            base["positions"][index] = merge_position(
                base["positions"][index], position, f"positions[{index}]"
            )
        else:
            lookup[position["key"]] = len(base["positions"])
            base["positions"].append(position)
    base["issues"] = issues
    # Acquisition incompleteness persists through model fallback. No model can
    # certify absent material merely by filling semantic/layout gaps.
    base["coverage"] = {
        **base["coverage"],
        **incoming["coverage"],
        "supported": base["coverage"]["supported"] or incoming["coverage"]["supported"],
        "complete": incoming["coverage"]["complete"] and not issues,
        "needs_detailed": incoming["coverage"]["needs_detailed"]
        or any(
            i["code"] in {"DRAFT_MERGE_CONFLICT", "CONDITION_REFINEMENT_CONFLICT"} for i in issues
        ),
    }
    return base


def validate_model_draft(draft, previous=None):
    """Validate model refinements, including usable titles, without an answer oracle."""
    errors = validate_draft(draft)
    if errors:
        return errors
    known = {p["key"]: p for p in (previous or {}).get("positions", [])}
    common = draft["notice_facts"].get("fields", {})
    for index, position in enumerate(draft["positions"]):
        fields = {**common, **position.get("fields", {})}
        others = [
            c
            for group in (draft["notice_facts"], position)
            for c in group.get("conditions", [])
            if c["type"] == "other"
        ]
        if len(others) > 1:
            errors.append(
                {
                    "code": "OTHER_CONDITION_CARDINALITY",
                    "message": "Keep at most one complete residual after notice/local inheritance",
                    "path": f"positions[{index}].conditions",
                }
            )
        title = fields.get("title", {})
        old_fields = {
            **(previous or {}).get("notice_facts", {}).get("fields", {}),
            **known.get(position["key"], {}).get("fields", {}),
        }
        old_title = old_fields.get("title", {}) if position["key"] in known else {}
        if (
            old_title.get("tag") == "source"
            and old_title.get("value")
            and title.get("tag") == "source"
            and title.get("value")
            and title["value"] != old_title["value"]
            and not any(
                c["operation"] == "replace_field"
                and c["position_key"] == position["key"]
                and c["field"] == "title"
                and c["before"] == old_title
                and c["after"] == title
                for c in draft.get("corrections", [])
            )
        ):
            errors.append(
                {
                    "code": "POSITION_DRAFT_SCHEMA",
                    "message": (
                        "Preserve the existing source title verbatim for this key; "
                        "put unit qualifiers in hiring_unit"
                    ),
                    "path": f"positions[{index}].fields.title",
                }
            )
        if not title.get("value") and not old_title.get("value"):
            errors.append(
                {
                    "code": "POSITION_DRAFT_SCHEMA",
                    "message": "An emitted position requires a usable local or inherited title",
                    "path": f"positions[{index}].fields.title",
                }
            )
    return errors
