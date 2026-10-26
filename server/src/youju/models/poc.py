"""POC tables: scope, leased queue, capture provenance and tagged extracted positions."""

from copy import deepcopy
from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from youju.positions.facts import PositionFacts

from .base import Base, Identified, UTCDateTime, utc_now


class OrganizationScope(Base):
    __tablename__ = "organization_scopes"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    root_domain: Mapped[str] = mapped_column(String(253))


class Capture(Identified, Base):
    """A version can be observed repeatedly; method and original response belong to each capture."""

    __tablename__ = "captures"
    __table_args__ = (
        CheckConstraint("mode IN ('static', 'dynamic', 'headed')", name="capture_mode"),
        CheckConstraint("html_kind IN ('response_html', 'rendered_dom')", name="html_kind"),
    )
    attempt_id: Mapped[str] = mapped_column(ForeignKey("fetch_attempts.id"), unique=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("resource_versions.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    html_kind: Mapped[str] = mapped_column(String(24))
    response_body_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class ParseResult(Identified, Base):
    __tablename__ = "parse_results"
    __table_args__ = (
        UniqueConstraint("version_id", "parser_key"),
        CheckConstraint(
            "status IN ('valuable', 'empty', 'review', 'cancelled')", name="parse_status"
        ),
    )
    version_id: Mapped[str] = mapped_column(ForeignKey("resource_versions.id"), index=True)
    parser_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class Position(Identified, Base):
    """An immutable extracted position with tagged fields and independent conditions."""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("parse_id", "ordinal"),
        CheckConstraint("ordinal > 0", name="positive_ordinal"),
    )
    parse_id: Mapped[str] = mapped_column(ForeignKey("parse_results.id"), index=True)
    ordinal: Mapped[int]
    facts: Mapped[dict] = mapped_column(JSON)
    deadline_on: Mapped[date | None] = mapped_column(Date, index=True)
    application_start_on: Mapped[date | None] = mapped_column(Date, index=True)
    # Optional historical/source locators, not a mandatory per-field output contract.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(24), default="needs_review")

    @validates("facts")
    def validate_facts(self, key, value):
        facts = PositionFacts.from_dict(value)
        if facts.fields["title"].value is None:
            raise ValueError("An extracted position requires a known title")
        for name in ("deadline_on", "application_start_on"):
            fact = facts.fields[name]
            # Only resolved source dates feed exact database facets. Inferred
            # candidates remain in JSON with their provenance, never a fake date.
            setattr(
                self,
                name,
                date.fromisoformat(fact.value)
                if fact.tag == "source" and fact.value is not None
                else None,
            )
        return deepcopy(facts.as_dict())


class CrawlJob(Base):
    """One durable queue slot per URL; FetchAttempt retains the attempt history."""

    __tablename__ = "crawl_jobs"
    __table_args__ = (
        CheckConstraint("mode IN ('static', 'dynamic')", name="automatic_modes"),
        CheckConstraint("state IN ('ready', 'running', 'done', 'review')", name="job_state"),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
    )
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="static")
    state: Mapped[str] = mapped_column(String(16), default="ready", index=True)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(128))
    last_capture_id: Mapped[str | None] = mapped_column(ForeignKey("captures.id"))
    last_parse_id: Mapped[str | None] = mapped_column(ForeignKey("parse_results.id"))
