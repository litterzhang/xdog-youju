"""Resource identity, discovery provenance, completed fetches and immutable body snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DDL,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import HTTPURL, Base, Identified, UTCDateTime, enum_column, normalize_url, utc_now
from .enums import DiscoveryMethod, FetchOutcome, ResourceKind, ReviewStatus

if TYPE_CHECKING:
    from .registry import Source


class Resource(Identified, Base):
    """One public URL; an unassigned source is allowed for newly discovered candidates."""

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("id", "source_id"),
        CheckConstraint(
            "last_seen_at IS NULL OR last_seen_at >= first_seen_at", name="observation_order"
        ),
        CheckConstraint("consecutive_failures >= 0", name="nonnegative_failures"),
    )

    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    original_url: Mapped[str] = mapped_column(HTTPURL())
    normalized_url: Mapped[str] = mapped_column(HTTPURL(), unique=True)
    kind: Mapped[ResourceKind] = mapped_column(
        enum_column(ResourceKind, "resource_kind"), default=ResourceKind.UNKNOWN
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "resource_review_status"), default=ReviewStatus.PENDING
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_fetch_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    consecutive_failures: Mapped[int] = mapped_column(default=0)

    source: Mapped[Source | None] = relationship(back_populates="resources")
    versions: Mapped[list[ResourceVersion]] = relationship(
        back_populates="resource", passive_deletes="all"
    )
    fetch_attempts: Mapped[list[FetchAttempt]] = relationship(
        back_populates="resource", passive_deletes="all"
    )

    def __init__(self, *, original_url: str, **kwargs):
        if "normalized_url" in kwargs:
            raise TypeError("normalized_url is derived from original_url")
        super().__init__(
            original_url=original_url, normalized_url=normalize_url(original_url), **kwargs
        )


class ResourceVersion(Identified, Base):
    """A distinct body for one URL. Repeated fetches can refer to the same snapshot."""

    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("id", "resource_id"),
        UniqueConstraint("resource_id", "content_sha256"),
        CheckConstraint(
            "length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="sha256_hex",
        ),
        CheckConstraint("length(trim(body_ref)) > 0", name="body_ref_not_blank"),
        CheckConstraint("byte_length >= 0", name="nonnegative_body_length"),
    )

    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), index=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    body_ref: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(128))
    byte_length: Mapped[int]
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    resource: Mapped[Resource] = relationship(back_populates="versions")


class Discovery(Identified, Base):
    """A syntactic link, not proof that one announcement corrects another."""

    __tablename__ = "discoveries"
    __table_args__ = (
        UniqueConstraint("parent_version_id", "target_resource_id", "method", "raw_url"),
        CheckConstraint("length(trim(raw_url)) > 0", name="raw_url_not_blank"),
    )

    parent_version_id: Mapped[str] = mapped_column(
        ForeignKey("resource_versions.id", ondelete="RESTRICT"), index=True
    )
    target_resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), index=True
    )
    method: Mapped[DiscoveryMethod] = mapped_column(
        enum_column(DiscoveryMethod, "discovery_method")
    )
    raw_url: Mapped[str] = mapped_column(Text)
    link_text: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    parent_version: Mapped[ResourceVersion] = relationship()
    target_resource: Mapped[Resource] = relationship()


class FetchAttempt(Identified, Base):
    """A completed request. Failed fetches must not point at a successfully verified version."""

    __tablename__ = "fetch_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "resource_id"],
            ["resource_versions.id", "resource_versions.resource_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("finished_at >= started_at", name="request_time_order"),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599", name="http_status"
        ),
        CheckConstraint(
            "(outcome = 'downloaded' AND http_status IS NOT NULL "
            "AND http_status BETWEEN 200 AND 299 "
            "AND version_id IS NOT NULL AND error_code IS NULL) "
            "OR (outcome = 'not_modified' AND http_status IS NOT NULL "
            "AND http_status = 304 AND version_id IS NOT NULL AND error_code IS NULL) "
            "OR (outcome = 'failed' AND version_id IS NULL AND error_code IS NOT NULL "
            "AND length(trim(error_code)) > 0)",
            name="outcome_consistency",
        ),
    )

    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), index=True
    )
    version_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime())
    outcome: Mapped[FetchOutcome] = mapped_column(enum_column(FetchOutcome, "fetch_outcome"))
    http_status: Mapped[int | None]
    final_url: Mapped[str | None] = mapped_column(HTTPURL())
    error_code: Mapped[str | None] = mapped_column(String(128))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)

    resource: Mapped[Resource] = relationship(back_populates="fetch_attempts")
    version: Mapped[ResourceVersion | None] = relationship(
        primaryjoin=(
            "and_(FetchAttempt.version_id == ResourceVersion.id, "
            "FetchAttempt.resource_id == ResourceVersion.resource_id)"
        ),
        foreign_keys=[version_id],
    )


# SQLite enforces snapshot immutability even for bulk SQL, not just ORM attribute writes.
for operation in ("UPDATE", "DELETE"):
    event.listen(
        ResourceVersion.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER resource_versions_no_{operation.lower()} BEFORE {operation} "
            "ON resource_versions BEGIN "
            "SELECT RAISE(ABORT, 'Resource versions are immutable'); END"
        ).execute_if(dialect="sqlite"),
    )

event.listen(
    Resource.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER resources_preserve_url BEFORE UPDATE OF original_url, normalized_url "
        "ON resources WHEN NEW.original_url <> OLD.original_url "
        "OR NEW.normalized_url <> OLD.normalized_url BEGIN "
        "SELECT RAISE(ABORT, 'Register a new resource when its URL changes'); END"
    ).execute_if(dialect="sqlite"),
)
