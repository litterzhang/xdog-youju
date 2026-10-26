"""Host-only semantic expectations, receipts and holdout exposure.

No model, author tool, HTTP request or candidate supplies an expectation. Explicit
operator registration is append-only and never edits extraction output or publishes
a recipe. Checks bind the exact result and checker; stale checks fail closed.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    AuditResult,
    RepairRequest,
    Resource,
    ResourceBundle,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.onboarding import SemanticAuditCheck, SemanticCaseExposure, SemanticCaseLabel
from youju.positions.facts import POSITION_FIELDS, PositionFacts, TaggedField

from .semantics import check_expected
from .store import digest, json_bytes

MAX_EXPECTATION_BYTES = 1024 * 1024
MAX_EXPECTATIONS = 1000


def checker_key():
    fingerprint = hashlib.sha256()
    root = Path(__file__).parent
    for path in (
        root / "semantics.py",
        root / "semantic_labels.py",
        root.parent / "positions" / "facts.py",
    ):
        fingerprint.update(path.read_bytes())
    return fingerprint.hexdigest()


def validate_expected(value):
    """Omitted fields are not assertions of absence; complete assertions are explicit."""
    if (
        not isinstance(value, dict)
        or set(value) - {"basis", "reference", "positions", "complete_position_set"}
        or not {"basis", "reference", "positions"} <= value.keys()
    ):
        raise ValueError("Invalid reviewed semantic expectation")
    if value["basis"] not in {"human-confirmed", "independent-rule"}:
        raise ValueError("A model suggestion cannot be registered as semantic gold")
    if not isinstance(value["reference"], str) or not 0 < len(value["reference"]) <= 2048:
        raise ValueError("A reviewed case requires an independent reference")
    if type(value.get("complete_position_set", False)) is not bool:
        raise ValueError("Completeness must be explicit boolean")
    if not isinstance(value["positions"], list) or len(value["positions"]) > MAX_EXPECTATIONS:
        raise ValueError("Too many semantic position expectations")
    if not value["positions"] and value.get("complete_position_set") is not True:
        raise ValueError(
            "A reviewed semantic case needs assertions, not an empty partial expectation"
        )
    seen = set()
    for item in value["positions"]:
        if not isinstance(item, dict) or set(item) - {
            "selector",
            "fields",
            "conditions",
            "condition_logic",
        }:
            raise ValueError("Invalid semantic position properties")
        selector = item.get("selector")
        if (
            not isinstance(selector, dict)
            or "title" not in selector
            or not ({"hiring_unit", "employer"} & selector.keys())
            or set(selector) - {"title", "hiring_unit", "employer", "work_location"}
            or any(not isinstance(v, str) or not 0 < len(v) <= 2048 for v in selector.values())
        ):
            raise ValueError("A semantic identity needs a title AND unit/employer, not title alone")
        identity = digest(selector)
        if identity in seen:
            raise ValueError("Ambiguous duplicate semantic selector")
        seen.add(identity)
        fields = item.get("fields", {})
        if not isinstance(fields, dict) or set(fields) - set(POSITION_FIELDS):
            raise ValueError("Unsupported semantic field")
        for field, fact in fields.items():
            TaggedField.from_dict(fact)
            if field in selector and fact["value"] != selector[field]:
                raise ValueError("Semantic selector conflicts with its field assertion")
        # Validate field types and condition grammar with the existing sole contract.
        facts = {name: {"value": None, "tag": "unknown"} for name in POSITION_FIELDS}
        facts.update(fields)
        if "title" not in fields:
            facts["title"] = {"value": selector["title"], "tag": "source"}
        has_conditions = "conditions" in item or "condition_logic" in item
        if has_conditions and not {"conditions", "condition_logic"} <= item.keys():
            raise ValueError("Conditions require explicit logic as well")
        PositionFacts.from_dict(
            {
                "schema_version": "position-facts-v3",
                **facts,
                "conditions": item.get("conditions", []),
                "condition_logic": item.get("condition_logic"),
            }
        )
    if len(json_bytes(value)) > MAX_EXPECTATION_BYTES:
        raise ValueError("Semantic expectation exceeds byte limit")
    return deepcopy(value)


def full_semantics(expected):
    """Only exhaustive independently reviewed facts can supersede every old count/delta."""
    return expected.get("complete_position_set") is True and all(
        set(item.get("fields", {})) == set(POSITION_FIELDS)
        and {"conditions", "condition_logic"} <= item.keys()
        for item in expected["positions"]
    )


def latest_label(session, bundle_id):
    return session.scalar(
        select(SemanticCaseLabel)
        .where(SemanticCaseLabel.bundle_id == bundle_id)
        .order_by(SemanticCaseLabel.created_at.desc(), SemanticCaseLabel.id.desc())
        .limit(1)
    )


def effective_split(session, label):
    exposed = session.scalar(
        select(SemanticCaseExposure.label_id)
        .join(SemanticCaseLabel, SemanticCaseLabel.id == SemanticCaseExposure.label_id)
        .where(SemanticCaseLabel.bundle_id == label.bundle_id)
        .limit(1)
    )
    return "regression" if exposed else label.split


def expose(session, label, context_id):
    if not isinstance(context_id, str) or not 0 < len(context_id) <= 128:
        raise ValueError("Invalid semantic exposure context")
    if session.get(SemanticCaseExposure, (label.id, context_id)) is None:
        session.add(SemanticCaseExposure(label_id=label.id, context_id=context_id))
        session.flush()


def author_visible(store, bundle_id):
    with store.lock, Session(store.engine) as session:
        label = latest_label(session, bundle_id)
        return label is None or effective_split(session, label) != "holdout"


def expected_value(store, label):
    body = store.blobs.read(label.expected_ref)
    if len(body) > MAX_EXPECTATION_BYTES:
        raise ValueError("Semantic expectation size mismatch")
    return validate_expected(json.loads(body))


def assess(store, session, bundle_id, result, *, not_evaluated_reason=None):
    label = latest_label(session, bundle_id)
    if label is None:
        return None, None
    bundle = session.get(ResourceBundle, bundle_id)
    resource = session.get(Resource, bundle.resource_id) if bundle else None
    if (
        bundle is None
        or bundle.digest != label.bundle_digest
        or resource.source_id != label.source_id
    ):
        raise ValueError("Semantic label material binding changed")
    expected = expected_value(store, label)
    check = (
        check_expected(result, expected)
        if not_evaluated_reason is None
        else {"status": not_evaluated_reason, "issues": [], "accuracy_verified": False}
    )
    if (
        not_evaluated_reason is None
        and result.get("status") == "cancelled"
        and result.get("disposition") == "cancelled"
    ):
        check = {"status": "not_applicable_cancelled", "issues": [], "accuracy_verified": False}
    if (
        not_evaluated_reason is None
        and result.get("execution", {}).get("path") == "quick"
        and not result.get("positions")
    ):
        check = {"status": "not_applicable_coarse", "issues": [], "accuracy_verified": False}
    check.update(
        label_id=label.id,
        material_digest=bundle.digest,
        fully_specified=full_semantics(expected),
        complete_position_set=expected.get("complete_position_set", False),
        basis=expected["basis"],
        reference=expected["reference"],
        split=effective_split(session, label),
    )
    return label, check


def errors_for(check, bundle_id):
    if (
        check is None
        or check["status"] in {"passed", "not_applicable_coarse", "not_applicable_cancelled"}
        or check["status"].startswith("not_evaluated_")
    ):
        return []
    issues = check["issues"] or [{"code": "SEMANTIC_FULL_REQUIRED", "path": "execution.path"}]
    return [
        {
            **issue,
            "code": "SEMANTIC_EXPECTATION",
            "semantic_code": issue["code"],
            "message": "Saved output disagrees with an independent reviewed semantic case",
            "case_id": bundle_id,
            "expectation_ref": check["label_id"],
            "expected_basis": check["basis"],
            "stage": "semantic",
            "scope": "resource",
        }
        for issue in issues
    ]


def save_check(session, audit, label, check):
    if label is None:
        return
    check = {key: value for key, value in check.items() if key != "split"}
    identity = (audit.id, label.id, checker_key())
    old = session.get(SemanticAuditCheck, identity)
    value = digest(audit.result)
    if old is not None:
        if old.result_digest != value or old.result != check:
            raise ValueError("Independent semantic receipt is immutable")
        return
    session.add(
        SemanticAuditCheck(
            audit_id=audit.id,
            label_id=label.id,
            checker_key=identity[2],
            result_digest=value,
            result=check,
        )
    )
    session.flush()


def receipt(session, audit):
    label = latest_label(session, audit.bundle_id)
    if label is None:
        return None
    row = session.get(SemanticAuditCheck, (audit.id, label.id, checker_key()))
    if row is None or row.result_digest != digest(audit.result):
        return {
            "label_id": label.id,
            "status": "recheck_required",
            "fully_specified": False,
            "issues": [],
            "basis": "unknown",
            "split": effective_split(session, label),
        }
    return {**row.result, "split": effective_split(session, label)}


def current(session, audit):
    check = receipt(session, audit)
    return check is None or check["status"] in {
        "passed",
        "not_applicable_coarse",
        "not_applicable_cancelled",
    }


def register_label(store, source_id, bundle_id, expected, *, reviewer, split="regression"):
    """Explicit trusted-host operation. Does not queue work, retag rows or publish."""
    expected = validate_expected(expected)
    if not isinstance(reviewer, str) or not 0 < len(reviewer.strip()) <= 128:
        raise ValueError("A semantic label requires a named reviewer")
    if split not in {"regression", "holdout"}:
        raise ValueError("Unknown semantic case split")
    reference = store.blobs.put(json_bytes(expected))
    with store.lock, Session(store.engine) as session, session.begin():
        bundle = session.get(ResourceBundle, bundle_id)
        resource = session.get(Resource, bundle.resource_id) if bundle else None
        if resource is None or resource.source_id != source_id:
            raise ValueError("Semantic case must belong to the requested source")
        pipeline = session.get(SourcePipeline, source_id)
        if pipeline and (pipeline.lease_token or pipeline.state == "running"):
            raise ValueError("Finish the source operation before changing semantic expectations")
        if session.scalar(
            select(RepairRequest.id)
            .where(
                RepairRequest.source_id == source_id,
                RepairRequest.status.in_(
                    ["queued", "preparing", "submitted", "auditing", "accepting", "rejecting"]
                ),
            )
            .limit(1)
        ):
            raise ValueError("Finish the pending repair before changing semantic expectations")
        if split == "holdout" and (
            session.scalar(
                select(SourceRecipe.id).where(SourceRecipe.source_id == source_id).limit(1)
            )
            or (
                pipeline
                and (pipeline.summary.get("authoring_job") or pipeline.summary.get("reanalysis"))
            )
        ):
            raise ValueError(
                "Previously authored sources cannot declare unseen holdouts retroactively"
            )
        identity = digest(
            {
                "source_id": source_id,
                "bundle_id": bundle_id,
                "material_digest": bundle.digest,
                "expected_ref": reference,
                "reviewer": reviewer,
                "split": split,
            }
        )
        label = session.get(SemanticCaseLabel, identity)
        if label is None:
            label = SemanticCaseLabel(
                id=identity,
                source_id=source_id,
                bundle_id=bundle_id,
                bundle_digest=bundle.digest,
                expected_ref=reference,
                reviewer=reviewer,
                split=split,
            )
            session.add(label)
            session.flush()
        # Check saved active output independently, without rewriting it. This
        # immediately quarantines a newly confirmed bad public row while preserving
        # healthy jobs and the original admission/audit history.
        binding = session.get(SourceRecipeBinding, source_id)
        if binding and binding.active_recipe_id:
            audit = session.scalar(
                select(AuditResult)
                .where(
                    AuditResult.bundle_id == bundle_id,
                    AuditResult.recipe_id == binding.active_recipe_id,
                    AuditResult.generation == binding.generation,
                )
                .order_by(AuditResult.evaluated_on.desc(), AuditResult.created_at.desc())
                .limit(1)
            )
            if audit is not None:
                actual_label, check = assess(store, session, bundle_id, audit.result)
                save_check(session, audit, actual_label, check)
        return {
            "label_id": label.id,
            "bundle_id": bundle_id,
            "split": effective_split(session, label),
            "queued_model_tasks": 0,
            "published": False,
        }
