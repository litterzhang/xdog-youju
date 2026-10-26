"""Extraction-code identity only; source policy and routing have separate owners."""

import hashlib
from pathlib import Path


def runtime_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "extraction_service.py",
        root / "material_contracts.py",
        root / "model_limits.py",
        root / "serialization.py",
        *sorted((root / "positions").glob("*.py")),
        *sorted((root / "recipe_runtime").glob("*.py")),
    ]
    fingerprint = hashlib.sha256()
    for path in paths:
        fingerprint.update(path.relative_to(root).as_posix().encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(path.read_bytes())
        fingerprint.update(b"\0")
    return fingerprint.hexdigest()
