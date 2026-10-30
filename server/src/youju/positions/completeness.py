"""Source-neutral extraction diagnostics, not a truth oracle or cost router.

Only recognizable, unrepresented information is a gap. An unknown optional field
without supporting text is not an error. Never fetch, call models, or mutate facts.
"""

import re

STRUCTURED_SIGNALS = {
    "education": r"学历|硕士研究生|博士研究生|(?:本科|专科|大专|研究生)(?:及以上|以上|学历)",
    "degree": r"(?:学士|硕士|博士)学位",
    "major": r"专业(?:要求|不限|背景)|(?:相关|类)专业",
    "age": r"(?:\d+\s*(?:周岁|岁)|年龄).{0,12}(?:以下|以上|不超过|不低于|放宽)|"
    r"(?:不超过|不满|年满)\s*\d+\s*(?:周岁|岁)",
    "work_experience": r"工作经验|工作经历|\d+\s*年.{0,8}经验",
    "graduation_status": r"应届(?:毕业生)?",
    "graduation_year": r"20\d{2}\s*届",
    "political_status": r"中共党员|政治面貌",
    "license": r"资格证|执业证|教师资格",
    "language": r"英语.{0,8}(?:级|水平)|雅思|托福",
}
# Preference/exception prose must be inspected semantically, not turned into a
# mandatory rule by keyword matching. Its existence alone is not a field error.
QUALIFIED = re.compile(r"优先|优选|放宽|除外|例外|或|之一|任一|但")
CONDITIONAL = re.compile(r"放宽|除外|例外|或|之一|任一|但")
STRICT_COMPLETION_CODES = frozenset(
    {
        "FIELD_COMPLETION_REQUIRED",
        "CONDITION_STRUCTURE_GAP",
        "OTHER_CONDITION_CARDINALITY",
        "HEADCOUNT_CONFLICT",
    }
)


def structured_signals(text):
    kinds = []
    for kind, pattern in STRUCTURED_SIGNALS.items():
        for match in re.finditer(pattern, text):
            if kind == "education" and re.search(
                r"指导|培养|讲授", text[max(0, match.start() - 12) : match.start()]
            ):
                continue  # Teaching duties are not an applicant education requirement.
            kinds.append(kind)
            break
    return kinds


def residual_structure_gaps(text):
    # A preferred/exception clause must not mask a different, plainly mandatory
    # requirement in the single residual text. Keep comma-linked exceptions intact.
    return sorted(
        {
            kind
            for clause in re.split(r"[；;\n。]+", text)
            if not QUALIFIED.search(clause)
            for kind in structured_signals(clause)
        }
    )


def unresolved_conditional_types(text):
    """Eligibility exceptions/alternatives are not the same as harmless preferences."""
    return sorted(
        {
            kind
            for clause in re.split(r"[；;\n。]+", text)
            if CONDITIONAL.search(clause)
            for kind in structured_signals(clause)
        }
    )


def title_headcount(title):
    """Only an explicit trailing count attached to this title, never notice totals."""
    if not isinstance(title, str):
        return None
    match = re.search(r"[（(]\s*(\d+)\s*(?:人|名)\s*[）)]\s*$", title)
    return int(match[1]) if match else None


def completion_issues(draft):
    """Conservative gaps, scoped by normalized row ordinal; not an accuracy score."""
    issues = []
    common = draft.get("notice_facts", {})
    for index, position in enumerate(draft.get("positions", [])):
        path = f"positions[{index}]"
        fields = {**common.get("fields", {}), **position.get("fields", {})}
        conditions = [*common.get("conditions", []), *position.get("conditions", [])]
        title_count = title_headcount(fields.get("title", {}).get("value"))
        count = fields.get("headcount", {}).get("value")
        if title_count is not None and count != title_count:
            issues.append(
                {
                    "code": "FIELD_COMPLETION_REQUIRED" if count is None else "HEADCOUNT_CONFLICT",
                    "message": "Explicit title headcount is missing or disagrees with the field",
                    "path": path + ".fields.headcount",
                }
            )
        other = [c for c in conditions if c.get("type") == "other"]
        if len(other) > 1:
            issues.append(
                {
                    "code": "OTHER_CONDITION_CARDINALITY",
                    "message": "Extract at most one complete residual requirement per position",
                    "path": path + ".conditions",
                }
            )
        for condition in other:
            value = condition.get("value") or {}
            text = value.get("text", "")
            kinds = residual_structure_gaps(text)
            # Do not suppress qualified text: it still needs semantic attention,
            # but must not create a hard eligibility rule or whole-job dispute.
            if kinds:
                issues.append(
                    {
                        "code": "CONDITION_STRUCTURE_GAP",
                        "message": "Residual text contains a recognizable typed requirement",
                        "path": path + ".conditions",
                        "condition_id": condition["id"],
                        "candidate_types": kinds,
                    }
                )
            conditional = unresolved_conditional_types(text)
            if conditional:
                issues.append(
                    {
                        "code": "UNRESOLVED_CONDITION",
                        "message": "Typed eligibility exception/alternative remains unresolved",
                        "path": path + ".conditions",
                        "condition_id": condition["id"],
                        "candidate_types": conditional,
                    }
                )
        from .requirement_coverage import requirement_gaps

        for source_path, group in (("notice_facts", common), (path, position)):
            requirement_text = (
                group.get("fields", {}).get("requirements_text", {}).get("value") or ""
            )
            applicable = conditions if source_path == path else common.get("conditions", [])
            graphs = [
                g.get("condition_logic")
                for g in ((common, position) if source_path == path else (common,))
                if g.get("condition_logic") is not None
            ]
            logic = (
                graphs[0]
                if len(graphs) == 1
                else ({"operator": "all", "items": graphs} if graphs else None)
            )
            issues.extend(
                requirement_gaps(
                    requirement_text,
                    applicable,
                    signal_types=structured_signals,
                    path=path,
                    source_path=source_path + ".fields.requirements_text",
                    position_key=position.get("key", str(index)),
                    logic=logic,
                )
            )
        for condition in conditions:
            if condition.get("type") != "other" and condition.get("value") is None:
                issues.append(
                    {
                        "code": "FIELD_COMPLETION_REQUIRED",
                        "message": "A declared typed requirement still has an unknown value",
                        "path": path + ".conditions",
                        "condition_id": condition["id"],
                    }
                )
    return issues


def facts_completion_issues(positions):
    """Check already-normalized facts; malformed raw fields belong to the normalizer."""
    from .facts import POSITION_FIELDS

    return completion_issues(
        {
            "notice_facts": {},
            "positions": [
                {
                    "fields": {name: p[name] for name in POSITION_FIELDS},
                    "conditions": p["conditions"],
                }
                for p in positions
            ],
        }
    )


def needs_semantic_completion(draft):
    """Readable qualified requirements need model interpretation, not an error tag."""
    groups = [draft.get("notice_facts", {}), *draft.get("positions", [])]
    return bool(completion_issues(draft)) or any(
        c.get("type") == "other" and structured_signals((c.get("value") or {}).get("text", ""))
        for group in groups
        for c in group.get("conditions", [])
    )


def material_field_hints(bundle):
    """Bounded readable prose signals for model attention, never inherited facts.

    An explicit location elsewhere in an announcement can describe only one role;
    the model must resolve scope. Do not mark every missing location as an error.
    """
    hints = []
    for doc in [bundle.get("page", {}), *(bundle.get("attachments") or [])]:
        if not isinstance(doc, dict):
            continue
        for block in doc.get("blocks") or []:
            if (
                not isinstance(block, dict)
                or block.get("region_kind") in {"navigation", "recommendation"}
                or re.search(
                    r"(?:nav|header|footer|aside)(?=:|\s|$)", str(block.get("locator") or "")
                )
            ):
                continue
            text = block.get("text", "")
            if isinstance(text, str) and re.search(r"(?:工作|任职|办公)地点\s*[：:]\s*\S", text):
                hints.append({"field": "work_location", "text": text[:240], "scope": "unresolved"})
                if len(hints) == 20:
                    return hints
    return hints


AUTHOR_FIELD_QUALITY_INSTRUCTIONS = """
Field completeness is not schema validity. On detailed extraction inspect every
available position for hiring_unit, employer, headcount/headcount_description,
work_location, application dates, employment mechanism and staffing entitlement.
Extract explicit '(1人)' title counts without changing titles. Notice-wide totals
must not be copied to each role or split without evidence. Contact addresses are
not automatically workplaces; contextual location inference is ai_inferred.
Inspect ALL requirements for education, degree, study form, major, age, experience,
graduation status/year, political status, licenses and language. Keep known typed
condition IDs when refining; extend their logic without inventing AND/OR. Extract
recognizable conditions, not just their text. At most ONE complete other condition
may remain after notice/local inheritance. Preferences and exceptions are not
mandatory gates: preserve them verbatim in that residual text when the schema
cannot express them. Never invent minimum years from qualitative experience.
Filing/quota management can coexist with explicit non-establishment. Prioritize
explicit establishment entitlement in staffing_status under the current contract;
retain additional management wording in requirements_text rather than dropping it
or inventing an employment conflict. Dispatch plus establishment in the same
position requires scope/conflict resolution, not silent acceptance.
Unknown optional fields without evidence are not extraction failures. Missing
attachments alone do not request AI or invalidate available facts. Detailed Flow
must honor runtime:inspect's needs_detailed signal even when rows already exist.
Test field values and condition semantics, not merely row counts/schema validity.
"""
