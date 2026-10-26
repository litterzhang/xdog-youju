"""Organizations, geographic reference data, publication channels and crawl entry points."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import HTTPURL, Base, Identified, UTCDateTime, enum_column, utc_now
from .enums import OrganizationType, RegistryStatus, SeedKind

if TYPE_CHECKING:
    from .crawl import Resource


source_regions = Table(
    "source_regions",
    Base.metadata,
    Column("source_id", ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True),
    Column("region_code", ForeignKey("regions.code", ondelete="RESTRICT"), primary_key=True),
)


class Region(Base):
    __tablename__ = "regions"
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="code_not_blank"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("parent_code IS NULL OR parent_code <> code", name="not_own_parent"),
    )

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_code: Mapped[str | None] = mapped_column(ForeignKey("regions.code", ondelete="RESTRICT"))
    parent: Mapped[Region | None] = relationship(remote_side=[code])


class Organization(Identified, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    name: Mapped[str] = mapped_column(String(256))
    short_name: Mapped[str | None] = mapped_column(String(128))
    organization_type: Mapped[OrganizationType] = mapped_column(
        enum_column(OrganizationType, "organization_type"), default=OrganizationType.UNKNOWN
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    location_region_code: Mapped[str | None] = mapped_column(
        ForeignKey("regions.code", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    parent: Mapped[Organization | None] = relationship(remote_side="Organization.id")
    location: Mapped[Region | None] = relationship()


class Source(Identified, Base):
    """A publication channel. Publisher identity and region tags do not establish job conditions."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint(
            "status <> 'active' OR (verified_at IS NOT NULL AND authority_evidence IS NOT NULL "
            "AND length(trim(authority_evidence)) > 0)",
            name="active_requires_verification",
        ),
    )

    name: Mapped[str] = mapped_column(String(256))
    publisher_organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    base_url: Mapped[str] = mapped_column(HTTPURL())
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[RegistryStatus] = mapped_column(
        enum_column(RegistryStatus, "source_status"), default=RegistryStatus.CANDIDATE
    )
    authority_evidence: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    publisher: Mapped[Organization | None] = relationship()
    region_tags: Mapped[list[Region]] = relationship(secondary=source_regions)
    resources: Mapped[list[Resource]] = relationship(back_populates="source", passive_deletes="all")
    seeds: Mapped[list[Seed]] = relationship(back_populates="source", passive_deletes="all")


class Seed(Identified, Base):
    """A scheduled discovery entry point; its URL belongs to the linked Resource."""

    __tablename__ = "seeds"
    __table_args__ = (
        UniqueConstraint("source_id", "resource_id"),
        ForeignKeyConstraint(
            ["resource_id", "source_id"],
            ["resources.id", "resources.source_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
        CheckConstraint("refresh_interval_seconds > 0", name="positive_refresh_interval"),
    )

    name: Mapped[str] = mapped_column(String(256))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    resource_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[SeedKind] = mapped_column(enum_column(SeedKind, "seed_kind"))
    status: Mapped[RegistryStatus] = mapped_column(
        enum_column(RegistryStatus, "seed_status"), default=RegistryStatus.CANDIDATE
    )
    discovery_rule: Mapped[str | None] = mapped_column(String(128))
    refresh_interval_seconds: Mapped[int] = mapped_column(default=3600)

    source: Mapped[Source] = relationship(back_populates="seeds", foreign_keys=[source_id])
    resource: Mapped[Resource] = relationship(
        primaryjoin="and_(Seed.resource_id == Resource.id, Seed.source_id == Resource.source_id)",
        foreign_keys=[resource_id],
    )
