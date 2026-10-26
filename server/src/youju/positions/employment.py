"""Separate staffing entitlement from employment mechanism; identity is not evidence."""

import re

EMPLOYMENT_TYPES = frozenset({"personnel_agency", "labor_dispatch", "labor_contract", "other"})
STAFFING_STATUSES = frozenset({"established", "non_established", "filing_system", "quota_managed"})


def employment_from_text(text):
    from .facts import TaggedField

    text = text if isinstance(text, str) else ""
    issues = []
    modes = []
    for phrase, mode in (
        ("人事代理", "personnel_agency"),
        ("劳务派遣", "labor_dispatch"),
        ("劳动合同制", "labor_contract"),
    ):
        for match in re.finditer(phrase, text):
            if not re.search(
                r"不(?:实行|采用|属于|是)?$|非$|无需$",
                text[max(0, match.start() - 6) : match.start()],
            ):
                modes.append(mode)
    staffing = []
    negative = re.search(
        r"不(?:纳入|占用|属于|具有|享有|提供|解决)?(?:事业)?编制|非(?:事业)?编制|编外|无(?:事业)?编制",
        text,
    )
    if negative:
        staffing.append("non_established")
    # Strip negative clauses before looking for an explicit positive entitlement.
    positive_text = re.sub(
        r"[^。；;，,\n]*(?:不(?:纳入|占用|属于|具有|享有|提供|解决)?(?:事业)?编制|非(?:事业)?编制|编外|无(?:事业)?编制)[^。；;，,\n]*",
        "",
        text,
    )
    if re.search(
        r"(?:纳入|占用|享有|具有|提供|解决)(?:事业)?编制|事业编制(?:岗位|人员|职工)|^(?:事业编制|在编)$",
        positive_text,
    ):
        staffing.append("established")
    management = []
    if re.search(r"备案制", text) and not re.search(r"非备案制|不实行备案制", text):
        management.append("filing_system")
    if re.search(r"员额制", text) and not re.search(r"非员额制|不实行员额制", text):
        management.append("quota_managed")

    def resolve(values, name):
        values = set(values)
        if len(values) > 1:
            issues.append(
                {"code": "EMPLOYMENT_CONFLICT", "message": f"Conflicting {name}: {sorted(values)}"}
            )
            return TaggedField()
        return TaggedField(next(iter(values)), "source") if values else TaggedField()

    # Filing/quota management can coexist with explicit non-establishment.
    # Within the existing public field, explicit entitlement has precedence;
    # never classify these compatible facts as competing entitlement assertions.
    # Do not infer entitlement from a management system alone.
    fields = {
        "employment_type": resolve(modes, "employment_type"),
        "staffing_status": resolve(staffing if staffing else management, "staffing_status"),
    }
    issues.extend(employment_consistency_issues(fields))
    return fields, issues


def employment_consistency_issues(fields):
    """Check effective same-position facts, including model-generated combinations."""

    def value(name):
        fact = fields.get(name)
        return fact.get("value") if isinstance(fact, dict) else getattr(fact, "value", None)

    if value("employment_type") == "labor_dispatch" and value("staffing_status") == "established":
        return [
            {
                "code": "EMPLOYMENT_CONFLICT",
                "message": "Resolve dispatch versus establishment within the same position scope",
            }
        ]
    return []
