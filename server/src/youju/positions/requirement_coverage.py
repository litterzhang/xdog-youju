"""Conservative, source-neutral requirement coverage; never generate business facts.

A cue is a request to inspect a particular piece of declared requirement prose,
not a claim that its keywords establish eligibility. Unknown scope/logic remains
semantic work. This module has no IO, clock, router or model dependency.
"""

import re

LEVELS = {
    "博士研究生": "doctorate",
    "硕士研究生": "master",
    "博士": "doctorate",
    "硕士": "master",
    "本科": "bachelor",
    "学士": "bachelor",
    "大专": "associate",
    "专科": "associate",
    "研究生": "postgraduate",
}
NUMBER = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
PREFERENCE = re.compile(r"优先|优选")
QUALIFIED = re.compile(r"放宽|除外|例外|或|之一|任一|但")


def _represented(kind, text, condition):
    value = condition.get("value")
    if condition.get("type") != kind or not value:
        return False
    compact = re.sub(r"\s+", "", text)
    if kind in {"education", "degree"}:
        if "不限" in compact:
            return value.get("level") == "unrestricted"
        levels = [v for k, v in LEVELS.items() if k in compact]
        # Longest first avoids treating 硕士研究生 as generic 研究生.
        expected = next((v for k, v in LEVELS.items() if k in compact), None)
        if expected and value.get("level") != expected:
            return False
        if levels and ("及以上" in compact or "以上" in compact):
            return condition.get("operator") == "at_least"
    if kind == "work_experience":
        years = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十])年", compact)
        if years:
            number = NUMBER.get(years[1])
            number = number if number is not None else float(years[1])
            if value.get("minimum_years") != number:
                return False
            if "以上" in compact and condition.get("operator") != "at_least":
                return False
    if kind == "age":
        bound = re.search(r"(不超过|年满)(\d+)(?:周岁|岁)", compact)
        if bound:
            endpoint = "maximum" if bound[1] == "不超过" else "minimum"
            return value.get(endpoint) == int(bound[2])
    if kind == "major":
        if "不限" in compact:
            return value.get("unrestricted") is True
        # Merely having a major atom elsewhere does not cover this clause.
        names = [re.sub(r"\s+", "", name) for name in value.get("names", [])]
        if value.get("unrestricted") is not False or not names:
            return False
        if not all(name in compact for name in names):
            return False
        # Check literal enumerations only, not a taxonomy or inferred synonyms.
        listed = re.search(r"([\w、/]+?)(?:等)?(?:相关)?专业", re.sub(r"^\d+[.、．]", "", compact))
        if listed and re.search(r"[、/]", listed[1]):
            tokens = [re.sub(r"^(?:要求|具有|须为)", "", t) for t in re.split(r"[、/]", listed[1])]
            return all(any(t in n or n in t for n in names) for t in tokens)
        return True
    return True


def _alternative_covers(logic, conditions, kinds):
    by_id = {c["id"]: c["type"] for c in conditions}

    def references(node):
        if not node:
            return set()
        if "condition_id" in node:
            return {by_id.get(node["condition_id"])}
        return set().union(*(references(child) for child in node["items"]))

    if not logic or "condition_id" in logic:
        return False
    if logic["operator"] == "any" and set(kinds) <= references(logic):
        return True
    return any(_alternative_covers(child, conditions, kinds) for child in logic["items"])


def _clauses(text):
    for match in re.finditer(r"[^；;\n。]+", text):
        clause = match[0]
        if PREFERENCE.search(clause) and not QUALIFIED.search(clause):
            for part in re.finditer(r"[^，,]+", clause):
                yield part[0], match.start() + part.start(), match.start() + part.end()
        else:
            yield clause, match.start(), match.end()


def requirement_gaps(
    text, conditions, *, signal_types, path, source_path, position_key, logic=None
):
    """Return located attention requests. Never equate nonempty conditions with coverage."""
    issues = []
    for clause, start, end in _clauses(text):
        kinds = signal_types(clause)
        if not kinds or PREFERENCE.search(clause):
            continue  # Preferences are retained prose, never mandatory facts.
        missing = [k for k in kinds if not any(_represented(k, clause, c) for c in conditions)]
        alternative_gap = (
            len(kinds) > 1
            and re.search(r"或|之一|任一", clause)
            and not _alternative_covers(logic, conditions, kinds)
        )
        if not missing and not alternative_gap:
            continue
        if alternative_gap:
            missing = sorted(set(missing) | set(kinds))
        qualified = bool(QUALIFIED.search(clause))
        issues.append(
            {
                "code": "UNRESOLVED_CONDITION" if qualified else "FIELD_COMPLETION_REQUIRED",
                "message": "Requirement prose is not represented by scoped typed conditions",
                "path": path + ".conditions",
                "position_key": position_key,
                "source_path": source_path,
                "start": start,
                "end": end,
                "excerpt": clause[:512],
                "candidate_types": missing,
                "reason": "scope_or_alternative_requires_interpretation"
                if qualified
                else "no_matching_typed_value_in_applicable_conditions",
            }
        )
    return issues
