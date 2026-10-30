"""Explicit draft corrections bound to old values and frozen material locations.

These checks establish identity/location/contract, NOT semantic truth. A model's
reason remains model provenance. No absent row is implicitly withdrawn.
"""

from copy import deepcopy

import fastjsonschema

CORRECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "operation",
        "position_key",
        "field",
        "before",
        "after",
        "bundle_id",
        "reason",
        "evidence",
    ],
    "properties": {
        "operation": {"type": "string", "enum": ["replace_field", "withdraw_position"]},
        "position_key": {"type": "string", "minLength": 1, "maxLength": 128},
        "field": {"type": ["string", "null"]},
        "before": {"type": "object"},
        "after": {"type": ["object", "null"]},
        "bundle_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
        "evidence": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["version_id", "pointer", "start", "end", "quote"],
                "properties": {
                    "version_id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "pointer": {
                        "type": "string",
                        "pattern": r"^/(blocks/[0-9]+/text|tables/[0-9]+/rows/[0-9]+/[0-9]+)$",
                    },
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
            },
        },
    },
}


_validate_schema = fastjsonschema.compile(CORRECTION_SCHEMA, use_default=False)


def validate_corrections(proposals):
    from .facts import POSITION_FIELDS, PositionFacts, TaggedField

    if not isinstance(proposals, list) or len(proposals) > 100:
        raise ValueError("Corrections must be a bounded list")
    for proposal in proposals:
        try:
            _validate_schema(proposal)
        except fastjsonschema.JsonSchemaException as exc:
            raise ValueError("Invalid explicit correction contract") from exc
        if proposal["operation"] == "replace_field":
            if proposal["field"] not in set(POSITION_FIELDS) - {
                "deadline_on",
                "application_start_on",
                "application_dates",
            }:
                raise ValueError("Correction requires a non-projected business field")
            TaggedField.from_dict(proposal["before"])
            replacement = TaggedField.from_dict(proposal["after"])
            fields = {name: TaggedField() for name in POSITION_FIELDS}
            fields[proposal["field"]] = replacement
            PositionFacts(fields)
        elif proposal["field"] is not None or proposal["after"] is not None:
            raise ValueError("Withdrawal needs a previous position and no replacement")


def _evidence_matches(bundle, proposal):
    if not bundle or proposal["bundle_id"] != bundle.get("id"):
        return False
    documents = [bundle.get("page", {}), *(bundle.get("attachments") or [])]
    for ref in proposal["evidence"]:
        docs = [doc for doc in documents if doc.get("version_id") == ref["version_id"]]
        if len(docs) != 1:
            return False
        text = docs[0]
        try:
            for token in ref["pointer"].split("/")[1:]:
                text = text[int(token)] if isinstance(text, list) else text[token]
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        if not isinstance(text, str) or not 0 <= ref["start"] < ref["end"] <= len(text):
            return False
        if text[ref["start"] : ref["end"]] != ref["quote"]:
            return False
    return True


def apply_corrections(base, incoming, bundle, receipts):
    """Apply a narrow pre-merge patch; deny overlapping operations per position.

    Invalid proposals preserve the entire old row; a rejected correction cannot
    sneak through as ordinary refinement. Unrelated rows still merge normally.
    """
    proposals = incoming.pop("corrections", [])
    originals = {p["key"]: p for p in deepcopy(base["positions"])}
    changed = {p["key"]: p for p in incoming["positions"]}
    common = base["notice_facts"].get("fields", {})
    keys = [p["position_key"] for p in proposals]
    for proposal in proposals:
        key = proposal["position_key"]
        old, new = originals.get(key), changed.get(key, {})
        reason = None
        if old is None or keys.count(key) != 1:
            reason = "unknown_or_conflicting_position_key"
        elif not _evidence_matches(bundle, proposal):
            reason = "material_version_or_literal_reference_mismatch"
        elif proposal["operation"] == "withdraw_position":
            if proposal["before"] != old or key in changed:
                reason = "withdrawal_snapshot_or_output_conflict"
        else:
            name = proposal["field"]
            before = {**common, **old.get("fields", {})}.get(name)
            after = new.get("fields", {}).get(name, proposal["after"])
            if before != proposal["before"] or after != proposal["after"]:
                reason = "field_snapshot_or_output_conflict"
            elif name == "title" and not proposal["after"].get("value"):
                reason = "replacement_title_is_unusable"
        receipts.append(
            {
                **deepcopy(proposal),
                "status": "rejected" if reason else "applied",
                "basis": "model_proposal_literal_reference_checked",
                "rejection_reason": reason,
            }
        )
        if reason:
            incoming["positions"] = [p for p in incoming["positions"] if p["key"] != key]
            base["issues"].append(
                {
                    "code": "DRAFT_CORRECTION_REJECTED",
                    "message": "Explicit correction requires review",
                    "position_key": key,
                    "reason": reason,
                    "path": f"positions[{list(originals).index(key)}]" if old else "positions",
                }
            )
        elif proposal["operation"] == "withdraw_position":
            base["positions"] = [p for p in base["positions"] if p["key"] != key]
        else:
            target = next(p for p in base["positions"] if p["key"] == key)
            target.setdefault("fields", {})[proposal["field"]] = deepcopy(proposal["after"])
    indices = {p["key"]: i for i, p in enumerate(base["positions"])}
    for issue in base["issues"]:
        if issue.get("code") == "DRAFT_CORRECTION_REJECTED":
            index = indices.get(issue.get("position_key"))
            issue["path"] = f"positions[{index}]" if index is not None else "positions"
    return base, incoming


def valid_correction_receipts(receipts):
    if not isinstance(receipts, list) or len(receipts) > 100:
        return False
    try:
        for receipt in receipts:
            if not isinstance(receipt, dict) or set(receipt) != set(
                CORRECTION_SCHEMA["required"]
            ) | {"status", "basis", "rejection_reason"}:
                return False
            if receipt["basis"] != "model_proposal_literal_reference_checked":
                return False
            if receipt["status"] == "applied":
                if receipt["rejection_reason"] is not None:
                    return False
            elif receipt["status"] != "rejected" or not isinstance(
                receipt["rejection_reason"], str
            ):
                return False
            validate_corrections([{k: receipt[k] for k in CORRECTION_SCHEMA["required"]}])
    except (ValueError, KeyError, TypeError):
        return False
    return True
