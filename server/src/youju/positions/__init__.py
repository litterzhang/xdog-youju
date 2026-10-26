"""Source-neutral extraction drafts and the sole structured position contract."""

from .drafts import (
    DRAFT_INSTRUCTIONS,
    DRAFT_SCHEMA,
    ExtractionDraft,
    merge_drafts,
    validate_draft,
)
from .facts import Condition, FieldTag, PositionFacts, TaggedField
from .normalization import normalize_extraction

__all__ = [
    "Condition",
    "FieldTag",
    "PositionFacts",
    "TaggedField",
    "ExtractionDraft",
    "DRAFT_SCHEMA",
    "DRAFT_INSTRUCTIONS",
    "validate_draft",
    "merge_drafts",
    "normalize_extraction",
]
