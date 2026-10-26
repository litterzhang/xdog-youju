"""Host-reviewed temporal inputs, attached to saved material without fetching it."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    RepairRequest,
    Resource,
    ResourceBundle,
    ResourceScan,
    Source,
    SourcePipeline,
    normalize_url,
)
from youju.models.onboarding import AnnouncementChangeReview, SourceCalendarReview

from .relative_periods import RELATIVE_RE, validate_calendar
from .store import digest, json_bytes


def calendar_review(session, source_id):
    return session.scalar(
        select(SourceCalendarReview)
        .where(SourceCalendarReview.source_id == source_id)
        .order_by(SourceCalendarReview.created_at.desc(), SourceCalendarReview.id.desc())
        .limit(1)
    )


def change_review(session, resource_id):
    return session.scalar(
        select(AnnouncementChangeReview)
        .where(AnnouncementChangeReview.target_resource_id == resource_id)
        .order_by(AnnouncementChangeReview.created_at.desc(), AnnouncementChangeReview.id.desc())
        .limit(1)
    )


def change_current(session, review, bundle_id):
    event = session.get(ResourceScan, review.event_resource_id)
    event_bundle = session.get(ResourceBundle, review.event_bundle_id)
    return (
        review.target_bundle_id == bundle_id
        and event is not None
        and event_bundle is not None
        and event.state in {"done", "review"}
        and event.bundle_id == review.event_bundle_id
        and event.last_version_id == event_bundle.page_version_id
    )


def attach_context(store, session, bundle):
    resource_id = bundle.get("resource_id")
    if resource_id is None:
        # Historical classifier-export bundles may omit identity metadata. Read
        # authoritative ownership from the saved row; never infer it from a URL.
        saved = session.get(ResourceBundle, bundle.get("id"))
        resource_id = saved.resource_id if saved is not None else None
    related = associated_event_reviews(session, resource_id, bundle["id"])
    if related:
        bundle["associated_event_reviews"] = related
    context = {}
    if any(
        match["unit"] == "工作日"
        for document in [bundle.get("page", {}), *bundle.get("attachments", [])]
        for block in document.get("blocks", [])
        for match in RELATIVE_RE.finditer(block.get("text", ""))
    ):
        review = calendar_review(session, bundle["source_id"])
        if review is not None:
            body = store.blobs.read(review.body_ref)
            if len(body) > 256 * 1024:
                raise ValueError("Reviewed calendar exceeds byte limit")
            context["calendar"] = {
                "review_id": review.id,
                "value": validate_calendar(json.loads(body)),
            }
    review = change_review(session, resource_id)
    if review is not None:
        valid = change_current(session, review, bundle["id"])
        context["change"] = {
            "review_id": review.id,
            "effect": review.effect,
            "target_bundle_id": review.target_bundle_id,
            "event_bundle_id": review.event_bundle_id,
            "supersedes": review.supersedes,
            "status": "current" if valid else "stale",
        }
        if valid:
            event = session.get(ResourceBundle, review.event_bundle_id)
            raw = json.loads(store.blobs.read(event.body_ref))
            # Reviewed event material is a separate fact dependency; source adapters
            # never merge its tables into the original position set.
            bundle.setdefault("attachments", []).append(
                {**raw["page"], "change_review_id": review.id}
            )
    if context:
        bundle["period_context"] = context
        facts = position_fact_context(bundle)
        if facts is not None:
            bundle["fact_context"] = facts
    return bundle


def position_fact_context(bundle):
    """Assemble host-reviewed associations as data, not a notice-expiry result.

    The source store has already checked association ownership/current versions.
    The domain still validates literal effect/scope; a review ID alone cannot
    turn arbitrary material into a cancellation or date override.
    """
    from copy import deepcopy

    period = bundle.get("period_context", {})
    change, calendar = period.get("change"), period.get("calendar")
    if not isinstance(change, dict) and not isinstance(calendar, dict):
        return None
    value = {"bundle_id": bundle.get("id")}
    if isinstance(calendar, dict):
        value["calendar"] = deepcopy(calendar)
    if isinstance(change, dict):
        docs = [
            deepcopy(doc)
            for doc in bundle.get("attachments", [])
            if doc.get("change_review_id") == change.get("review_id")
        ]
        for doc in docs:
            doc["event_bundle_id"] = change.get("event_bundle_id")
        value.update(change=deepcopy(change), change_documents=docs)
    return value


def register_calendar(store, source_id, value, *, reviewer, reference):
    value = validate_calendar(value)
    if not isinstance(reviewer, str) or not 0 < len(reviewer.strip()) <= 128:
        raise ValueError("A workday policy requires an explicit human reviewer")
    if not isinstance(reference, str) or not 0 < len(reference.strip()) <= 2048:
        raise ValueError("A workday policy requires independent provenance")
    body_ref = store.blobs.put(json_bytes(value))
    identity = digest(
        {"source_id": source_id, "calendar": body_ref, "reviewer": reviewer, "reference": reference}
    )
    with store.lock, Session(store.engine) as session, session.begin():
        if session.get(Source, source_id) is None:
            raise ValueError("Unknown calendar source")
        pipeline = session.get(SourcePipeline, source_id)
        if pipeline and (pipeline.lease_token or pipeline.state == "running"):
            raise ValueError("Finish source work before updating temporal policies")
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
            raise ValueError("Finish the pending repair before updating temporal policies")
        if session.get(SourceCalendarReview, identity) is None:
            session.add(
                SourceCalendarReview(
                    id=identity,
                    source_id=source_id,
                    body_ref=body_ref,
                    reviewer=reviewer,
                    reference=reference,
                )
            )
        return {"review_id": identity, "queued_model_tasks": 0, "published": False}


def current(session, audit):
    """Updated or stale reviewed dependencies invalidate prior temporal observations."""
    if audit.result.get("schema_version") == "position-facts-v3":
        from .auditing import associated_change_issues

        dependencies = audit.result.get("fact_dependencies") or {}
        if "calendar" in dependencies:
            saved = session.get(ResourceBundle, audit.bundle_id)
            resource = session.get(Resource, saved.resource_id) if saved else None
            latest = calendar_review(session, resource.source_id) if resource else None
            if dependencies["calendar"].get("review_id") != (latest.id if latest else None):
                return False
        return not associated_change_issues(
            session,
            audit.bundle_id,
            dependencies=audit.result.get("fact_dependencies"),
            disposition=audit.result.get("disposition"),
        )
    period = audit.result.get("validity", {}).get("application_period", {})
    refs = period.get("context_refs", {})
    bundle = session.get(ResourceBundle, audit.bundle_id)
    resource = session.get(Resource, bundle.resource_id) if bundle else None
    if resource is None:
        return False
    if "calendar_ref" in refs:
        latest = calendar_review(session, resource.source_id)
        if refs["calendar_ref"] != (latest.id if latest else None):
            return False
    change = change_review(session, resource.id)
    if change is not None:
        if refs.get("change_ref") != change.id or not change_current(
            session, change, audit.bundle_id
        ):
            return False
    elif refs.get("change_ref"):
        return False
    return True


def register_change(
    store,
    source_id,
    target_bundle_id,
    event_bundle_id,
    *,
    effect,
    reviewer,
    reference,
    supersedes=None,
):
    from youju.positions.changes import observe_change

    if effect not in {"extension", "cancellation"}:
        raise ValueError("Only explicit notice-wide extension/cancellation is supported")
    if not isinstance(reviewer, str) or not 0 < len(reviewer.strip()) <= 128:
        raise ValueError("A change association needs a named reviewer")
    if not isinstance(reference, str) or not 0 < len(reference.strip()) <= 2048:
        raise ValueError("A change association needs independent provenance")
    with store.lock, Session(store.engine) as session, session.begin():
        target = session.get(ResourceBundle, target_bundle_id)
        event = session.get(ResourceBundle, event_bundle_id)
        target_resource = session.get(Resource, target.resource_id) if target else None
        event_resource = session.get(Resource, event.resource_id) if event else None
        if (
            target_resource is None
            or event_resource is None
            or target.resource_id == event.resource_id
            or target_resource.source_id != source_id
            or event_resource.source_id != source_id
        ):
            raise ValueError("Associated target/event must be distinct resources of this source")
        pipeline = session.get(SourcePipeline, source_id)
        if pipeline and (pipeline.lease_token or pipeline.state == "running"):
            raise ValueError("Finish source work before associating changes")
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
            raise ValueError("Finish pending repair before associating changes")
        for bundle in (target, event):
            scan = session.get(ResourceScan, bundle.resource_id)
            if (
                scan is None
                or scan.state not in {"done", "review"}
                or scan.bundle_id != bundle.id
                or scan.last_version_id != bundle.page_version_id
            ):
                raise ValueError("Association requires settled current saved material")
        raw = json.loads(store.blobs.read(event.body_ref))
        if observe_change(raw["page"], effect) is None:
            raise ValueError("Amendment needs supported notice-wide literal semantics")
        targets = set()
        for link in raw["page"].get("links", []):
            try:
                targets.add(normalize_url(link.get("url", "")))
            except (ValueError, TypeError):
                pass
        if target_resource.normalized_url not in targets:
            raise ValueError("Amendment must literally link the reviewed original notice")
        latest = change_review(session, target.resource_id)
        # Original per-position scope/date consistency belongs to shared draft
        # normalization at execution time, not a second source-owned extractor.
        # Staging this association neither publishes a date nor activates a Recipe.
        identity = digest(
            {
                "source_id": source_id,
                "target": target.id,
                "event": event.id,
                "effect": effect,
                "reviewer": reviewer,
                "reference": reference,
                "supersedes": supersedes,
            }
        )
        if session.get(AnnouncementChangeReview, identity) is not None:
            return {"review_id": identity, "published": False, "queued_model_tasks": 0}
        if (latest.id if latest else None) != supersedes:
            raise ValueError(
                "Explicit supersedes must name the current association; no timestamp precedence"
            )
        session.add(
            AnnouncementChangeReview(
                id=identity,
                source_id=source_id,
                target_resource_id=target.resource_id,
                target_bundle_id=target.id,
                event_resource_id=event.resource_id,
                event_bundle_id=event.id,
                effect=effect,
                supersedes=supersedes,
                reviewer=reviewer,
                reference=reference,
            )
        )
        return {"review_id": identity, "published": False, "queued_model_tasks": 0}


def associated_event_reviews(session, resource_id, bundle_id):
    """Reviewed change resources remain monitored, but are not new vacancy notices."""
    output = []
    for review in session.scalars(
        select(AnnouncementChangeReview).where(
            AnnouncementChangeReview.event_resource_id == resource_id,
            AnnouncementChangeReview.event_bundle_id == bundle_id,
        )
    ):
        latest = change_review(session, review.target_resource_id)
        target = session.get(ResourceScan, review.target_resource_id)
        if (
            latest is not None
            and latest.id == review.id
            and target is not None
            and target.state == "done"
            and target.bundle_id == review.target_bundle_id
            and change_current(session, review, target.bundle_id)
        ):
            output.append(
                {
                    "review_id": review.id,
                    "target_bundle_id": review.target_bundle_id,
                    "effect": review.effect,
                }
            )
    return output
