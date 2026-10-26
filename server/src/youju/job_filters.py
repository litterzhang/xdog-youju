"""Literal saved-fact facets, never applicant eligibility or current hiring claims."""

from youju.positions.facts import EMPLOYMENT_TYPES

EDUCATION_FILTERS = frozenset(
    {
        "all",
        "unknown",
        "associate",
        "bachelor",
        "postgraduate",
        "master",
        "doctorate",
        "unrestricted",
    }
)
EMPLOYMENT_FILTERS = frozenset({"all", "unknown", "public_institution_staff", *EMPLOYMENT_TYPES})
DEADLINE_FILTERS = frozenset(
    {"all", "not_expired", "expired", "unknown", "open_ended", "until_filled"}
)
DEGREE_FILTERS = frozenset({"all", "unknown", "bachelor", "master", "doctorate", "unrestricted"})
STAFFING_FILTERS = frozenset(
    {"all", "unknown", "established", "non_established", "filing_system", "quota_managed"}
)
EXPERIENCE_FILTERS = frozenset({"all", "unknown", "not_required", "experienced"})
FACETS = {
    "education": EDUCATION_FILTERS,
    "employment": EMPLOYMENT_FILTERS,
    "deadline": DEADLINE_FILTERS,
    "degree": DEGREE_FILTERS,
    "staffing": STAFFING_FILTERS,
    "experience": EXPERIENCE_FILTERS,
}


def condition_matches(conditions, kind, selected):
    if selected == "all":
        return True
    levels = [
        c.fact.value["level"] for c in conditions if c.type == kind and c.fact.value is not None
    ]
    return not levels if selected == "unknown" else selected in levels


def additional_matches(facts, validity, *, deadline, degree, staffing, experience):
    if not condition_matches(facts.conditions, "degree", degree):
        return False
    identity = facts.fields["staffing_status"].value
    if staffing != "all" and identity != (None if staffing == "unknown" else staffing):
        return False
    if deadline in {"not_expired", "expired", "unknown"} and validity["status"] != deadline:
        return False
    if (
        deadline in {"open_ended", "until_filled"}
        and facts.fields["application_rule"].value != deadline
    ):
        return False
    years = [
        c.fact.value["minimum_years"]
        for c in facts.conditions
        if c.type == "work_experience"
        and c.fact.value is not None
        and c.fact.value["minimum_years"] is not None
    ]
    if experience == "unknown":
        return not years
    if experience == "not_required":
        # A missing requirement is not an explicit zero-year requirement.
        return bool(years) and all(value == 0 for value in years)
    if experience == "experienced":
        return any(value > 0 for value in years)
    return True
