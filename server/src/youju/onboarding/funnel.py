"""Read-only saved-source funnel and pure, explicitly bound replay aggregation.

Counts describe saved observations, not classification accuracy or live vacancies.
No PipelineStore/BlobStore is constructed: their constructors can create storage.
"""

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from youju.jobs import job_validity, public_jobs
from youju.models import (
    AuditResult,
    MaterialDocument,
    PageAssessment,
    ParseResult,
    Position,
    Resource,
    ResourceBundle,
    ResourceScan,
    ResourceVersion,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)

from .auditing import _available_dependency_current, _dependency_current
from .semantic_labels import current as semantic_current
from .store import digest, toolkit_key
from .temporal_context import current as temporal_current

SCHEMA = "source-data-funnel-v2"
VALIDITIES = ("not_expired", "expired", "unknown")
QUALITIES = ("coarse", "full", "unversioned")
STATES = ("accepted", "coarse", "cancelled", "rejected", "failed", "review", "missing", "stale")


def explicit_clock(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Clock must include a UTC offset")
    return stamp.astimezone(ZoneInfo("Asia/Shanghai"))


@dataclass(frozen=True)
class FunnelContext:
    source_id: str
    clock: str
    classifier_key: str | None
    recipe_id: str | None
    generation: int
    mode: str = "snapshot"
    contract: str = "saved_audits_not_new_contract_replay"


@dataclass(frozen=True)
class FunnelObservation:
    """One current resource, including missing/unclassified cases (never audit history).

    Replay callers normalize *host-validated* outputs into this contract. Counts
    are not inferred from old labels or from a recipe's self-reported success.
    Material/recipe/classifier bindings are required even for failed cases.
    """

    resource_id: str
    version_id: str | None
    document_id: str | None
    bundle_id: str | None
    classifier_key: str | None
    recipe_id: str | None
    generation: int
    material_current: bool = False
    classified: bool = False
    page_roles: tuple[str, ...] = ()
    business_type: str = "unknown"
    classification_review: bool = False
    state: str = "missing"
    validity: str = "unknown"
    cancelled: bool = False
    quality: str = "unversioned"
    reported_positions: int = 0
    accepted_positions: int = 0
    public_positions: int = 0
    public_positions_with_partial: int = 0
    reasons: tuple[str, ...] = ()
    routing_category: str = "unknown"
    position_calendar_statuses: tuple[str, ...] = ()


def aggregate_funnel(context, observations):
    """Pure aggregation; duplicate resources are errors, never a history denominator.

    Replay has no publication side effect or implied admission. Its public counts
    must be zero. A replay envelope must cover every resource, including failures.
    """
    explicit_clock(context.clock)
    if context.mode not in {"snapshot", "new_contract_replay"}:
        raise ValueError("Unknown measurement mode")
    if type(context.generation) is not int or context.generation < 0:
        raise ValueError("Invalid generation")
    if context.mode == "new_contract_replay" and (
        not context.classifier_key
        or not context.recipe_id
        or not context.contract
        or context.contract == "saved_audits_not_new_contract_replay"
    ):
        raise ValueError("Replay requires explicit new classifier, recipe and contract provenance")
    rows = list(observations)
    if len({r.resource_id for r in rows}) != len(rows):
        raise ValueError(
            "Expected one observation per unique resource; history/duplicates forbidden"
        )
    classification = Counter(
        dict.fromkeys(
            (
                "classified_current",
                "unclassified_or_stale",
                "notice_list",
                "recruitment_article",
                "recruitment_attachment",
                "recruitment_candidates",
                "notice_list_recruitment_overlap",
                "notice_list_recruitment_article_overlap",
                "classification_review",
                "cancellation_pages",
                "correction_pages",
            ),
            0,
        )
    )
    states, reasons = Counter(), Counter()
    validity = Counter({v: 0 for v in VALIDITIES})
    matrix = {
        v: {
            q: dict.fromkeys(("reported", "accepted", "public_default", "public_with_partial"), 0)
            for q in QUALITIES
        }
        for v in VALIDITIES
    }
    cancelled = 0
    for r in rows:
        if any(
            type(flag) is not bool
            for flag in (
                r.material_current,
                r.classified,
                r.classification_review,
                r.cancelled,
            )
        ):
            raise ValueError("Normalized observation flags must be booleans")
        if not isinstance(r.resource_id, str) or not r.resource_id:
            raise ValueError("A resource ID is required")
        if r.material_current and not all((r.version_id, r.document_id, r.bundle_id)):
            raise ValueError("Current material requires version/document/bundle bindings")
        if r.classified and not context.classifier_key:
            raise ValueError("Classification requires an explicit classifier key")
        if not isinstance(r.page_roles, (tuple, list)) or any(
            not isinstance(role, str) for role in r.page_roles
        ):
            raise ValueError("Page roles must be a normalized list")
        if (r.classifier_key, r.recipe_id, r.generation) != (
            context.classifier_key,
            context.recipe_id,
            context.generation,
        ):
            raise ValueError("Observation does not bind the selected classifier/recipe/generation")
        counts = (
            r.reported_positions,
            r.accepted_positions,
            r.public_positions,
            r.public_positions_with_partial,
        )
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("Position counts must be nonnegative integers")
        if not 0 <= r.accepted_positions <= r.reported_positions:
            raise ValueError("Accepted <= reported position counts required")
        if r.public_positions > r.public_positions_with_partial:
            raise ValueError("Default display cannot exceed all-material display")
        if r.state not in STATES or r.validity not in VALIDITIES or r.quality not in QUALITIES:
            raise ValueError("Unknown normalized state, validity or quality")
        if context.mode == "new_contract_replay" and r.public_positions_with_partial:
            raise ValueError("Offline replay cannot assert public admission")
        resolved = r.material_current and r.classified
        roles = set(r.page_roles) if resolved else set()
        recruitment = (
            resolved
            and r.business_type == "recruitment"
            and bool(roles & {"article", "attachment"})
        )
        if r.accepted_positions and (
            not recruitment
            or not context.recipe_id
            or r.classification_review
            or r.state != "accepted"
            or r.cancelled
            or r.quality == "coarse"
            or (context.mode == "new_contract_replay" and r.quality != "full")
        ):
            raise ValueError("Non-current/rejected/unresolved/coarse rows cannot be accepted")
        classification["classified_current"] += int(resolved)
        classification["unclassified_or_stale"] += int(not resolved)
        classification["notice_list"] += int("notice_list" in roles)
        classification["recruitment_article"] += int(recruitment and "article" in roles)
        classification["recruitment_attachment"] += int(recruitment and "attachment" in roles)
        classification["recruitment_candidates"] += int(recruitment)
        classification["notice_list_recruitment_overlap"] += int(
            recruitment and "notice_list" in roles
        )
        classification["notice_list_recruitment_article_overlap"] += int(
            recruitment and {"notice_list", "article"} <= roles
        )
        classification["classification_review"] += int(resolved and r.classification_review)
        classification["cancellation_pages"] += int(resolved and r.business_type == "cancellation")
        classification["correction_pages"] += int(resolved and r.business_type == "correction")
        reasons.update(r.reasons)
        if not recruitment:
            continue
        states[r.state] += 1
        if r.cancelled:
            cancelled += 1
            continue
        # Failed/rejected observations can retain independently validated validity,
        # but missing/stale bindings must never reuse an old date decision.
        v = r.validity if r.state not in {"missing", "stale"} else "unknown"
        validity[v] += 1
        cell = matrix[v][r.quality]
        for key, n in zip(cell, counts, strict=True):
            cell[key] += n
    totals = {
        k: sum(matrix[v][q][k] for v in VALIDITIES for q in QUALITIES)
        for k in ("reported", "accepted", "public_default", "public_with_partial")
    }
    # Display counts describe the public read policy, not audit acceptance. They
    # include old readable candidates when the current audit/material is stale.
    totals["public_default"] = sum(row.public_positions for row in rows)
    totals["public_with_partial"] = sum(row.public_positions_with_partial for row in rows)
    return {
        "schema": SCHEMA,
        "context": asdict(context),
        "denominators": {
            "unique_resources": len(rows),
            "latest_versions": len({r.version_id for r in rows if r.version_id}),
            "latest_material_documents": len({r.document_id for r in rows if r.document_id}),
            "current_bundles": len(
                {r.bundle_id for r in rows if r.material_current and r.bundle_id}
            ),
        },
        "classification": dict(classification),
        "recruitment": {
            "validity": dict(validity),
            "cancelled": cancelled,
            "states": {s: states[s] for s in STATES},
        },
        "positions_by_validity_quality": matrix,
        "cost_routing": dict(
            Counter(
                row.routing_category
                for row in rows
                if row.classified
                and row.business_type == "recruitment"
                and "article" in row.page_roles
            )
        ),
        "position_calendar_status": dict(
            Counter(status for row in rows for status in row.position_calendar_statuses)
        ),
        "positions": totals,
        "reason_counts": dict(sorted(reasons.items())),
        "observations": [
            asdict(replace(row, validity="unknown"))
            if row.state in {"missing", "stale"}
            else asdict(row)
            for row in rows
        ],
        "limits": [
            "Saved inventory only; coverage is not accuracy or confirmed live hiring.",
            "Notice-list and recruitment counts overlap; do not add them.",
            "Validity buckets plus cancelled equal recruitment candidates.",
            "Reported positions may include rejected output; accepted counts passed current gates.",
            "Public counts include expired/review candidates and are independent of acceptance.",
            "Snapshot labels are saved audit observations, not new-contract replay results.",
        ],
    }


class _SavedBlobs:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Blobs path must be an existing directory")

    def read(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
            raise ValueError("Invalid saved blob reference")
        path = self.root / reference
        if path.is_symlink():
            raise ValueError("Saved blob must not be a symlink")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != reference:
            raise ValueError("Saved blob digest mismatch")
        return body


def _material_current(session, blobs, resource, scan, bundle):
    if not scan or not bundle:
        return False, "missing_bundle"
    document = (
        session.get(MaterialDocument, scan.last_document_id) if scan.last_document_id else None
    )
    version = session.get(ResourceVersion, scan.last_version_id) if scan.last_version_id else None
    if (
        scan.state not in {"done", "review"}
        or bundle.resource_id != resource.id
        or bundle.page_version_id != scan.last_version_id
        or document is None
        or document.version_id != scan.last_version_id
        or version is None
        or version.resource_id != resource.id
    ):
        return False, "stale_material_pointer"
    manifest = {
        "page_document_id": scan.last_document_id,
        "page_state": scan.state,
        "page_error": scan.last_error,
        "attachments": bundle.dependencies,
    }
    if bundle.digest != digest(manifest):
        return False, "stale_bundle_manifest"
    try:
        payload = json.loads(blobs.read(bundle.body_ref))
        page = payload["page"]
        if (
            payload["id"] != bundle.id
            or payload["resource_id"] != resource.id
            or payload["source_id"] != resource.source_id
            or page["version_id"] != version.id
        ):
            return False, "blob_material_binding_mismatch"
        # Bundle embeds the parsed document. Verify the independently saved parsed
        # document and original bytes too; do not assume a DB reference is readable.
        json.loads(blobs.read(document.body_ref))
        blobs.read(version.body_ref)
        for dep in bundle.dependencies:
            if not _available_dependency_current(session, dep):
                return False, "stale_dependency"
            if dep.get("document_id"):
                attached = session.get(MaterialDocument, dep["document_id"])
                json.loads(blobs.read(attached.body_ref))
                original = session.get(ResourceVersion, attached.version_id)
                blobs.read(original.body_ref)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, "unreadable_or_invalid_saved_blob"
    return True, None


def snapshot_observations(session, *, blobs, source_id, clock, material_only=False):
    """Read current pointers in an existing transaction; no flush/commit/model calls."""
    if session.new or session.dirty or session.deleted:
        raise ValueError("Snapshot reporting requires a clean read-only session")
    stamp = explicit_clock(clock)
    if session.get(Source, source_id) is None:
        raise ValueError("Unknown source")
    blobs = _SavedBlobs(blobs)
    pipeline = session.get(SourcePipeline, source_id)
    summary = pipeline.summary if pipeline else {}
    classifier = summary.get(
        "effective_classifier_key", summary.get("classification", {}).get("classifier_key")
    )
    binding = session.get(SourceRecipeBinding, source_id)
    recipe = (
        session.get(SourceRecipe, binding.active_recipe_id)
        if (binding and binding.active_recipe_id)
        else None
    )
    active = recipe is not None and recipe.source_id == source_id and recipe.status == "active"
    context = FunnelContext(
        source_id,
        clock,
        classifier,
        recipe.id if active else None,
        binding.generation if binding else 0,
    )
    rows = []
    current_toolkit = toolkit_key() if not material_only else None
    resources = session.scalars(
        select(Resource).where(Resource.source_id == source_id).order_by(Resource.id)
    )
    for resource in resources:
        scan = session.get(ResourceScan, resource.id)
        bundle = session.get(ResourceBundle, scan.bundle_id) if scan and scan.bundle_id else None
        current, reason = _material_current(session, blobs, resource, scan, bundle)
        assessment = (
            session.scalar(
                select(PageAssessment).where(
                    PageAssessment.bundle_id == bundle.id,
                    PageAssessment.classifier_key == classifier,
                )
            )
            if bundle and classifier and current and not material_only
            else None
        )
        label = assessment.result if assessment else {}
        row = FunnelObservation(
            resource_id=resource.id,
            version_id=scan.last_version_id if scan else None,
            document_id=scan.last_document_id if scan else None,
            bundle_id=bundle.id if bundle else None,
            classifier_key=classifier,
            recipe_id=context.recipe_id,
            generation=context.generation,
            material_current=current,
            classified=assessment is not None,
            page_roles=tuple(label.get("page_roles", [])),
            business_type=label.get("business_type", "unknown"),
            classification_review=label.get("requires_review", False),
            state="missing" if current else "stale",
            reasons=(reason,) if reason else (),
        )
        if material_only:
            rows.append(row)
            continue
        if not current or not assessment:
            rows.append(
                replace(
                    row,
                    reasons=row.reasons
                    + (() if assessment else ("missing_current_classification",)),
                )
            )
            continue
        audit = (
            session.scalar(
                select(AuditResult)
                .where(
                    AuditResult.bundle_id == bundle.id,
                    AuditResult.recipe_id == context.recipe_id,
                    AuditResult.generation == context.generation,
                    AuditResult.evaluated_on <= stamp.date().isoformat(),
                )
                .order_by(
                    AuditResult.evaluated_on.desc(),
                    AuditResult.created_at.desc(),
                    AuditResult.id.desc(),
                )
                .limit(1)
            )
            if active
            else None
        )
        if audit is None:
            rows.append(replace(row, state="missing", reasons=("missing_bound_audit",)))
            continue
        path = audit.result.get("execution", {}).get("path")
        quality = (
            ("coarse" if not audit.result.get("positions") else "full")
            if path in {"quick", "detailed"}
            else "unversioned"
        )
        quality = quality if quality in QUALITIES else "unversioned"
        reported = len(audit.result.get("positions", []))
        state, reasons = "accepted", []
        if recipe.validation.get("toolkit_key") != current_toolkit:
            state, reasons = "stale", ["stale_recipe_toolkit"]
        elif recipe.validation.get("errors"):
            state, reasons = "rejected", ["recipe_validation_errors"]
        elif not semantic_current(session, audit) or not temporal_current(session, audit):
            state, reasons = "stale", ["stale_semantic_or_temporal_receipt"]
        elif audit.status == "failed":
            state = "failed"
        elif audit.errors:
            state = "rejected"
        elif label.get("requires_review") or audit.status == "review":
            state = "review"
        elif audit.status == "cancelled":
            state = "cancelled"
        elif audit.status not in {"success", "partial"}:
            state = "rejected"
        elif quality == "coarse":
            state = "coarse"
        dependency_check = (
            _available_dependency_current if audit.status == "partial" else _dependency_current
        )
        if not all(dependency_check(session, dep) for dep in bundle.dependencies):
            state, reasons = "stale", ["stale_dependency"]
        validity = "unknown"
        cancelled = False
        # Aggregate actual position dates, never a notice-routing hypothesis.
        from youju.positions.facts import PositionFacts

        invalid_date_claim = any(
            issue.get("code")
            in {
                "UNSUPPORTED_VALIDITY",
                "UNSUPPORTED_SOURCE_DEADLINE",
                "INVALID_APPLICATION_DATE",
                "PERIOD_DATE",
                "PERIOD_SEMANTICS",
            }
            or str(issue.get("path", "")).startswith("validity")
            or ".deadline_on" in str(issue.get("path", ""))
            for issue in audit.errors
        )
        if invalid_date_claim:
            reasons.append("rejected_temporal_claim")
        job_statuses = []
        for raw in audit.result.get("positions", []):
            try:
                facts = PositionFacts.from_dict(raw)
                job_statuses.append(
                    "unknown"
                    if invalid_date_claim
                    else job_validity(facts, stamp.date(), now_iso=clock)["status"]
                )
            except (TypeError, ValueError, KeyError):
                job_statuses.append("unknown")
        if job_statuses and all(value == "expired" for value in job_statuses):
            validity = "expired"
        elif "not_expired" in job_statuses:
            validity = "not_expired"
        cancelled = audit.status == "cancelled"
        parse = session.get(ParseResult, audit.parse_id) if audit.parse_id else None
        accepted = 0
        if state == "accepted":
            if (
                parse is None
                or parse.version_id != bundle.page_version_id
                or parse.status != "valuable"
                or parse.role != "notice"
            ):
                state, reasons = "missing", ["missing_or_misbound_parse"]
            else:
                accepted = len(
                    list(session.scalars(select(Position.id).where(Position.parse_id == parse.id)))
                )
                if accepted != reported:
                    accepted, state, reasons = 0, "rejected", ["stored_position_count_mismatch"]
        if (
            label.get("business_type") != "recruitment"
            or not set(row.page_roles) & {"article", "attachment"}
            or cancelled
        ):
            accepted = 0
        row = replace(
            row,
            state=state,
            validity=validity if validity in VALIDITIES else "unknown",
            cancelled=cancelled,
            quality=quality,
            reported_positions=reported,
            accepted_positions=accepted,
            routing_category=audit.result.get("routing", {}).get("category", "unknown"),
            position_calendar_statuses=tuple(job_statuses),
            reasons=tuple(reasons),
        )
        rows.append(row)
    if material_only:
        return context, rows
    # Display is independent of active-recipe audit acceptance. Count by resource
    # so readable fallback versions survive stale/missing current observations.
    indexes = {row.resource_id: index for index, row in enumerate(rows)}
    counts = Counter()
    for job in public_jobs(session, day=stamp.date(), now_iso=clock):
        if job["source_id"] != source_id:
            continue
        saved_bundle = session.get(ResourceBundle, job["bundle_id"])
        if saved_bundle.resource_id in indexes:
            counts[indexes[saved_bundle.resource_id]] += 1
    for index, count in counts.items():
        rows[index] = replace(
            rows[index],
            public_positions=count,
            public_positions_with_partial=count,
        )
    return context, rows


def replay_observations(envelope, *, snapshot_rows, source_id, clock):
    """Validate normalized full-source replay coverage against the saved snapshot.

    No snapshot classification/audit labels are copied into replay observations.
    The envelope contains context and observations, both matching dataclass fields.
    """
    if envelope.get("schema") != SCHEMA:
        raise ValueError("Unknown normalized replay schema")
    context = FunnelContext(**envelope["context"])
    if (
        context.mode != "new_contract_replay"
        or context.source_id != source_id
        or explicit_clock(context.clock) != explicit_clock(clock)
    ):
        raise ValueError("Replay source/mode/clock mismatch")
    rows = [FunnelObservation(**r) for r in envelope["observations"]]
    baseline = {r.resource_id: r for r in snapshot_rows}
    if {r.resource_id for r in rows} != set(baseline):
        raise ValueError(
            "Full-source replay must include every snapshot resource, even missing cases"
        )
    for row in rows:
        saved = baseline[row.resource_id]
        if (row.version_id, row.document_id, row.bundle_id) != (
            saved.version_id,
            saved.document_id,
            saved.bundle_id,
        ) or (row.material_current and not saved.material_current):
            raise ValueError("Replay material does not bind the current saved snapshot")
    aggregate_funnel(context, rows)  # Validate before returning an accepted normalized envelope.
    return context, rows


def report_source_funnel(*, snapshot, blobs, source_id, clock, replay=None):
    """Open an explicit SQLite snapshot read-only; return JSON-compatible totals.

    Use a completed consistent SQLite backup, not a concurrently changing live DB.
    query_only and an explicit read transaction protect all ORM/public query reads.
    """
    path = Path(snapshot).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Snapshot must be an existing file")
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Snapshot has pending WAL; use a consistent standalone SQLite backup")
    uri = path.as_uri() + "?mode=ro&immutable=1"

    def connect():
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        connection.execute("PRAGMA query_only=ON")
        return connection

    engine = create_engine("sqlite://", creator=connect)

    @event.listens_for(engine, "begin")
    def begin_read(connection):
        # Begin after pool/dialect initialization, whose initial rollback would
        # otherwise undo a BEGIN issued inside the DBAPI connection creator.
        connection.exec_driver_sql("BEGIN")

    try:
        with Session(engine, autoflush=False) as session:
            context, rows = snapshot_observations(
                session,
                blobs=blobs,
                source_id=source_id,
                clock=clock,
                material_only=replay is not None,
            )
            if replay is not None:
                context, rows = replay_observations(
                    replay, snapshot_rows=rows, source_id=source_id, clock=clock
                )
            report = aggregate_funnel(context, rows)
            report["inputs"] = {"snapshot": str(path), "blobs": str(Path(blobs).resolve())}
            return report
    finally:
        engine.dispose()
