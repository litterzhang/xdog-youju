"""Host-owned cadence; model labels cannot disable link discovery or monitoring."""

POLICY_VERSION = "page-cadence-v1"


def revisit_for(result):
    roles = result.get("page_roles", [])
    business = result.get("business_type", "unknown")
    if "notice_list" in roles or (
        {"article", "attachment"}.intersection(roles)
        and business in {"recruitment", "correction", "cancellation"}
    ):
        return "daily"
    # Slow historical change is not proof that a future announcement cannot be short-lived.
    # Stop-scanning requires an explicit operator policy, never a learned negative label.
    return "weekly"


def scheduled(result):
    result = dict(result)
    result["revisit"] = revisit_for(result)
    return result


def notice_candidate(result):
    """Intent drives parsing; PDF/office primary notices need not masquerade as HTML articles."""
    return result.get("business_type") in {"recruitment", "correction", "cancellation"} and bool(
        {"article", "attachment"}.intersection(result.get("page_roles", []))
    )


def needs_extraction(bundle):
    """Associated changes have their own review receipt; never create duplicate jobs."""
    return notice_candidate(bundle.get("assessment", {})) and not bundle.get(
        "associated_event_reviews"
    )
