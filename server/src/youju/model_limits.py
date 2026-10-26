"""Host-owned UTF-8 input ceilings; no provider, domain or source dependencies."""

import json
from dataclasses import asdict, is_dataclass

# Decimal MB, intentionally not MiB. A caller can lower, never raise, this ceiling.
MAX_AGENT_INPUT_BYTES = 1_000_000


def input_bytes(value):
    """Measure finite JSON, including system/messages/tool schemas for SDK Contexts.

    Never stringify an unknown object: that can hide unserialized data or secrets.
    This is the logical Agent input, not a provider's HTTP framing/compression size.
    """
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
