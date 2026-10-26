"""Small CPU-only page classifier with held-out, class-specific abstention gates.

The calibration split selects thresholds; its observed precision is NOT an
independent accuracy estimate or calibrated probability. The host owns semantic
validation and a separate final evaluation set. Artifacts contain JSON numbers,
strings and provenance digests only; loading never unpickles or executes code.
"""

import copy
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

FORMAT = "youju-local-page-model-v1"
MAX_FEATURES = 20_000
MAX_CLASSES = 32
MAX_RECORDS = 5_000
MAX_TEXT_CHARS = 32_000
MAX_CORPUS_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VECTORIZER = {
    "analyzer": "char",
    "ngram_range": [2, 4],
    "lowercase": True,
    "norm": "l2",
    "use_idf": True,
    "smooth_idf": True,
    "sublinear_tf": False,
    "max_features": MAX_FEATURES,
}


def _encoded(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _bounded_string(value, limit=256):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _number(value, low, high):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and low <= value <= high
    except OverflowError:
        return False


def _prepare(records, name):
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError(f"{name}: expected at most {MAX_RECORDS} records")
    normalized, ids, groups, texts, origins = [], set(), set(), set(), Counter()
    group_labels, content_labels, total_bytes = {}, {}, 0
    for record in records:
        if not isinstance(record, dict) or not {"id", "text", "label", "group"} <= set(record):
            raise ValueError(f"{name}: id, text, label and group are required")
        if not all(_bounded_string(record[key]) for key in ("id", "label", "group")):
            raise ValueError(f"{name}: invalid identifier, label or group")
        text = record["text"]
        if (
            not isinstance(text, str)
            or not 2 <= len(text) <= MAX_TEXT_CHARS
            or len(text.strip()) < 2
        ):
            raise ValueError(f"{name}: empty or oversized text")
        try:
            total_bytes += len(text.encode("utf-8"))
        except UnicodeError as exc:
            raise ValueError(f"{name}: invalid Unicode text") from exc
        if total_bytes > MAX_CORPUS_BYTES:
            raise ValueError(f"{name}: corpus byte limit exceeded")
        origin = record.get("origin")
        if origin is not None and not _bounded_string(origin, 1024):
            raise ValueError(f"{name}: invalid provenance origin")
        identifier, label = record["id"], record["label"]
        group = record["group"].strip().lower().removeprefix("sha256:")
        if not group:
            raise ValueError(f"{name}: empty normalized group")
        # Also catch copied content with incorrectly reassigned group IDs.
        text_hash = _digest(" ".join(text.lower().split()))
        if identifier in ids:
            raise ValueError(f"{name}: duplicate record ID")
        for key, lookup in ((group, group_labels), (text_hash, content_labels)):
            if key in lookup and lookup[key] != label:
                raise ValueError(f"{name}: conflicting labels for the same content group")
            lookup[key] = label
        ids.add(identifier)
        groups.add(group)
        texts.add(text_hash)
        origin_hash = "unspecified" if origin is None else _digest(origin)
        origins[origin_hash] += 1
        normalized.append(
            {
                "id": identifier,
                "text": text,
                "label": label,
                "group": group,
                "text_hash": text_hash,
                "origin_hash": origin_hash,
            }
        )
    normalized.sort(key=lambda item: (item["group"], item["id"]))
    # Repeated captures/URLs cannot inflate calibration support or training weight.
    examples, used_groups, used_texts = [], set(), set()
    for record in normalized:
        if record["group"] in used_groups or record["text_hash"] in used_texts:
            continue
        examples.append(record)
        used_groups.add(record["group"])
        used_texts.add(record["text_hash"])
    summary = {
        "records": len(records),
        "groups": len(groups),
        "unique_texts": len(texts),
        "effective_records": len(examples),
        "label_counts": dict(Counter(record["label"] for record in examples)),
        "origin_counts": dict(origins),
        "fingerprint_sha256": _digest(
            [
                {
                    "id_sha256": _digest(record["id"]),
                    "group_sha256": _digest(record["group"]),
                    "text_sha256": record["text_hash"],
                    "label": record["label"],
                    "origin_sha256": record["origin_hash"],
                }
                for record in normalized
            ]
        ),
    }
    return examples, {"ids": ids, "groups": groups, "texts": texts}, summary


def _vectorizer(vocabulary=None):
    options = {**_VECTORIZER, "ngram_range": tuple(_VECTORIZER["ngram_range"])}
    return TfidfVectorizer(**options, vocabulary=vocabulary, dtype=np.float64)


def _estimator():
    return LogisticRegression(
        solver="lbfgs", max_iter=500, random_state=0, tol=1e-4, C=4.0, class_weight="balanced"
    )


def _gates(classes, probabilities, labels, usable, min_precision, min_support):
    thresholds, reports = {}, {}
    winners = probabilities.argmax(axis=1) if len(probabilities) else []
    for index, label in enumerate(classes):
        observations = sorted(
            [
                (float(probabilities[row, index]), labels[row] == label)
                for row in range(len(labels))
                if usable[row] and winners[row] == index
            ],
            reverse=True,
        )
        threshold, accepted, correct_at_gate = None, 0, 0
        correct = 0
        for number, (score, right) in enumerate(observations, 1):
            correct += right
            # Include the whole equal-score group; no optimistic tie splitting.
            if number < len(observations) and observations[number][0] == score:
                continue
            if number >= min_support and correct / number >= min_precision:
                threshold, accepted, correct_at_gate = score, number, correct
        thresholds[label] = threshold
        reports[label] = {
            "predicted_groups": len(observations),
            "accepted_groups": accepted,
            "correct_groups": correct_at_gate,
            "observed_precision": correct_at_gate / accepted if accepted else None,
        }
    return thresholds, reports


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def _require(condition, message):
    if not condition:
        raise ValueError("Invalid local classifier artifact: " + message)


def _validate_summary(summary):
    _require(
        isinstance(summary, dict)
        and set(summary)
        == {
            "records",
            "groups",
            "unique_texts",
            "effective_records",
            "label_counts",
            "origin_counts",
            "fingerprint_sha256",
        },
        "split summary",
    )
    for field in ("records", "groups", "unique_texts", "effective_records"):
        _require(type(summary[field]) is int and 0 <= summary[field] <= MAX_RECORDS, "split count")
    count = summary["effective_records"]
    _require(count <= summary["groups"] <= summary["records"], "group counts")
    _require(count <= summary["unique_texts"] <= summary["records"], "content counts")
    for key, total in (("label_counts", count), ("origin_counts", summary["records"])):
        values = summary[key]
        _require(isinstance(values, dict), "provenance counts")
        _require(
            all(
                _bounded_string(name) and type(n) is int and 0 < n <= MAX_RECORDS
                for name, n in values.items()
            ),
            "provenance count entries",
        )
        _require(sum(values.values()) == total, "provenance count total")
    _require(
        all(key == "unspecified" or _SHA256.fullmatch(key) for key in summary["origin_counts"]),
        "origin fingerprints",
    )
    _require(
        isinstance(summary["fingerprint_sha256"], str)
        and _SHA256.fullmatch(summary["fingerprint_sha256"]),
        "split fingerprint",
    )


def _validate_payload(payload):
    _require(
        isinstance(payload, dict)
        and set(payload)
        == {
            "format",
            "vectorizer",
            "vocabulary",
            "idf",
            "classes",
            "coef",
            "intercept",
            "thresholds",
            "metadata",
        },
        "properties",
    )
    _require(
        payload["format"] == FORMAT and _encoded(payload["vectorizer"]) == _encoded(_VECTORIZER),
        "format/options",
    )
    vocabulary = payload["vocabulary"]
    _require(
        isinstance(vocabulary, dict) and 0 < len(vocabulary) <= MAX_FEATURES, "vocabulary size"
    )
    features = len(vocabulary)
    _require(
        all(
            isinstance(key, str) and 2 <= len(key) <= 4 and type(value) is int
            for key, value in vocabulary.items()
        ),
        "vocabulary entries",
    )
    _require(set(vocabulary.values()) == set(range(features)), "vocabulary dimensions")
    classes = payload["classes"]
    _require(
        isinstance(classes, list)
        and 2 <= len(classes) <= MAX_CLASSES
        and all(_bounded_string(label) for label in classes),
        "classes",
    )
    _require(len(set(classes)) == len(classes), "duplicate classes")
    rows = 1 if len(classes) == 2 else len(classes)
    _require(
        isinstance(payload["idf"], list)
        and len(payload["idf"]) == features
        and all(_number(value, 1, 20) for value in payload["idf"]),
        "IDF dimensions/values",
    )
    _require(
        isinstance(payload["coef"], list)
        and len(payload["coef"]) == rows
        and all(
            isinstance(row, list)
            and len(row) == features
            and all(_number(value, -1e6, 1e6) for value in row)
            for row in payload["coef"]
        ),
        "coefficient dimensions/values",
    )
    _require(
        isinstance(payload["intercept"], list)
        and len(payload["intercept"]) == rows
        and all(_number(value, -1e6, 1e6) for value in payload["intercept"]),
        "intercepts",
    )
    thresholds, metadata = payload["thresholds"], payload["metadata"]
    _require(isinstance(thresholds, dict) and set(thresholds) == set(classes), "threshold classes")
    _require(
        isinstance(metadata, dict)
        and set(metadata)
        == {
            "sklearn_version",
            "score_kind",
            "evaluation_note",
            "min_precision",
            "min_support",
            "iterations",
            "training",
            "calibration",
            "class_gates",
            "unseen_calibration_groups",
            "group_overlap",
            "text_overlap",
        },
        "metadata",
    )
    _require(
        metadata["sklearn_version"] == sklearn.__version__, "sklearn version mismatch; retrain"
    )
    _require(
        metadata["score_kind"] == "uncalibrated_logistic_predict_proba"
        and metadata["evaluation_note"]
        == "Threshold-selection observations, not independent accuracy.",
        "score interpretation",
    )
    _require(
        _number(metadata["min_precision"], 0, 1)
        and metadata["min_precision"] > 0
        and type(metadata["min_support"]) is int
        and 1 <= metadata["min_support"] <= MAX_RECORDS,
        "gate settings",
    )
    _require(
        type(metadata["iterations"]) is int and 0 <= metadata["iterations"] <= 500, "iterations"
    )
    _validate_summary(metadata["training"])
    _validate_summary(metadata["calibration"])
    _require(set(metadata["training"]["label_counts"]) == set(classes), "training classes")
    _require(
        all(
            type(metadata[key]) is int and metadata[key] == 0
            for key in ("group_overlap", "text_overlap")
        ),
        "split overlap",
    )
    count = metadata["calibration"]["effective_records"]
    _require(
        type(metadata["unseen_calibration_groups"]) is int
        and 0 <= metadata["unseen_calibration_groups"] <= count,
        "unseen feature count",
    )
    gates = metadata["class_gates"]
    _require(isinstance(gates, dict) and set(gates) == set(classes), "gate reports")
    predicted_total = 0
    for label in classes:
        report, threshold = gates[label], thresholds[label]
        _require(
            isinstance(report, dict)
            and set(report)
            == {
                "predicted_groups",
                "accepted_groups",
                "correct_groups",
                "observed_precision",
            },
            "gate report properties",
        )
        predicted, accepted, correct = (
            report[key] for key in ("predicted_groups", "accepted_groups", "correct_groups")
        )
        _require(
            all(type(value) is int for value in (predicted, accepted, correct))
            and 0 <= correct <= accepted <= predicted <= count,
            "gate counts",
        )
        predicted_total += predicted
        if threshold is None:
            _require(
                accepted == correct == 0 and report["observed_precision"] is None,
                "abstention report",
            )
        else:
            _require(
                _number(threshold, 1 / len(classes), 1) and accepted >= metadata["min_support"],
                "unsupported threshold",
            )
            precision = report["observed_precision"]
            _require(
                _number(precision, metadata["min_precision"], 1)
                and correct / accepted >= metadata["min_precision"]
                and math.isclose(precision, correct / accepted, abs_tol=1e-12),
                "gate precision",
            )
    _require(predicted_total <= count - metadata["unseen_calibration_groups"], "predicted total")


class LocalPageModel:
    """Use ``LocalPageModel.fit(training, calibration)`` or ``load(path)``.

    Duplicate content groups are reduced to one deterministic representative per
    split. Origin values and record content are fingerprinted, never copied into
    metadata. A supported gate is only an observation on threshold-selection data.
    """

    def __init__(self, payload, vectorizer, estimator):
        self._payload = payload
        self._vectorizer = vectorizer
        self._estimator = estimator
        self._key = _digest(payload)

    @classmethod
    def fit(cls, training, calibration, *, min_precision=0.98, min_support=5):
        if (
            not _number(min_precision, 0, 1)
            or min_precision == 0
            or type(min_support) is not int
            or not 1 <= min_support <= MAX_RECORDS
        ):
            raise ValueError("Invalid observed-precision/support requirements")
        train, train_sets, train_summary = _prepare(training, "training")
        cal, cal_sets, cal_summary = _prepare(calibration, "calibration")
        if any(train_sets[key] & cal_sets[key] for key in ("ids", "groups", "texts")):
            raise ValueError(
                "Training/calibration leakage: record, group or normalized content overlap"
            )
        labels = {record["label"] for record in train}
        if not 2 <= len(labels) <= MAX_CLASSES:
            raise ValueError("Training needs between 2 and 32 distinct classes")
        vectorizer, estimator = _vectorizer(), _estimator()
        features = vectorizer.fit_transform([record["text"] for record in train])
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            try:
                estimator.fit(features, [record["label"] for record in train])
            except ConvergenceWarning as exc:
                raise ValueError(
                    "Local classifier did not converge within its iteration budget"
                ) from exc
        # Use the same memory layout before threshold selection and JSON reconstruction.
        estimator.coef_ = np.ascontiguousarray(estimator.coef_, dtype=np.float64)
        estimator.intercept_ = np.ascontiguousarray(estimator.intercept_, dtype=np.float64)
        classes = estimator.classes_.tolist()
        if cal:
            cal_features = vectorizer.transform([record["text"] for record in cal])
            probabilities = estimator.predict_proba(cal_features)
            usable = np.asarray(cal_features.getnnz(axis=1) > 0)
            sorted_scores = np.sort(probabilities, axis=1)
            usable &= sorted_scores[:, -1] > sorted_scores[:, -2]
            unseen = int(np.sum(cal_features.getnnz(axis=1) == 0))
        else:
            probabilities, usable, unseen = np.empty((0, len(classes))), [], 0
        thresholds, reports = _gates(
            classes,
            probabilities,
            [record["label"] for record in cal],
            usable,
            min_precision,
            min_support,
        )
        payload = {
            "format": FORMAT,
            "vectorizer": dict(_VECTORIZER),
            "vocabulary": {term: int(index) for term, index in vectorizer.vocabulary_.items()},
            "idf": vectorizer.idf_.tolist(),
            "classes": classes,
            "coef": estimator.coef_.tolist(),
            "intercept": estimator.intercept_.tolist(),
            "thresholds": thresholds,
            "metadata": {
                "sklearn_version": sklearn.__version__,
                "score_kind": "uncalibrated_logistic_predict_proba",
                "evaluation_note": "Threshold-selection observations, not independent accuracy.",
                "min_precision": min_precision,
                "min_support": min_support,
                "iterations": int(estimator.n_iter_.max()),
                "training": train_summary,
                "calibration": cal_summary,
                "class_gates": reports,
                "unseen_calibration_groups": unseen,
                "group_overlap": 0,
                "text_overlap": 0,
            },
        }
        _validate_payload(payload)
        if len(_encoded({"sha256": _digest(payload), "model": payload})) > MAX_ARTIFACT_BYTES:
            raise ValueError("Local classifier artifact exceeds its byte limit")
        return cls(payload, vectorizer, estimator)

    @property
    def key(self):
        return self._key

    @property
    def metadata(self):
        return copy.deepcopy(self._payload["metadata"])

    def export_payload(self):
        """Return an independent numerical JSON payload (never executable state)."""
        return copy.deepcopy(self._payload)

    @classmethod
    def from_payload(cls, payload):
        """Validate and reconstruct a bounded numerical payload for composite models."""
        if len(_encoded(payload)) > MAX_ARTIFACT_BYTES:
            raise ValueError("Local classifier payload exceeds its byte limit")
        _validate_payload(payload)
        payload = copy.deepcopy(payload)
        vectorizer = _vectorizer(payload["vocabulary"])
        vectorizer.idf_ = np.asarray(payload["idf"], dtype=np.float64)
        estimator = _estimator()
        estimator.classes_ = np.asarray(payload["classes"])
        estimator.coef_ = np.asarray(payload["coef"], dtype=np.float64)
        estimator.intercept_ = np.asarray(payload["intercept"], dtype=np.float64)
        estimator.n_features_in_ = len(payload["vocabulary"])
        estimator.n_iter_ = np.asarray([payload["metadata"]["iterations"]], dtype=np.int32)
        return cls(payload, vectorizer, estimator)

    def predict(self, text):
        """Abstain without known features; nonzero features do not prove in-domain input."""
        if not isinstance(text, str) or not 2 <= len(text) <= MAX_TEXT_CHARS or not text.strip():
            return None
        try:
            text.encode("utf-8")
        except UnicodeError:
            return None
        features = self._vectorizer.transform([text])
        if features.nnz == 0:
            return None
        scores = self._estimator.predict_proba(features)[0]
        if not np.isfinite(scores).all():
            return None
        winner = int(np.argmax(scores))
        label, confidence = self._payload["classes"][winner], float(scores[winner])
        margin = confidence - float(np.partition(scores, -2)[-2])
        threshold = self._payload["thresholds"][label]
        if threshold is None or margin <= 0 or confidence < threshold:
            return None
        return {"label": label, "confidence": confidence, "margin": margin}

    def save(self, path):
        encoded = _encoded({"sha256": self.key, "model": self._payload})
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError("Local classifier artifact exceeds its byte limit")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".local-model-", delete=False
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
            raise ValueError("Expected a bounded, regular JSON artifact file")
        with path.open("rb") as handle:
            encoded = handle.read(MAX_ARTIFACT_BYTES + 1)
        if len(encoded) > MAX_ARTIFACT_BYTES:
            raise ValueError("Local classifier artifact exceeds its byte limit")
        try:
            envelope = json.loads(
                encoded, object_pairs_hook=_object, parse_constant=_reject_constant
            )
            _require(
                isinstance(envelope, dict) and set(envelope) == {"sha256", "model"}, "envelope"
            )
            payload = envelope["model"]
            _require(
                isinstance(envelope["sha256"], str)
                and _SHA256.fullmatch(envelope["sha256"])
                and envelope["sha256"] == _digest(payload),
                "content digest",
            )
            _validate_payload(payload)
        except (RecursionError, UnicodeError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid local classifier JSON artifact") from exc
        vectorizer = _vectorizer(payload["vocabulary"])
        vectorizer.idf_ = np.asarray(payload["idf"], dtype=np.float64)
        estimator = _estimator()
        # Public fitted attributes preserve the original binary/multinomial class order.
        estimator.classes_ = np.asarray(payload["classes"])
        estimator.coef_ = np.asarray(payload["coef"], dtype=np.float64)
        estimator.intercept_ = np.asarray(payload["intercept"], dtype=np.float64)
        estimator.n_features_in_ = len(payload["vocabulary"])
        estimator.n_iter_ = np.asarray([payload["metadata"]["iterations"]], dtype=np.int32)
        return cls(payload, vectorizer, estimator)
