"""Bounded rejected-output evidence, independent of publishable extraction rows.

Records contain untrusted data, never instructions or executable adapters. Missing
or over-limit output is explicit; an empty host failure envelope is not evidence
that a model/parser returned no positions.
"""

import hashlib
import json
from copy import deepcopy

from youju.recipe_runtime.versioning import runtime_fingerprint as toolkit_key
from youju.serialization import digest, json_bytes

FAILURE_FORMAT = "youju-recipe-failure-v1"
MAX_FAILURE_BYTES = 20 * 1024 * 1024
MAX_COMPONENT_BYTES = 8 * 1024 * 1024
PAGE_CHARS = 12000
PAGE_BYTES = 64000


def bounded_value(value, *, limit=MAX_COMPONENT_BYTES):
    if value is None:
        return {
            "available": False,
            "reason": "no_rejected_output",
            "value": None,
            "truncated": False,
            "sha256": None,
            "bytes": 0,
        }
    try:
        body = json_bytes(value)
    except (TypeError, ValueError, RecursionError):
        return {
            "available": False,
            "reason": "non_finite_or_non_json_output",
            "value": None,
            "truncated": False,
            "sha256": None,
            "bytes": None,
        }
    metadata = {"available": True, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
    if len(body) <= limit:
        return {**metadata, "truncated": False, "value": deepcopy(value)}
    return {
        **metadata,
        "truncated": True,
        "value": None,
        "reason": "output_exceeds_diagnostic_component_limit",
        "untrusted_preview": body[:PAGE_BYTES].decode("utf-8", errors="replace"),
    }


def failure_record(bundle, candidate_key, now_iso, error, *, bindings=None):
    output = bounded_value(error.rejected_result)
    errors = bounded_value(error.errors)
    supplied = bindings or {}
    stable_issues = [
        {
            key: value
            for key, value in issue.items()
            if key
            in {"code", "message", "path", "detail", "actual", "expected", "scope", "semantic_code"}
        }
        for issue in error.errors
    ]
    try:
        issue_digest = digest(stable_issues)
    except (TypeError, ValueError):
        issue_digest = errors["sha256"]
    record = {
        "format": FAILURE_FORMAT,
        "progress_issue_digest": issue_digest,
        "rejected_origin": getattr(error, "rejected_origin", "host_validation"),
        "bindings": {
            **supplied,
            "candidate_digest": candidate_key,
            "case_id": bundle.get("id", bundle.get("resource_id")),
            "case_version": digest(bundle),
            "source_id": bundle.get("source_id"),
            "resource_id": bundle.get("resource_id"),
            "material_versions": [
                doc.get("version_id")
                for doc in [bundle.get("page", {}), *bundle.get("attachments", [])]
            ],
            "frozen_clock": now_iso,
            "checker_version": supplied.get("checker_version", toolkit_key()),
        },
        "error_code": error.code,
        "issues": errors,
        "rejected_output": output,
        "partial_output": bounded_value(getattr(error, "partial_result", None), limit=1_000_000),
        "stage_outputs": {
            name: bounded_value(value, limit=256_000)
            for name, value in getattr(error, "stage_outputs", {}).items()
            if name in {"script_draft", "detailed_draft", "merged_draft"}
        },
        "routing": deepcopy(error.routing),
        "execution": deepcopy(error.execution),
        "stage": stage_for(error.code),
        "stage_basis": "host_error_code",
        "untrusted": True,
    }
    if len(json_bytes(record)) > MAX_FAILURE_BYTES:
        raise ValueError("Rejected diagnostic record exceeds its explicit byte limit")
    return record


def stage_for(code):
    if code.startswith("SEMANTIC"):
        return "semantic"
    if code.startswith(("PERIOD", "VALIDITY", "RECIPE_CLOCK")):
        return "validity"
    if code.startswith(("MODEL", "OUTPUT_SCHEMA")):
        return "model"
    if code.startswith(("SCRIPT", "COARSE")):
        return "script"
    if code.startswith(("RECIPE_INVALID", "WORKFLOW")):
        return "workflow"
    return "extraction_validation"


def failure_summary(record):
    output = record["rejected_output"]
    value = output.get("value") or {}
    positions = value.get("positions") if isinstance(value, dict) else None
    return {
        "case_id": record["bindings"]["case_id"],
        "error_code": record["error_code"],
        "stage": record["stage"],
        "issue_digest": record["progress_issue_digest"],
        "rejected_origin": record.get("rejected_origin", "unknown"),
        "rejected_output_available": output["available"],
        "rejected_output_truncated": output["truncated"],
        "rejected_output_digest": output["sha256"],
        "rejected_position_count": len(positions) if isinstance(positions, list) else None,
    }


def failed_result(error, *, failure_ref=None):
    """A nonpublishable envelope, never a replacement for the original output evidence."""
    from youju.positions.facts import PositionFacts

    original = error.rejected_result or {}
    positions = original.get("positions", []) if isinstance(original, dict) else []
    try:
        if not isinstance(positions, list):
            raise ValueError("Rejected positions are not an array")
        for position in positions:
            PositionFacts.from_dict(position)
    except (TypeError, ValueError):
        positions = []
    details = [
        {
            **item,
            "failure_code": error.code,
            **({"failure_ref": failure_ref} if failure_ref else {}),
            "rejected_output_available": error.rejected_result is not None,
            "host_failure_envelope": True,
        }
        for item in error.errors
    ]
    partial = getattr(error, "partial_result", None)
    if partial is not None:
        from youju.positions.extraction import validate_output

        if validate_output({}, partial) or not partial.get("positions"):
            raise ValueError("Partial failure facts must satisfy the canonical position contract")
        result = deepcopy(partial)
        result["status"] = "partial"
        result["coverage"].update(complete=False, needs_detailed=True)
        for issue in details:
            if issue not in result["errors"]:
                result["errors"].append(issue)
        return result, details
    coverage = original.get("coverage") if isinstance(original, dict) else None
    if not isinstance(coverage, dict) or any(
        type(coverage.get(key)) is not bool for key in ("supported", "complete", "needs_detailed")
    ):
        # Missing rejected output is an execution observation, not a second
        # malformed model answer. Keep a valid host failure envelope.
        coverage = {"supported": False, "complete": False, "needs_detailed": True}
    result = {
        "schema_version": "position-facts-v3",
        "status": "failed",
        "positions": deepcopy(positions),
        "errors": details,
        "coverage": deepcopy(coverage),
        "routing": deepcopy(error.routing) or {},
        "execution": deepcopy(error.execution)
        or {
            "path": "unknown",
            "model_used": None,
            "runtime_contract": "recipe-runtime-v3",
            "parser": "unknown",
            "script_issues": [],
        },
    }
    return result, details


def page_record(record, *, section="issues", cursor=0, issue_index=0, limit=20):
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Invalid failure page")
    common = {
        "error_code": record["error_code"],
        "bindings": record["bindings"],
        "stage": record["stage"],
        "rejected_origin": record.get("rejected_origin", "unknown"),
        "untrusted": True,
    }
    if section == "issues":
        component = record["issues"]
        issues = component.get("value") if not component["truncated"] else None
        if not isinstance(issues, list):
            return {**common, "issues": [], "component": component, "next_cursor": None}
        if cursor > len(issues):
            raise ValueError("Invalid issue cursor")
        selected = []
        for number in range(cursor, min(cursor + limit, len(issues))):
            issue = issues[number]
            if len(json_bytes(issue)) > PAGE_BYTES:
                issue = {
                    "code": issue.get("code", "OVERSIZED_ISSUE"),
                    "issue_index": number,
                    "message": "Use section=issue and this issue_index for full paginated data",
                }
            if selected and len(json_bytes([*selected, issue])) > PAGE_BYTES:
                break
            selected.append(deepcopy(issue))
        end = cursor + len(selected)
        return {
            **common,
            "issues": selected,
            "total": len(issues),
            "next_cursor": end if end < len(issues) else None,
        }
    if section == "issue":
        issues = record["issues"].get("value")
        if (
            not isinstance(issues, list)
            or type(issue_index) is not int
            or not 0 <= issue_index < len(issues)
        ):
            raise ValueError("Unknown diagnostic issue")
        text = json.dumps(issues[issue_index], ensure_ascii=False)
        metadata = {"issue_index": issue_index}
    elif section in {
        "rejected_output",
        "partial_output",
        "script_draft",
        "detailed_draft",
        "merged_draft",
    }:
        component = (
            record.get(section, bounded_value(None))
            if section in {"rejected_output", "partial_output"}
            else record.get("stage_outputs", {}).get(section, bounded_value(None))
        )
        text = (
            json.dumps(component.get("value"), ensure_ascii=False)
            if not component["truncated"]
            else component["untrusted_preview"]
        )
        metadata = {
            key: value
            for key, value in component.items()
            if key not in {"value", "untrusted_preview"}
        }
    else:
        raise ValueError("Unknown failure section")
    if cursor > len(text):
        raise ValueError("Invalid text cursor")
    end = min(cursor + PAGE_CHARS, len(text))
    return {
        **common,
        **metadata,
        "untrusted_text": text[cursor:end],
        "text_length": len(text),
        "next_cursor": end if end < len(text) else None,
    }


def summarize_issues(issues, failures):
    """Keep small host/tool summaries; exact rejected issues remain in failure records."""
    selected = []
    for issue in issues:
        compact = {
            key: (value[:2000] if isinstance(value, str) else value)
            for key, value in issue.items()
            if key
            in {
                "code",
                "message",
                "path",
                "phase",
                "scope",
                "sample_index",
                "resource_id",
                "semantic_code",
                "expected_basis",
                "failure_code",
                "line",
                "offset",
                "detail",
                "field",
            }
        }
        if len(json_bytes([*selected, compact])) > PAGE_BYTES:
            break
        selected.append(compact)
    if len(selected) < len(issues):
        selected.append(
            {
                "code": "ADDITIONAL_DIAGNOSTICS",
                "message": "Use the immutable failure records for all original issues",
                "omitted_count": len(issues) - len(selected),
                "failure_ids": [f["failure_id"] for f in failures if f.get("failure_id")],
            }
        )
    return selected
