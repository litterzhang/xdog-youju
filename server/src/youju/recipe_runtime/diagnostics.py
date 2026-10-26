"""Bounded, untrusted stage receipts for successful as well as failed extraction."""

import re
from copy import deepcopy

from youju.serialization import digest, json_bytes

STAGES = frozenset({"script_draft", "detailed_draft", "merged_draft"})
STAGE_BYTES = 32_000


def stage_receipts(outputs):
    result = {}
    for name, value in outputs.items():
        if name not in STAGES:
            continue
        size = len(json_bytes(value))
        result[name] = {
            "sha256": digest(value),
            "bytes": size,
            "truncated": size > STAGE_BYTES,
            "value": deepcopy(value) if size <= STAGE_BYTES else None,
        }
    return result


def valid_stage_receipts(stages):
    if not isinstance(stages, dict) or not stages.keys() <= STAGES:
        return False
    for value in stages.values():
        if (
            not isinstance(value, dict)
            or set(value) != {"sha256", "bytes", "truncated", "value"}
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
            or type(value["bytes"]) is not int
            or value["bytes"] < 0
            or type(value["truncated"]) is not bool
        ):
            return False
        if value["truncated"]:
            if value["value"] is not None or value["bytes"] <= STAGE_BYTES:
                return False
        else:
            try:
                if (
                    not isinstance(value["value"], dict)
                    or len(json_bytes(value["value"])) != value["bytes"]
                    or value["bytes"] > STAGE_BYTES
                    or digest(value["value"]) != value["sha256"]
                ):
                    return False
            except (ValueError, TypeError, RecursionError):
                return False
    return True
