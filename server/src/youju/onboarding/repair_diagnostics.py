"""Separate attributable adapter defects from material/provider/uncertainty issues."""

# Recipe artifacts include native extraction prompts, not only Python scripts.
import re

ADAPTER_CODES = {"SCRIPT_PARSE_FAILED", "DRAFT_INVALID", "OUTPUT_SCHEMA"}
# Shared normalizer failures are not an invitation to patch source scripts.
DOMAIN_CODES = {
    "RESULT_INVALID",
    "POSITION_V3_SCHEMA",
    "APPLICATION_DATE_CONFLICT",
    "EMPLOYMENT_CONFLICT",
    "CONDITION_CONFLICT",
    "REFINEMENT_IDENTITY_CONFLICT",
}

SEMANTIC_DEFECTS = {
    "EXPECTED_FIELD",
    "EXPECTED_CONDITIONS",
    "EXPECTED_POSITION_SET",
    "EXPECTED_CORRESPONDENCE",
}


def valid_failure_receipt(value):
    return bool(
        isinstance(value, dict)
        and set(value) == {"code", "origin", "evidence_digest"}
        and isinstance(value["code"], str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value["code"])
        and isinstance(value["origin"], str)
        and 0 < len(value["origin"]) <= 80
        and isinstance(value["evidence_digest"], str)
        and re.fullmatch(r"[a-f0-9]{64}", value["evidence_digest"])
    )


def diagnose_row(session, row):
    """Never trust a model's self-reported 'semantic' code as independent confirmation."""
    from .semantic_labels import receipt

    check = receipt(session, row)
    codes = {e.get("failure_code", e.get("code")) for e in row.errors}
    confirmed = (
        check is not None
        and check["status"] == "failed"
        and check.get("basis") in {"human-confirmed", "independent-rule"}
        and bool(check["issues"])
        and all(e["code"] in SEMANTIC_DEFECTS for e in check["issues"])
    )
    if (
        check is not None
        and check["status"] == "passed"
        and check.get("fully_specified")
        and (
            codes == {"RESULT_INVALID"}
            or (
                {e.get("failure_code") for e in row.errors if e.get("failure_code")}
                == {"RESULT_INVALID"}
                and codes <= {"RESULT_INVALID", "POSITION_V3_SCHEMA", "RUNTIME_RESULT_INVALID"}
            )
        )
    ):
        return {
            "repairable": False,
            "category": "host_validation",
            "basis": "original_rejected_positions_pass_independent_exhaustive_case",
        }
    # The audit owner mints this receipt only from a bound failure_record. Source
    # draft issues cannot mint it through a code/failure_code string.
    failure = row.result.get("execution", {}).get("failure")
    trusted_failure = valid_failure_receipt(failure)
    if trusted_failure:
        vetoes = {
            code
            for code in codes
            if code in DOMAIN_CODES
            or "CLASSIFICATION" in (code or "")
            or "ASSOCIATION" in (code or "")
            or "MATERIAL" in (code or "")
        }
        codes = {failure["code"]} | vetoes
    allowed = (ADAPTER_CODES if trusted_failure else set()) | (
        {"SEMANTIC_EXPECTATION"} if confirmed else set()
    )
    # Non-adapter errors are not fixed by rewriting source scripts, even when a
    # separate semantic mismatch happens to be present on the same notice.
    if codes and codes <= allowed:
        return {
            "repairable": True,
            "category": "semantic" if confirmed else "adapter",
            "basis": check.get("basis") if confirmed else "runtime-diagnostic",
        }
    if confirmed and not codes:
        return {"repairable": True, "category": "semantic", "basis": check["basis"]}
    if codes & {"EMPTY_CAPTURE_NEEDS_RENDER", "EMPTY_CAPTURE_UNVERIFIED"}:
        return {
            "repairable": False,
            "category": "acquisition",
            "basis": "empty_capture_needs_acquisition_check",
        }
    category = "position_domain" if codes & DOMAIN_CODES else "unresolved"
    if "SCRIPT_RUNTIME_UNAVAILABLE" in codes:
        return {
            "repairable": False,
            "category": "execution_infrastructure",
            "basis": "diagnosis_required",
        }
    if any("MODEL" in (code or "") or "BUDGET" in (code or "") for code in codes):
        category = "provider_or_budget"
    elif any("CLASSIFICATION" in (code or "") or "ASSOCIATION" in (code or "") for code in codes):
        category = "classification_or_association"
    elif any("MATERIAL" in (code or "") or "ATTACHMENT" in (code or "") for code in codes):
        category = "material"
    return {"repairable": False, "category": category, "basis": "diagnosis_required"}


def routing_for(bundle, now_iso):
    """A fallible cost policy can prioritize repairs, never alter job facts."""
    from datetime import datetime

    from youju.notices.routing import decide_route

    return decide_route(
        bundle, bundle.get("assessment"), now=datetime.fromisoformat(now_iso)
    ).as_dict()
