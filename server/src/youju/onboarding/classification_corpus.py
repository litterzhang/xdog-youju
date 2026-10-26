"""Bounded offline corpus contracts; provenance labels are declarations, not audits."""

import hashlib
import json
import unicodedata
from collections import Counter

from .local_classifier import MAX_CORPUS_BYTES, MAX_RECORDS, MAX_TEXT_CHARS

PAGE_ROLES = ("notice_list", "article", "navigation", "attachment", "other", "unknown")
BUSINESS_TYPES = (
    "recruitment",
    "correction",
    "cancellation",
    "result",
    "guide",
    "report",
    "unrelated",
    "unknown",
)
FIELDS = (
    "id",
    "source_id",
    "template_family",
    "group",
    "text",
    "page_role",
    "business_type",
    "label_quality",
)
PROVENANCE_NOTE = "label_quality is declared provenance, not proof of human review."


def normalized_text(text):
    """Canonical content identity (Unicode compatibility, case and whitespace)."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _identity(value):
    return normalized_text(value).removeprefix("sha256:")


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def validate_records(records, *, allow_synthetic=False, require_labels=True):
    """Return copied records; accept null/unlabeled exports only when explicitly allowed.

    Silver may train but is not held-out human evaluation. Repeated content/groups
    may exist within a partition, but must not carry inconsistent labels.
    """
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError(f"Corpus requires a list of at most {MAX_RECORDS} records")
    output, identifiers, content_labels, group_labels = [], set(), {}, {}
    total = 0
    for record in records:
        if not isinstance(record, dict) or not set(FIELDS) <= record.keys():
            raise ValueError("Corpus record missing required fields")
        item = {key: record[key] for key in FIELDS}
        quality = item["label_quality"]
        if quality not in ("human", "silver", "synthetic", "unlabeled"):
            raise ValueError("Invalid label_quality")
        if quality == "synthetic" and not allow_synthetic:
            raise ValueError("Synthetic corpus requires explicit allow_synthetic")
        if quality == "unlabeled":
            if require_labels or item["page_role"] is not None or item["business_type"] is not None:
                raise ValueError("Unlabeled exports require null labels and require_labels=False")
        elif item["page_role"] not in PAGE_ROLES or item["business_type"] not in BUSINESS_TYPES:
            raise ValueError("Invalid page_role/business_type label")
        for field in ("id", "source_id", "template_family", "group"):
            value = item[field]
            if (
                field == "template_family"
                and value is None
                and not require_labels
                and item["label_quality"] == "unlabeled"
            ):
                continue
            if not isinstance(value, str) or not 0 < len(value) <= 256 or not _identity(value):
                raise ValueError(f"Invalid corpus {field}")
        text = item["text"]
        if not isinstance(text, str) or not 2 <= len(text) <= MAX_TEXT_CHARS:
            raise ValueError("Invalid corpus text size")
        if len(normalized_text(text)) < 2:
            raise ValueError("Empty corpus text")
        try:
            total += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        except (UnicodeError, TypeError, ValueError) as exc:
            raise ValueError("Invalid corpus Unicode/values") from exc
        if total > MAX_CORPUS_BYTES:
            raise ValueError("Corpus byte limit exceeded")
        identifier = _identity(item["id"])
        if identifier in identifiers:
            raise ValueError("Duplicate corpus ID")
        identifiers.add(identifier)
        for lookup, key in (
            (content_labels, normalized_text(text)),
            (group_labels, _identity(item["group"])),
        ):
            existing = lookup.setdefault(key, {})
            for field in ("page_role", "business_type"):
                label = item[field]
                if label is not None:
                    if field in existing and existing[field] != label:
                        raise ValueError("Inconsistent labels for identical content/group")
                    existing[field] = label
        output.append(item)
    return output


def corpus_manifest(records):
    """Content-free split identity for held-out leakage checks and artifact validation."""
    identities = {
        field: sorted({_digest(_identity(row[field])) for row in records})
        for field in ("id", "group")
    }
    identities["text"] = sorted({_digest(normalized_text(row["text"])) for row in records})
    identities["source_id"] = sorted({_identity(row["source_id"]) for row in records})
    identities["template_family"] = sorted(
        {_identity(row["template_family"]) for row in records if row["template_family"] is not None}
    )
    return {
        "records": len(records),
        "identities": identities,
        "label_quality_counts": dict(Counter(row["label_quality"] for row in records)),
        "label_counts": {
            field: dict(Counter(row[field] for row in records))
            for field in ("page_role", "business_type")
        },
        "fingerprint_sha256": _digest(
            sorted(
                [
                    {
                        **{
                            key: row[key] for key in ("page_role", "business_type", "label_quality")
                        },
                        **{
                            key: (_digest(_identity(row[key])) if row[key] is not None else None)
                            for key in ("id", "group", "source_id", "template_family")
                        },
                        "text": _digest(normalized_text(row["text"])),
                    }
                    for row in records
                ],
                key=lambda row: row["id"],
            )
        ),
    }


def assert_disjoint(partitions):
    """Reject any cross-partition source, template, group, text or ID overlap.

    Also validates label consistency across partitions, even if source assignments
    were forged. Synthetic and unlabeled data are accepted here only for identity
    inspection; callers must apply their own provenance policy. A null export
    template has no identity and does not establish template independence.
    """
    if not isinstance(partitions, dict):
        raise ValueError("Expected named corpus partitions")
    seen = {key: set() for key in ("source_id", "template_family", "group", "text", "id")}
    for name, rows in partitions.items():
        rows = validate_records(rows, allow_synthetic=True, require_labels=False)
        identities = corpus_manifest(rows)["identities"]
        for key in seen:
            if seen[key].intersection(identities[key]):
                raise ValueError(f"Corpus leakage across partitions ({name}): {key} overlap")
            seen[key].update(identities[key])
    # Each partition is bounded separately; pairwise conflicts can only occur with
    # overlapping content/groups, already rejected above.


def split_corpus(
    records, *, training_sources, calibration_sources, test_sources, allow_synthetic=False
):
    """Assign every row by explicit source; never randomly split captures/templates."""
    rows = validate_records(records, allow_synthetic=allow_synthetic)
    assignments, partitions = {}, {}
    for name, sources in (
        ("training", training_sources),
        ("calibration", calibration_sources),
        ("test", test_sources),
    ):
        if isinstance(sources, (str, bytes)):
            raise ValueError("Source sets must be collections, not strings")
        partitions[name] = []
        for source in sources:
            if not isinstance(source, str) or not _identity(source):
                raise ValueError("Invalid split source")
            source = _identity(source)
            if source in assignments:
                raise ValueError("Source overlap across split source sets")
            assignments[source] = name
    for row in rows:
        name = assignments.get(_identity(row["source_id"]))
        if name is None:
            raise ValueError("Every corpus source must be assigned to a partition")
        partitions[name].append(row)
    assert_disjoint(partitions)
    return partitions
