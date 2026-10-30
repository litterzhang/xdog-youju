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


def same_source_revision(session, binding):
    """An unchanged service revision must still name identical immutable artifacts."""
    if not binding.candidate_revision_id or (
        binding.candidate_revision_id != binding.active_revision_id
    ):
        return False
    old = session.get(SourceRecipe, binding.active_recipe_id)
    new = session.get(SourceRecipe, binding.candidate_recipe_id)
    if (
        old is None
        or new is None
        or old.revision_id != binding.active_revision_id
        or new.revision_id != binding.candidate_revision_id
        or old.source_id != binding.source_id
        or new.source_id != binding.source_id
        or (old.body_ref, old.workspace_id) != (new.body_ref, new.workspace_id)
    ):
        raise ValueError("An unchanged service revision requires identical artifacts")
    return True


def failed_candidate_audit(session, source_id, audit_id):
    """Late or unbound audit results have no authority over the current candidate."""
    from youju.models import AuditRun

    row = session.get(SourcePipeline, source_id)
    binding = session.get(SourceRecipeBinding, source_id)
    audit = session.get(AuditRun, audit_id)
    if (
        row is None
        or binding is None
        or audit is None
        or row.stage != "audit"
        or row.summary.get("audit_id") != audit_id
        or audit.source_id != source_id
        or audit.kind != "onboarding"
        or audit.recipe_id != binding.candidate_recipe_id
        or audit.status not in {"complete", "blocked"}
        or audit.finished_at is None
        or audit.summary.get("passed") is not False
        or audit.summary.get("generation") != row.summary.get("candidate_generation")
    ):
        raise RecipeRPCError("RECIPE_REVIEW_CHANGED")
    return binding


def review_diagnostic(session, source_id):
    """Read-only explanation of legacy pending decisions; never dequeue or call RPC."""
    row = session.get(SourcePipeline, source_id)
    binding = session.get(SourceRecipeBinding, source_id)
    if row is None or binding is None:
        raise ValueError("Unknown source")
    pending = row.summary.get("revision_review", {})
    intent = pending.get("intent", {})
    runtime_conflict = bool(
        pending.get("status") == "pending"
        and intent.get("decision") == "reject"
        and intent.get("revision_id") == binding.active_revision_id
    )
    return {
        "source_id": source_id,
        "review_status": pending.get("status", "none"),
        "revision_id": intent.get("revision_id"),
        "decision": intent.get("decision"),
        "audit_id": intent.get("feedback", {}).get("audit_id"),
        "candidate_recipe_id": binding.candidate_recipe_id,
        "candidate_generation": row.summary.get("candidate_generation"),
        "runtime_review_conflict": runtime_conflict,
        "changed": False,
        "next_action": "Explicitly reconcile the pending intent against the saved audit and "
        "service review receipt; preserve the terminal source decision. No automatic reset."
        if runtime_conflict
        else "No legacy same-revision reject conflict detected.",
    }
