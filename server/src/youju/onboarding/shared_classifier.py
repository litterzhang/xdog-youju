"""Offline two-head TF-IDF/logistic classifier with conservative abstention.

Threshold selection is not probability calibration or independent evaluation.
No artifact is executable, authenticated, or proof of declared label provenance.
"""

import copy
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .classification_corpus import (
    BUSINESS_TYPES,
    PAGE_ROLES,
    PROVENANCE_NOTE,
    _digest,
    _identity,
    assert_disjoint,
    corpus_manifest,
    normalized_text,
    validate_records,
)
from .local_classifier import MAX_RECORDS, LocalPageModel

FORMAT = "youju-shared-page-model-v1"
SCORE_KIND = "uncalibrated_logistic_predict_proba"
MAX_ARTIFACT_BYTES = 72 * 1024 * 1024
HEADS = {"page_role": PAGE_ROLES, "business_type": BUSINESS_TYPES}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EVALUATION_NOTE = "Threshold-selection observations, not independent accuracy."


def _encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


def _require(condition, message):
    if not condition:
        raise ValueError("Invalid shared classifier artifact: " + message)


def _precision(value):
    return type(value) in (int, float) and 0 < value <= 1


def _human_or_test(rows, allow_synthetic, partition):
    if any(
        row["label_quality"] != "human"
        and not (allow_synthetic and row["label_quality"] == "synthetic")
        for row in rows
    ):
        raise ValueError(f"{partition} requires human labels or explicit synthetic-test opt-in")


def _calibration_mode(manifest):
    if "synthetic" in manifest["label_quality_counts"]:
        return "synthetic_test"
    return "declared_human" if manifest["records"] else "empty"


def _head_records(rows, head, *, training):
    return [
        {
            "id": row["id"],
            "text": normalized_text(row["text"]),
            "label": row[head],
            "group": _identity(row["group"]),
            "origin": row["source_id"],
        }
        for row in rows
        if not training or row[head] != "unknown"
    ]


def _supports(heads):
    return {
        head: {
            label: {
                "training": model.metadata["training"]["label_counts"].get(label, 0),
                "calibration": model.metadata["calibration"]["label_counts"].get(label, 0),
                "accepted_calibration": model.metadata["class_gates"]
                .get(label, {})
                .get("accepted_groups", 0),
            }
            for label in HEADS[head]
        }
        for head, model in heads.items()
    }


def _validate_manifest(manifest):
    _require(
        isinstance(manifest, dict)
        and set(manifest)
        == {"records", "identities", "label_quality_counts", "label_counts", "fingerprint_sha256"},
        "split provenance fields",
    )
    count = manifest["records"]
    _require(type(count) is int and 0 <= count <= MAX_RECORDS, "split record count")
    identities = manifest["identities"]
    _require(
        isinstance(identities, dict)
        and set(identities) == {"id", "group", "text", "source_id", "template_family"},
        "split identity fields",
    )
    for field, values in identities.items():
        _require(isinstance(values, list) and len(values) <= count, "identity count")
        _require(
            all(isinstance(value, str) and 0 < len(value) <= 256 for value in values),
            "identity strings",
        )
        _require(values == sorted(set(values)), "unique sorted identities")
        _require(bool(values) == bool(count), "missing identities")
        if field in ("id", "group", "text"):
            _require(all(_SHA256.fullmatch(value) for value in values), "identity digest")
        else:
            _require(all(value == _identity(value) for value in values), "normalized sources")
    _require(len(identities["id"]) == count, "record IDs")
    counts = manifest["label_quality_counts"]
    _require(
        isinstance(counts, dict) and set(counts) <= {"human", "silver", "synthetic"},
        "label provenance",
    )
    _validate_counts(counts, count)
    labels = manifest["label_counts"]
    _require(isinstance(labels, dict) and set(labels) == set(HEADS), "label count heads")
    for head, allowed in HEADS.items():
        _require(
            isinstance(labels[head], dict) and set(labels[head]) <= set(allowed),
            "label count classes",
        )
        _validate_counts(labels[head], count)
    _require(
        isinstance(manifest["fingerprint_sha256"], str)
        and _SHA256.fullmatch(manifest["fingerprint_sha256"]),
        "corpus fingerprint",
    )


def _validate_counts(counts, total):
    _require(
        all(type(n) is int and 0 < n <= MAX_RECORDS for n in counts.values())
        and sum(counts.values()) == total,
        "provenance count totals",
    )


def _validate_payload(payload):
    _require(
        isinstance(payload, dict) and set(payload) == {"format", "heads", "metadata"},
        "properties (a binary local artifact is not a shared model)",
    )
    _require(payload["format"] == FORMAT, "format")
    _require(isinstance(payload["heads"], dict) and set(payload["heads"]) == set(HEADS), "heads")
    heads = {name: LocalPageModel.from_payload(value) for name, value in payload["heads"].items()}
    meta = payload["metadata"]
    _require(
        isinstance(meta, dict)
        and set(meta)
        == {
            "score_kind",
            "evaluation_note",
            "provenance_note",
            "allow_synthetic",
            "min_precision",
            "negative_precision",
            "min_support",
            "training",
            "calibration",
            "class_supports",
            "calibration_mode",
        },
        "metadata",
    )
    _require(
        meta["score_kind"] == SCORE_KIND
        and meta["evaluation_note"] == EVALUATION_NOTE
        and meta["provenance_note"] == PROVENANCE_NOTE,
        "score/provenance interpretation",
    )
    _require(type(meta["allow_synthetic"]) is bool, "synthetic policy")
    _require(
        _precision(meta["min_precision"])
        and _precision(meta["negative_precision"])
        and type(meta["min_support"]) is int
        and 1 <= meta["min_support"] <= MAX_RECORDS,
        "gate requirements",
    )
    for split in ("training", "calibration"):
        _validate_manifest(meta[split])
        qualities = set(meta[split]["label_quality_counts"])
        _require(meta["allow_synthetic"] or "synthetic" not in qualities, "synthetic provenance")
        if split == "calibration":
            _require(qualities <= {"human", "synthetic"}, "calibration provenance")
    _require(
        meta["calibration_mode"] == _calibration_mode(meta["calibration"]),
        "calibration mode/provenance",
    )
    for field in meta["training"]["identities"]:
        _require(
            not set(meta["training"]["identities"][field]).intersection(
                meta["calibration"]["identities"][field]
            ),
            f"{field} provenance leakage",
        )
    for head, model in heads.items():
        value = payload["heads"][head]
        classes = set(value["classes"])
        expected = set(meta["training"]["label_counts"][head]) - {"unknown"}
        _require(
            classes == expected and classes <= set(HEADS[head]) - {"unknown"}, f"{head} classes"
        )
        hm = model.metadata
        precision = (
            meta["min_precision"]
            if head == "page_role"
            else max(meta["min_precision"], meta["negative_precision"])
        )
        _require(
            hm["min_precision"] == precision and hm["min_support"] == meta["min_support"],
            "head gate requirements",
        )
        for split in ("training", "calibration"):
            expected_records = meta[split]["records"]
            if split == "training":
                expected_records -= meta[split]["label_counts"][head].get("unknown", 0)
            _require(hm[split]["records"] == expected_records, "head provenance count")
            for label, n in hm[split]["label_counts"].items():
                _require(
                    n <= meta[split]["label_counts"][head].get(label, 0),
                    "head label provenance counts",
                )
    _require(meta["class_supports"] == _supports(heads), "class support report")
    return heads


def _object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Duplicate JSON property")
        output[key] = value
    return output


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


class SharedPageModel:
    """Two independently gated heads; abstain unless both heads accept.

    The aggregate confidence/margin are minima, not a joint probability. Missing
    classes/support disable gates; observed support never promises accuracy.
    """

    def __init__(self, payload, heads):
        self._payload = payload
        self._heads = heads
        self._key = _digest(payload)

    @classmethod
    def fit(
        cls,
        training,
        calibration,
        *,
        min_precision=0.98,
        negative_precision=0.995,
        min_support=5,
        allow_synthetic=False,
    ):
        if not _precision(min_precision) or not _precision(negative_precision):
            raise ValueError("Invalid precision requirements")
        if type(allow_synthetic) is not bool:
            raise ValueError("allow_synthetic must be boolean")
        train = validate_records(training, allow_synthetic=allow_synthetic)
        cal = validate_records(calibration, allow_synthetic=allow_synthetic)
        _human_or_test(cal, allow_synthetic, "Calibration")
        assert_disjoint({"training": train, "calibration": cal})
        heads = {
            head: LocalPageModel.fit(
                _head_records(train, head, training=True),
                _head_records(cal, head, training=False),
                min_precision=(
                    min_precision if head == "page_role" else max(min_precision, negative_precision)
                ),
                min_support=min_support,
            )
            for head in HEADS
        }
        payload = {
            "format": FORMAT,
            "heads": {head: model.export_payload() for head, model in heads.items()},
            "metadata": {
                "score_kind": SCORE_KIND,
                "evaluation_note": EVALUATION_NOTE,
                "provenance_note": PROVENANCE_NOTE,
                "allow_synthetic": allow_synthetic,
                "min_precision": min_precision,
                "negative_precision": negative_precision,
                "min_support": min_support,
                "training": corpus_manifest(train),
                "calibration": corpus_manifest(cal),
                "calibration_mode": _calibration_mode(corpus_manifest(cal)),
                "class_supports": _supports(heads),
            },
        }
        _validate_payload(payload)
        if len(_encoded({"sha256": _digest(payload), "model": payload})) > MAX_ARTIFACT_BYTES:
            raise ValueError("Shared classifier exceeds artifact byte limit")
        return cls(payload, heads)

    @property
    def key(self):
        return self._key

    @property
    def metadata(self):
        return copy.deepcopy(self._payload["metadata"])

    def predict(self, text):
        if not isinstance(text, str) or len(text) > 32000:
            return None
        text = normalized_text(text)
        scores = {head: model.predict(text) for head, model in self._heads.items()}
        if any(
            value is None or value["label"] not in set(HEADS[head]) - {"unknown"}
            for head, value in scores.items()
        ):
            return None
        return {
            **{head: score["label"] for head, score in scores.items()},
            "confidence": min(score["confidence"] for score in scores.values()),
            "margin": min(score["margin"] for score in scores.values()),
            "score_kind": SCORE_KIND,
            **{
                f"{head}_{key}": score[key]
                for head, score in scores.items()
                for key in ("confidence", "margin")
            },
        }

    def save(self, path):
        encoded = _encoded({"sha256": self.key, "model": self._payload})
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError("Shared classifier exceeds artifact byte limit")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".shared-model-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        path = Path(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Expected bounded regular shared JSON artifact")
        with path.open("rb") as handle:
            encoded = handle.read(MAX_ARTIFACT_BYTES + 1)
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError("Shared classifier exceeds artifact byte limit")
        try:
            envelope = json.loads(
                encoded, object_pairs_hook=_object, parse_constant=_reject_constant
            )
            _require(
                isinstance(envelope, dict) and set(envelope) == {"sha256", "model"}, "envelope"
            )
            _require(
                isinstance(envelope["sha256"], str)
                and _SHA256.fullmatch(envelope["sha256"])
                and envelope["sha256"] == _digest(envelope["model"]),
                "content digest",
            )
            payload = envelope["model"]
            heads = _validate_payload(payload)
        except (RecursionError, UnicodeError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid shared classifier JSON artifact") from exc
        return cls(payload, heads)


def load_runtime_model(path):
    """Configured services cannot silently treat smoke/synthetic artifacts as reviewed models."""
    model = SharedPageModel.load(path)
    metadata = model.metadata
    if (
        metadata["calibration_mode"] != "declared_human"
        or "synthetic" in metadata["training"]["label_quality_counts"]
    ):
        raise ValueError(
            "Runtime shared classifier needs declared-human calibration and "
            "non-synthetic training; use classifier-evaluate for smoke artifacts"
        )
    return model


def _unique_records(rows):
    """One representative per connected group/content component, including alias bridges."""
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        current, trail = key, []
        while parents[current] != current:
            trail.append(current)
            current = parents[current]
        for node in trail:
            parents[node] = current
        return current

    for row in rows:
        group, text = ("group", _identity(row["group"])), ("text", normalized_text(row["text"]))
        a, b = root(group), root(text)
        if a != b:
            parents[b] = a
    unique = {}
    for row in sorted(rows, key=lambda item: _identity(item["id"])):
        unique.setdefault(root(("group", _identity(row["group"]))), row)
    return list(unique.values())


def _metrics(rows, predictions):
    total = len(rows)
    accepted = sum(value is not None for value in predictions)
    correct = sum(
        value is not None and all(value[head] == row[head] for head in HEADS)
        for row, value in zip(rows, predictions, strict=True)
    )
    heads = {}
    for head, labels in HEADS.items():
        confusion = {label: dict.fromkeys(labels, 0) for label in labels}
        for row, value in zip(rows, predictions, strict=True):
            confusion[row[head]][value[head] if value is not None else "unknown"] += 1
        label_metrics = {}
        for label in labels:
            support = sum(confusion[label].values())
            predicted = sum(value is not None and value[head] == label for value in predictions)
            tp = sum(
                row[head] == label and value is not None and value[head] == label
                for row, value in zip(rows, predictions, strict=True)
            )
            label_metrics[label] = {
                "support": support,
                "predicted": predicted,
                "true_positive": tp,
                "precision": tp / predicted if predicted else None,
                "recall": tp / support if support else None,
            }
        head_correct = sum(
            value is not None and value[head] == row[head]
            for row, value in zip(rows, predictions, strict=True)
        )
        heads[head] = {
            "per_label": label_metrics,
            "confusion": confusion,
            "accuracy_over_total": head_correct / total if total else None,
            "accuracy_over_accepted": head_correct / accepted if accepted else None,
        }
    recruitment_support = sum(row["business_type"] == "recruitment" for row in rows)
    erroneous_exclusions = sum(
        row["business_type"] == "recruitment"
        and value is not None
        and value["business_type"] != "recruitment"
        for row, value in zip(rows, predictions, strict=True)
    )
    return {
        "total": total,
        "accepted": accepted,
        "abstentions": total - accepted,
        "coverage": accepted / total if total else None,
        "correct_accepted": correct,
        "accuracy_over_total": correct / total if total else None,
        "accuracy_over_accepted": correct / accepted if accepted else None,
        "heads": heads,
        "recruitment_support": recruitment_support,
        "recruitment_erroneous_exclusions": erroneous_exclusions,
        "recruitment_erroneous_exclusion_rate": (
            erroneous_exclusions / recruitment_support if recruitment_support else None
        ),
    }


def evaluate_model(model, test, *, allow_synthetic=False, decision_fn=None):
    """Evaluate unseen declared-human (or opt-in synthetic) records, never silver gold.

    Accuracy-over-total treats abstentions as not correct. Confusion maps all
    abstentions to unknown; an unknown/unknown cell is not an accepted decision.
    Evaluation deduplicates group/content components, not a claim of independent pages.
    """
    if not isinstance(model, SharedPageModel):
        raise ValueError("Evaluation requires a shared model, not a binary local model")
    rows = validate_records(test, allow_synthetic=allow_synthetic)
    _human_or_test(rows, allow_synthetic, "Test")
    manifest, metadata = corpus_manifest(rows), model.metadata
    for split in ("training", "calibration"):
        for field, values in manifest["identities"].items():
            if set(values).intersection(metadata[split]["identities"][field]):
                raise ValueError(f"Test leakage against {split}: {field} overlap")
    original = rows
    rows = _unique_records(original)
    decide = decision_fn or (lambda row: model.predict(row["text"]))
    predictions = [decide(row) for row in rows]
    per_source = {}
    for source in manifest["identities"]["source_id"]:
        source_rows = _unique_records(
            [row for row in original if _identity(row["source_id"]) == source]
        )
        per_source[source] = _metrics(source_rows, [decide(row) for row in source_rows])
    return {
        **_metrics(rows, predictions),
        "per_source": per_source,
        "raw_test_records": len(original),
        "evaluation_unit": "deduplicated-group-content-components",
        "independence_note": "Deduplication does not establish statistical independence; "
        "per-source metrics deduplicate within source and are not additive.",
        "model_key": model.key,
        "score_kind": SCORE_KIND,
        "provenance_note": PROVENANCE_NOTE,
        "test": manifest,
        "evaluation_kind": (
            "synthetic_test"
            if "synthetic" in manifest["label_quality_counts"]
            else "declared_human_labels"
        ),
        "confusion_note": "Abstentions map to unknown; they are not exclusions or accepted hits.",
        "class_supports": metadata["class_supports"],
        "calibration_mode": metadata["calibration_mode"],
        "promotion_qualified": False,
        "promotion_note": "Offline metrics alone do not qualify a model for promotion; "
        "synthetic/silver labels are never gold evaluation evidence.",
    }
