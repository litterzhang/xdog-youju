"""Private service metadata; never creates or opens source/business tables."""

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True)
    physical_path: Mapped[str] = mapped_column(String(2048))
    owner_id: Mapped[str] = mapped_column(String(128), default="local", server_default="local")
    lifecycle: Mapped[str] = mapped_column(String(16), default="managed", server_default="managed")
    status: Mapped[str] = mapped_column(
        String(24), default="waiting_input", server_default="waiting_input"
    )
    agent_profile: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    profile_revision: Mapped[str | None] = mapped_column(String(64))
    init_contract_digest: Mapped[str | None] = mapped_column(String(64))
    init_task_id: Mapped[str | None] = mapped_column(String(32))
    checkpoint_ref: Mapped[str | None] = mapped_column(String(64))
    prior_context_ref: Mapped[str | None] = mapped_column(String(64))
    session_revision: Mapped[int] = mapped_column(default=0, server_default="0")
    latest_candidate_id: Mapped[str | None] = mapped_column(String(32))
    accepted_revision_id: Mapped[str | None] = mapped_column(String(32))
    latest_revision_id: Mapped[str | None] = mapped_column(String(32))
    accepted_recipe_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    input_digest: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON)
    result_ref: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    owner: Mapped[str | None] = mapped_column(String(32))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24), default="build", server_default="build")
    input_revision_id: Mapped[str | None] = mapped_column(ForeignKey("input_revisions.id"))
    depends_on: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    prompt_ref: Mapped[str | None] = mapped_column(String(64))
    request_digest: Mapped[str | None] = mapped_column(String(64))
    callback_target: Mapped[str | None] = mapped_column(String(128))
    base_session_revision: Mapped[int] = mapped_column(default=0, server_default="0")
    checkpoint_ref: Mapped[str | None] = mapped_column(String(64))
    recovery_ref: Mapped[str | None] = mapped_column(String(64))
    base_recipe_digest: Mapped[str | None] = mapped_column(String(64))
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("recipe_revisions.id"))
    base_revision_id: Mapped[str | None] = mapped_column(ForeignKey("recipe_revisions.id"))
    accepted_base_revision_id: Mapped[str | None] = mapped_column(String(32))
    artifact_digest: Mapped[str | None] = mapped_column(String(64))
    admission: Mapped[str | None] = mapped_column(String(16))


class Event(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)


class InputRevision(Base):
    __tablename__ = "input_revisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "revision"),
        UniqueConstraint("workspace_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    revision: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    init_task_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(40))


class Outbox(Base):
    __tablename__ = "callback_outbox"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[float] = mapped_column(default=0.0)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_expires_at: Mapped[float | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))


class RecipeRevision(Base):
    """Append-only publication of saved artifacts, never mutable execution state."""

    __tablename__ = "recipe_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    input_revision_id: Mapped[str] = mapped_column(ForeignKey("input_revisions.id"))
    input_digest: Mapped[str] = mapped_column(String(64))
    profile_revision: Mapped[str] = mapped_column(String(64))
    agent_profile: Mapped[dict] = mapped_column(JSON)
    base_revision_id: Mapped[str | None] = mapped_column(ForeignKey("recipe_revisions.id"))
    base_recipe_digest: Mapped[str | None] = mapped_column(String(64))
    accepted_base_revision_id: Mapped[str | None] = mapped_column(String(32))
    accepted_base_recipe_digest: Mapped[str | None] = mapped_column(String(64))
    artifact_digest: Mapped[str] = mapped_column(String(64))
    artifact_ref: Mapped[str] = mapped_column(String(64))
    sample_validation: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))


class RevisionReview(Base):
    """Trusted owner's terminal audit decision, independent of the authoring job."""

    __tablename__ = "revision_reviews"
    __table_args__ = (UniqueConstraint("workspace_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("recipe_revisions.id"), unique=True)
    request_id: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    audit_ref: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
