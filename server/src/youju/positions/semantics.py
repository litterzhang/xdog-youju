"""Independent semantic comparisons. Old outputs are baselines, never gold.

Changes without an independent expected value require review, not an invented
accuracy verdict. Position titles alone never establish correspondence.
"""

import json
from collections import defaultdict
from copy import deepcopy

from .facts import POSITION_FIELDS


def _condition_tree(position):
    conditions = {
        item["id"]: {k: v for k, v in item.items() if k != "id"}
        for item in position.get("conditions", [])
    }

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {"condition_id"}:
                key = value["condition_id"]
                return {"condition": conditions.get(key, {"unresolved_condition_ref": key})}
            result = {key: resolve(item) for key, item in value.items()}
            if result.get("operator") in {"all", "any"} and isinstance(result.get("items"), list):
                result["items"] = sorted(result["items"], key=canonical)
            return result
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return {
        "conditions": sorted(conditions.values(), key=canonical),
        "logic": resolve(position.get("condition_logic")),
    }


def _identity(position):
    def known(field):
        value = position.get(field, {})
        return value.get("value") if value.get("tag") == "source" else None

    title, unit = known("title"), known("hiring_unit") or known("employer")
    if not title or not unit:
        return None
    return unit, title, known("work_location")


def compare_outputs(before, after):
    """Return bounded field/condition/scope deltas and unresolved correspondence.

    Unchanged same-order output is only an unchanged baseline, not evidence of truth.
    Duplicate identities, unknown units and changed identities remain unresolved.
    """
    issues, old, new = [], defaultdict(list), defaultdict(list)
    if before.get("positions") == after.get("positions"):
        return {"status": "unchanged_baseline", "issues": [], "accuracy_verified": False}
    for target, output in ((old, before), (new, after)):
        for number, position in enumerate(output.get("positions", [])):
            target[_identity(position)].append((number, position))
    for identity in old.keys() | new.keys():
        previous, current = old[identity], new[identity]
        if identity is None or len(previous) != 1 or len(current) != 1:
            issues.append(
                {
                    "code": "POSITION_CORRESPONDENCE_UNRESOLVED",
                    "path": "positions",
                    "old_indices": [n for n, _ in previous],
                    "new_indices": [n for n, _ in current],
                    "basis": "baseline_delta_not_gold",
                }
            )
            continue
        (_, left), (number, right) = previous[0], current[0]
        for field in POSITION_FIELDS:
            if left.get(field) != right.get(field):
                issues.append(
                    {
                        "code": "SEMANTIC_FIELD_CHANGE",
                        "path": f"positions[{number}].{field}",
                        "actual": deepcopy(right.get(field)),
                        "previous": deepcopy(left.get(field)),
                        "basis": "baseline_delta_not_gold",
                    }
                )
        if _condition_tree(left) != _condition_tree(right):
            issues.append(
                {
                    "code": "SEMANTIC_CONDITION_CHANGE",
                    "path": f"positions[{number}].conditions",
                    "basis": "baseline_delta_not_gold",
                }
            )
        if len(issues) >= 200:
            break
    return {
        "status": "needs_review" if issues else "unchanged_baseline",
        "issues": issues[:200],
        "truncated": len(issues) > 200,
        "accuracy_verified": False,
    }


def check_expected(result, expected):
    """Host-only reviewed assertions, NOT author supplied expectations or model labels.

    Expectations explicitly specify exact fields/count/conditions; omitted fields
    stay unasserted. A confirmed false-job removal may reduce count if this case
    specifies the full expected job set. Callers must protect gold outside /work.
    """
    if (
        not isinstance(expected, dict)
        or expected.get("basis") not in {"human-confirmed", "independent-rule"}
        or not expected.get("reference")
        or not isinstance(expected.get("positions"), list)
    ):
        return {"status": "unlabeled", "issues": [], "accuracy_verified": False}
    if not expected["positions"] and expected.get("complete_position_set") is not True:
        return {"status": "unlabeled", "issues": [], "accuracy_verified": False}
    issues = []
    if expected.get("complete_position_set") and len(result.get("positions", [])) != len(
        expected["positions"]
    ):
        issues.append(
            {
                "code": "EXPECTED_POSITION_SET",
                "path": "positions",
                "actual": len(result.get("positions", [])),
                "expected": len(expected["positions"]),
                "basis": expected["basis"],
                "reference": expected["reference"],
            }
        )
    for item in expected["positions"]:
        selector = item.get("selector", {})
        # A title alone is deliberately insufficient for matching gold as well.
        if "title" not in selector or not ({"hiring_unit", "employer"} & selector.keys()):
            issues.append({"code": "EXPECTED_IDENTITY_UNRESOLVED", "path": "positions"})
            continue
        matches = [
            (number, p)
            for number, p in enumerate(result.get("positions", []))
            if all(p.get(key, {}).get("value") == value for key, value in selector.items())
        ]
        if len(matches) != 1:
            issues.append(
                {"code": "EXPECTED_CORRESPONDENCE", "path": "positions", "matches": len(matches)}
            )
            continue
        number, position = matches[0]
        for field, value in item.get("fields", {}).items():
            if field not in POSITION_FIELDS:
                raise ValueError("Invalid semantic field expectation")
            if position.get(field) != value:
                issues.append(
                    {
                        "code": "EXPECTED_FIELD",
                        "path": f"positions[{number}].{field}",
                        "actual": deepcopy(position.get(field)),
                        "expected": deepcopy(value),
                        "basis": expected["basis"],
                        "reference": expected["reference"],
                    }
                )
        if "conditions" in item and _condition_tree(position) != _condition_tree(item):
            issues.append(
                {
                    "code": "EXPECTED_CONDITIONS",
                    "path": f"positions[{number}].conditions",
                    "basis": expected["basis"],
                    "reference": expected["reference"],
                }
            )
    return {
        "status": "failed" if issues else "passed",
        "issues": issues,
        "accuracy_verified": not issues,
    }


def compare_reviewed(before, after, expected):
    """Excuse only changes explicitly covered by a passing reviewed assertion.

    Complete membership permits removal of a distinct old false job. Partial field
    assertions never authorize arbitrary changes to surviving conditions or fields;
    ambiguous identity still requires exhaustive gold or independent resolution.
    """
    delta = compare_outputs(before, after)
    verified = check_expected(after, expected)
    if verified["status"] != "passed":
        return delta
    covered, remaining = [], []
    assertions = {}
    for item in expected["positions"]:
        matches = [
            n
            for n, p in enumerate(after.get("positions", []))
            if all(p.get(k, {}).get("value") == v for k, v in item["selector"].items())
        ]
        if len(matches) == 1:
            assertions[matches[0]] = item
    for issue in delta["issues"]:
        permitted = False
        for number, assertion in assertions.items():
            prefix = f"positions[{number}]."
            path = issue.get("path", "")
            if issue["code"] == "SEMANTIC_FIELD_CHANGE" and path.startswith(prefix):
                field = path[len(prefix) :]
                permitted = field in assertion.get("fields", {})
            elif issue["code"] == "SEMANTIC_CONDITION_CHANGE" and path == prefix + "conditions":
                permitted = {"conditions", "condition_logic"} <= assertion.keys()
            if permitted:
                break
        if (
            issue["code"] == "POSITION_CORRESPONDENCE_UNRESOLVED"
            and expected.get("complete_position_set") is True
            and len(issue.get("old_indices", [])) == 1
            and not issue.get("new_indices")
        ):
            permitted = True
        (covered if permitted else remaining).append(issue)
    return {
        **delta,
        "status": "needs_review" if remaining else "reviewed_change",
        "issues": remaining,
        "reviewed_changes": covered,
        "basis": expected["basis"],
        "reference": expected["reference"],
    }
