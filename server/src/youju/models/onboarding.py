"""Source lifecycle records and explicit local Recipe service bindings."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Identified, UTCDateTime, utc_now


class SourcePipeline(Base):
    __tablename__ = "source_pipelines"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('inventory','classification','authoring','audit','active')",
            name="pipeline_stage",
        ),
        CheckConstraint("state IN ('ready','running','blocked')", name="pipeline_state"),
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(24), default="inventory")
    state: Mapped[str] = mapped_column(String(16), default="ready")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SourceRecipeBinding(Base):
    """One source's mutable recipe pointers; service identifiers are opaque, not FKs.

    Generation is the publication epoch, independent of onboarding pipeline config.
    Nullable service IDs allow offline artifacts without invented service provenance.
    """

    __tablename__ = "source_recipe_bindings"
    __table_args__ = (CheckConstraint("generation >= 0", name="binding_generation"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    candidate_recipe_id: Mapped[str | None] = mapped_column(ForeignKey("source_recipes.id"))
    active_recipe_id: Mapped[str | None] = mapped_column(ForeignKey("source_recipes.id"))
    candidate_revision_id: Mapped[str | None] = mapped_column(String(64))
    active_revision_id: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ResourceScan(Base):
    """Acquisition work slot. ScanPolicy controls recurrence; this slot controls attempts."""

    __tablename__ = "resource_scans"
    __table_args__ = (
        CheckConstraint("mode IN ('static','dynamic')", name="scan_mode"),
        CheckConstraint("state IN ('ready','running','done','review')", name="scan_state"),
        CheckConstraint("depth >= 0 AND attempts >= 0", name="scan_counters"),
    )
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="ready", index=True)
    mode: Mapped[str] = mapped_column(String(16), default="static")
    depth: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_version_id: Mapped[str | None] = mapped_column(ForeignKey("resource_versions.id"))
    last_document_id: Mapped[str | None] = mapped_column(ForeignKey("material_documents.id"))
    last_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("fetch_attempts.id"))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(String(128))
    bundle_id: Mapped[str | None] = mapped_column(ForeignKey("resource_bundles.id"))


class MaterialDocument(Identified, Base):
    __tablename__ = "material_documents"
    __table_args__ = (UniqueConstraint("version_id", "parser_key", "body_ref"),)
    version_id: Mapped[str] = mapped_column(ForeignKey("resource_versions.id"), index=True)
    parser_key: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    body_ref: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ResourceBundle(Identified, Base):
    __tablename__ = "resource_bundles"
    __table_args__ = (UniqueConstraint("resource_id", "digest"),)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    page_version_id: Mapped[str] = mapped_column(ForeignKey("resource_versions.id"))
    digest: Mapped[str] = mapped_column(String(64))
    body_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    dependencies: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class PageAssessment(Identified, Base):
    __tablename__ = "page_assessments"
    __table_args__ = (UniqueConstraint("bundle_id", "classifier_key"),)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"), index=True)
    classifier_key: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ScanPolicy(Base):
    __tablename__ = "scan_policies"
    __table_args__ = (CheckConstraint("cadence IN ('none','daily','weekly')", name="scan_cadence"),)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("page_assessments.id"))
    cadence: Mapped[str] = mapped_column(String(16))
    next_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    reason: Mapped[str] = mapped_column(Text)


class SourceRecipe(Identified, Base):
    __tablename__ = "source_recipes"
    __table_args__ = (
        UniqueConstraint("source_id", "digest"),
        CheckConstraint(
            "status IN ('candidate','validated','rejected','active','retired')",
            name="recipe_status",
        ),
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    revision_id: Mapped[str | None] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    body_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    samples: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AuditRun(Identified, Base):
    __tablename__ = "source_audits"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("source_recipes.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="running")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditResult(Identified, Base):
    """Results are keyed by material dependencies, recipe and explicit evaluation day."""

    __tablename__ = "announcement_audits"
    __table_args__ = (UniqueConstraint("bundle_id", "recipe_id", "evaluated_on", "generation"),)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"), index=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("source_recipes.id"), index=True)
    evaluated_on: Mapped[str] = mapped_column(String(10))
    generation: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    parse_id: Mapped[str | None] = mapped_column(ForeignKey("parse_results.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class OnboardingResult(Base):
    """Immutable first admitted baseline; maintenance and monitoring never overwrite it."""

    __tablename__ = "onboarding_results"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("source_recipes.id"))
    audit_id: Mapped[str] = mapped_column(ForeignKey("source_audits.id"))
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    summary: Mapped[dict] = mapped_column(JSON)
    acquisition: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[str] = mapped_column(String(48), default="activation")


class IncrementalControl(Base):
    __tablename__ = "incremental_controls"
    __table_args__ = (CheckConstraint("state IN ('idle','blocked')", name="incremental_state"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    state: Mapped[str] = mapped_column(String(16), default="idle")
    error: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class IncrementalRun(Identified, Base):
    __tablename__ = "incremental_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','complete','blocked')", name="incremental_run_status"
        ),
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    audit_id: Mapped[str | None] = mapped_column(ForeignKey("source_audits.id"))
    # Private comparison state is never exposed by HTTP.
    baseline: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class RepairRequest(Identified, Base):
    """Durable incremental repair intent; never changes baseline admission while building."""

    __tablename__ = "recipe_repair_requests"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    base_recipe_id: Mapped[str] = mapped_column(ForeignKey("source_recipes.id"))
    base_recipe_digest: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32), default="incremental_parse_failure")
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    classifier_key: Mapped[str] = mapped_column(String(64))
    toolkit_key: Mapped[str] = mapped_column(String(64))
    feedback_ref: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    candidate_recipe_id: Mapped[str | None] = mapped_column(ForeignKey("source_recipes.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','preparing','submitted','auditing','accepting',"
            "'rejecting','succeeded','failed','superseded')",
            name="repair_request_status",
        ),
    )


class RepairIncident(Identified, Base):
    """One attempt per failed material/base/profile fingerprint, including failed repairs."""

    __tablename__ = "recipe_repair_incidents"
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("recipe_repair_requests.id"), index=True)
    incremental_run_id: Mapped[str] = mapped_column(ForeignKey("incremental_runs.id"))
    bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"))
    audit_result_id: Mapped[str] = mapped_column(ForeignKey("announcement_audits.id"))


class ApplicationRuleReceipt(Base):
    """Immutable extracted rule identity; time evaluation is deliberately not cached."""

    __tablename__ = "application_rule_receipts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_ref: Mapped[str] = mapped_column(String(64))
    evaluator_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SemanticCaseLabel(Base):
    """Append-only host-reviewed expectations pinned to an immutable material bundle."""

    __tablename__ = "semantic_case_labels"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"), index=True)
    bundle_digest: Mapped[str] = mapped_column(String(64))
    expected_ref: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(128))
    split: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    __table_args__ = (
        CheckConstraint("split IN ('regression','holdout')", name="semantic_case_split"),
    )


class SemanticAuditCheck(Base):
    """Independent check receipt, separate from immutable extraction/audit output."""

    __tablename__ = "semantic_audit_checks"
    audit_id: Mapped[str] = mapped_column(ForeignKey("announcement_audits.id"), primary_key=True)
    label_id: Mapped[str] = mapped_column(ForeignKey("semantic_case_labels.id"), primary_key=True)
    checker_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SemanticCaseExposure(Base):
    """Once exposed, a material cannot become an independent holdout by relabeling it."""

    __tablename__ = "semantic_case_exposures"
    label_id: Mapped[str] = mapped_column(ForeignKey("semantic_case_labels.id"), primary_key=True)
    context_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class SourceCalendarReview(Base):
    """Append-only source policy; models cannot supply or replace workday calendars."""

    __tablename__ = "source_calendar_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    body_ref: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(128))
    reference: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class AnnouncementChangeReview(Base):
    """Explicit target/event association; newer timestamps alone never establish precedence."""

    __tablename__ = "announcement_change_reviews"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    target_resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    target_bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"))
    event_resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"))
    event_bundle_id: Mapped[str] = mapped_column(ForeignKey("resource_bundles.id"))
    effect: Mapped[str] = mapped_column(String(16))
    supersedes: Mapped[str | None] = mapped_column(ForeignKey("announcement_change_reviews.id"))
    reviewer: Mapped[str] = mapped_column(String(128))
    reference: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    __table_args__ = (
        CheckConstraint("effect IN ('extension','cancellation')", name="change_effect"),
    )
