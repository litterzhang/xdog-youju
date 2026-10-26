"""Position facts/type/invariant validation only; never re-extract an answer oracle."""

from .contracts import CONTRACT
from .facts import PositionFacts


def validate_output(bundle, result):
    """Validate normalized core keys, ignoring caller-owned execution/routing data.

    bundle is retained in the signature for callers, but cannot adjudicate facts,
    acquisition completeness, notice validity, eligibility or extractor equality.
    """
    del bundle
    errors = []

    def error(message, path=""):
        errors.append({"code": "POSITION_V3_SCHEMA", "message": str(message), "path": path})

    if not isinstance(result, dict):
        error("Result must be an object")
        return errors
    required = {"schema_version", "status", "positions", "errors", "coverage"}
    if not required <= result.keys() or result.get("schema_version") != CONTRACT:
        error("Unsupported position-facts-v3 envelope")
        return errors
    status = result["status"]
    if not isinstance(status, str) or status not in {
        "success",
        "partial",
        "empty",
        "review",
        "failed",
        "cancelled",
    }:
        error("Unsupported extraction status", "status")
    positions = result["positions"]
    if not isinstance(positions, list) or len(positions) > 1000:
        error("Positions must be a bounded array", "positions")
        positions = []
    for index, raw in enumerate(positions):
        try:
            position = PositionFacts.from_dict(raw)
            if position.fields["title"].value is None:
                raise ValueError("A position requires a known title")
        except (ValueError, TypeError, KeyError) as exc:
            error(exc, f"positions[{index}]")
    issues = result["errors"]
    if not isinstance(issues, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("code"), str)
        or not isinstance(item.get("message"), str)
        for item in issues
    ):
        error("Errors require code and message", "errors")
    coverage = result["coverage"]
    if not isinstance(coverage, dict) or any(
        type(coverage.get(key)) is not bool for key in ("supported", "complete", "needs_detailed")
    ):
        error("Coverage requires supported/complete/needs_detailed booleans", "coverage")
    if status == "success" and (
        not positions
        or issues
        or not isinstance(coverage, dict)
        or coverage.get("complete") is not True
    ):
        error("Success requires positions, no errors, and declared complete coverage", "status")
    if status == "empty" and (
        positions
        or issues
        or coverage.get("complete") is not True
        or coverage.get("supported") is not True
        or coverage.get("needs_detailed") is not False
        or not isinstance(coverage.get("empty_capture"), dict)
    ):
        error("Empty means an explicit, complete zero-row capture observation", "status")
    if status in {"failed", "cancelled"} and positions:
        error("Failed/cancelled extraction cannot contain positions", "status")
    return errors
