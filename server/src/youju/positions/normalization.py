"""The pure, sole draft -> final position-facts-v3 business transformation."""

import json
from copy import deepcopy
from dataclasses import replace

from .completeness import facts_completion_issues
from .drafts import ExtractionDraft
from .employment import employment_consistency_issues
from .facts import POSITION_FIELDS, Condition, PositionFacts, TaggedField

CONTRACT = "position-facts-v3"


def normalize_extraction(draft, *, context=None):
    """Normalize available facts without capture gating, routing or external authority.

    context is reserved for caller metadata; it never supplies dates, time, validity
    or missing facts. Invalid individual fields become unknown with visible errors.
    Inputs are not mutated. Neither an LLM nor a network/clock is used.
    """
    del context
    try:
        draft = ExtractionDraft.from_dict(draft) if isinstance(draft, dict) else draft
        if not isinstance(draft, ExtractionDraft):
            raise ValueError("Expected ExtractionDraft or its canonical JSON object")
        # Recheck mutable payloads even for a dataclass constructed earlier.
        draft = ExtractionDraft.from_dict(deepcopy(draft.as_dict()))
    except (TypeError, ValueError) as error:
        return {
            "schema_version": CONTRACT,
            "status": "failed",
            "positions": [],
            "errors": [{"code": "POSITION_DRAFT_SCHEMA", "message": str(error)}],
            "coverage": {"supported": False, "complete": False, "needs_detailed": True},
        }
    errors = deepcopy(draft.issues)
    positions = []

    def error(code, message, path):
        errors.append({"code": code, "message": str(message), "path": path})

    def field_values(raw, path):
        if not isinstance(raw, dict):
            error("POSITION_FIELDS", "fields must be an object", path)
            return {}
        result = {}
        for name, value in raw.items():
            if name not in POSITION_FIELDS:
                error("UNKNOWN_POSITION_FIELD", name, path)
                continue
            try:
                tagged = TaggedField.from_dict(value)
                # Validate each field independently, preserving other healthy fields.
                probe = {key: TaggedField() for key in POSITION_FIELDS}
                probe[name] = tagged
                if name not in {"deadline_on", "application_start_on"}:
                    PositionFacts(probe)
                elif tagged.value is not None:
                    from datetime import date

                    if date.fromisoformat(tagged.value).isoformat() != tagged.value:
                        raise ValueError("Date projection requires canonical ISO")
                result[name] = tagged
            except (ValueError, TypeError, KeyError) as exc:
                result[name] = TaggedField()
                error("INVALID_POSITION_FIELD", exc, f"{path}.{name}")
        return result

    common = draft.notice_facts
    common_fields = field_values(common.get("fields", {}), "notice_facts.fields")

    def condition_group(raw, prefix, scope, path):
        conditions = []
        items = raw.get("conditions", [])
        if not isinstance(items, list) or len(items) > 200:
            error("INVALID_CONDITIONS", "conditions must be a bounded array", path)
            return (), None
        for index, item in enumerate(items):
            try:
                condition = Condition.from_dict(item)
                conditions.append(replace(condition, id=prefix + condition.id, scope=scope))
            except (ValueError, TypeError, KeyError) as exc:
                error("INVALID_CONDITION", exc, f"{path}.conditions[{index}]")
                # Retain the ID as an explicitly unknown condition where possible,
                # so logical structure and all unaffected requirements survive.
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    try:
                        conditions.append(
                            Condition(
                                prefix + item["id"],
                                item.get("type", "other"),
                                item.get("operator", "unspecified"),
                                TaggedField(),
                                scope,
                            )
                        )
                    except (ValueError, TypeError):
                        pass
        logic = deepcopy(raw.get("condition_logic"))

        def rename(node, depth=0):
            if depth > 12 or not isinstance(node, dict):
                raise ValueError("Invalid condition logic")
            if set(node) == {"condition_id"} and isinstance(node["condition_id"], str):
                node["condition_id"] = prefix + node["condition_id"]
            elif set(node) == {"operator", "items"} and isinstance(node["items"], list):
                for child in node["items"]:
                    rename(child, depth + 1)
            else:
                raise ValueError("Invalid condition logic")

        try:
            if logic is not None:
                rename(logic)
            PositionFacts(
                {name: TaggedField() for name in POSITION_FIELDS}, tuple(conditions), logic
            )
        except (ValueError, TypeError, KeyError) as exc:
            error("INVALID_CONDITION_LOGIC", exc, path)
            # Keep known typed atoms. Unknown relationships are representable;
            # neither an invented AND nor flattening all facts into text is needed.
            return tuple(conditions), None
        if conditions and logic is None:
            error(
                "UNRESOLVED_CONDITION_LOGIC", "Condition relationship is explicitly unknown", path
            )
        return tuple(conditions), logic

    common_conditions, common_logic = condition_group(common, "n:", "notice", "notice_facts")
    for index, raw in enumerate(draft.positions):
        path = f"positions[{index}]"
        raw_fields = raw.get("fields", {})
        fields = {name: TaggedField() for name in POSITION_FIELDS}
        fields.update(common_fields)
        local_fields = field_values(raw_fields, path + ".fields")
        fields.update(local_fields)
        if (
            common_fields.get("requirements_text", TaggedField()).value
            and local_fields.get("requirements_text", TaggedField()).value
        ):
            fields["requirements_text"] = TaggedField(
                common_fields["requirements_text"].value
                + "\n"
                + local_fields["requirements_text"].value,
                "ai_inferred"
                if "ai_inferred"
                in {common_fields["requirements_text"].tag, local_fields["requirements_text"].tag}
                else "source",
            )
        for issue in employment_consistency_issues(fields):
            error(issue["code"], issue["message"], path + ".fields")
        if fields["title"].value is None:
            error("MISSING_POSITION_TITLE", "Position has no usable title", path)
            continue
        common_dates, overrides = common.get("dates", {}), raw.get("dates", {})
        if not isinstance(common_dates, dict) or not isinstance(overrides, dict):
            error("INVALID_APPLICATION_DATES", "dates must be an object", path)
            common_dates, overrides = {}, {}
        dates = {
            endpoint: deepcopy(overrides.get(endpoint, common_dates.get(endpoint)))
            for endpoint in ("start", "end")
        }
        for endpoint in dates:
            if dates[endpoint] is not None:
                from .deadlines import validate_date_fact

                try:
                    validate_date_fact(dates[endpoint])
                except (ValueError, TypeError) as exc:
                    error("INVALID_APPLICATION_DATE", exc, path + ".dates." + endpoint)
                    dates[endpoint] = None
        from .deadlines import validate_dates

        try:
            validate_dates(dates)
        except (ValueError, TypeError) as exc:
            error("APPLICATION_DATE_CONFLICT", exc, path + ".dates")
            dates = {"start": None, "end": None}
        for endpoint, name in (("start", "application_start_on"), ("end", "deadline_on")):
            if fields[name].value is not None and not dates[endpoint]:
                error(
                    "UNSTRUCTURED_APPLICATION_DATE",
                    "Use extracted structured dates, not a flat deadline",
                    path + "." + name,
                )
            fact = dates[endpoint]
            fields[name] = (
                TaggedField(fact["date"], fact["tag"]) if fact and fact["date"] else TaggedField()
            )
        if any(dates.values()):
            tag = (
                "ai_inferred"
                if any(d and d["tag"] == "ai_inferred" for d in dates.values())
                else "source"
            )
            fields["application_dates"] = TaggedField(dates, tag)
        else:
            fields["application_dates"] = TaggedField()
        if dates["end"]:
            if fields["application_rule"].value not in {None, "not_stated", "fixed_deadline"}:
                error(
                    "APPLICATION_RULE_CONFLICT",
                    "Fixed date conflicts with declared rolling rule",
                    path,
                )
                fields["application_rule"] = TaggedField()
            else:
                fields["application_rule"] = TaggedField("fixed_deadline", "source")
        conditions, logic = condition_group(raw, "p:", "position", path)
        conditions = common_conditions + conditions
        groups = [g for g in (common_logic, logic) if g is not None]
        unknown_logic = bool(
            (common_conditions and common_logic is None)
            or (conditions[len(common_conditions) :] and logic is None)
        )
        logic = (
            None
            if unknown_logic
            else {"operator": "all", "items": groups}
            if len(groups) > 1
            else groups[0]
            if groups
            else None
        )
        try:
            positions.append(PositionFacts(fields, conditions, logic).as_dict())
        except (ValueError, TypeError) as exc:
            error("POSITION_INVARIANT", exc, path)
            # More than 200 common+local conditions cannot hide a usable job.
            fields["requirements_text"] = TaggedField(
                (fields["requirements_text"].value or "")
                + "\n"
                + json.dumps([c.as_dict() for c in conditions], ensure_ascii=False),
                "source",
            )
            positions.append(PositionFacts(fields).as_dict())
    # Run semantic-shape checks on canonical facts, after per-field recovery.
    # Malformed raw conditions must never prevent healthy fields/jobs surviving.
    for issue in facts_completion_issues(positions):
        if issue not in errors:
            errors.append(issue)
    coverage = deepcopy(draft.coverage)
    coverage.setdefault("needs_detailed", False)
    if any(
        e["code"]
        in {
            "INVALID_CONDITION",
            "INVALID_CONDITION_LOGIC",
            "MISSING_POSITION_TITLE",
            "POSITION_INVARIANT",
            "FIELD_COMPLETION_REQUIRED",
            "CONDITION_STRUCTURE_GAP",
            "OTHER_CONDITION_CARDINALITY",
            "HEADCOUNT_CONFLICT",
        }
        for e in errors
    ):
        coverage["needs_detailed"] = True
    if errors:
        coverage["complete"] = False
    status = (
        ("partial" if errors or not coverage["complete"] else "success")
        if positions
        else "empty"
        if coverage.get("empty_capture") and coverage["complete"] and not errors
        else "review"
    )
    return {
        "schema_version": CONTRACT,
        "status": status,
        "positions": positions,
        "errors": errors,
        "coverage": coverage,
    }
