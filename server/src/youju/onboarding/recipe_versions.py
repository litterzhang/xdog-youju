"""Source-side immutable revision import; service artifacts are never edited here."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.models import (
    AuditResult,
    Resource,
    ResourceBundle,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)

from .recipe_client import RecipeRPCError
from .recipes import candidate_digest
from .store import digest, json_bytes


def import_revision(
    store, source_id, revision, task, *, runner, toolkit, sample_ids, coverage=None
):
    candidate = revision.get("candidate")
    if (
        revision.get("id") != task.get("revision_id")
        or revision.get("workspace_id") != task.get("workspace_id")
        or revision.get("task_id") != task.get("id")
        or revision.get("input_revision_id") != task.get("input_revision_id")
        or candidate_digest(candidate) != revision.get("artifact_digest")
        or revision.get("artifact_digest") != task.get("artifact_digest")
        or runner.validate(candidate)
    ):
        raise RecipeRPCError("RECIPE_REVISION_INVALID")
    identity = digest(
        {"revision": revision["id"], "workspace": revision["workspace_id"], "toolkit": toolkit}
    )
    with store.lock, Session(store.engine) as session, session.begin():
        row = session.scalar(
            select(SourceRecipe).where(
                SourceRecipe.source_id == source_id,
                SourceRecipe.digest == identity,
            )
        )
        if row is None:
            row = SourceRecipe(
                source_id=source_id,
                digest=identity,
                body_ref=store.blobs.put(json_bytes(candidate)),
                status="validated",
                revision_id=revision["id"],
                workspace_id=revision["workspace_id"],
                samples=sample_ids,
                coverage=coverage or {},
                validation={
                    "errors": [],
                    "accuracy_contract": candidate["config"].get("accuracy_contract"),
                    "toolkit_key": toolkit,
                    "sample_validation": revision.get("sample_validation", []),
                },
            )
            session.add(row)
            session.flush()
        elif (
            row.revision_id != revision["id"]
            or row.workspace_id != revision["workspace_id"]
            or row.body_ref != store.blobs.put(json_bytes(candidate))
        ):
            raise RecipeRPCError("RECIPE_REVISION_CHANGED")
        return row.id


def next_generation(session, source_id):
    maximum = session.scalar(
        select(func.max(AuditResult.generation))
        .join(
            ResourceBundle,
            ResourceBundle.id == AuditResult.bundle_id,
        )
        .join(Resource)
        .where(Resource.source_id == source_id)
    )
    binding = session.get(SourceRecipeBinding, source_id)
    pipeline = session.get(SourcePipeline, source_id)
    requested = pipeline.summary.get("candidate_generation", 0) if pipeline else 0
    return max(maximum or 0, binding.generation if binding else 0, requested) + 1


def queue_review(session, source_id, *, workspace_id, revision_id, decision, feedback, request_id):
    """Persist an owner decision before RPC; an acknowledgement never replaces an audit."""
    pipeline = session.get(SourcePipeline, source_id)
    previous = pipeline.summary.get("revision_review", {})
    intent = {
        "workspace_id": workspace_id,
        "revision_id": revision_id,
        "decision": decision,
        "feedback": feedback,
        "request_id": request_id,
    }
    if previous.get("status") == "pending" and previous.get("intent") != intent:
        raise RecipeRPCError("RECIPE_REVIEW_PENDING")
    if previous.get("intent") == intent:
        return
    pipeline.summary = dict(
        pipeline.summary, revision_review={"status": "pending", "intent": intent}
    )
