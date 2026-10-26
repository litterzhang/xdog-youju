"""Three focused SQLite integration tests; no network, mocks or external services."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    Discovery,
    DiscoveryMethod,
    FetchAttempt,
    FetchOutcome,
    Organization,
    OrganizationType,
    Region,
    RegistryStatus,
    Resource,
    ResourceVersion,
    Seed,
    SeedKind,
    Source,
)

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    engine = create_sqlite_engine(tmp_path / "test.sqlite3")
    initialize_database(engine)
    yield engine
    engine.dispose()


def snapshot(resource, content):
    digest = sha256(content).hexdigest()
    return ResourceVersion(
        resource=resource,
        content_sha256=digest,
        body_ref=f"synthetic/{digest}",
        byte_length=len(content),
        media_type="text/html",
        captured_at=NOW,
    )


def test_chinese_registry_and_separate_region_tags_round_trip(engine):
    with Session(engine) as session, session.begin():
        anhui = Region(code="anhui", name="安徽省")
        jiangsu = Region(code="jiangsu", name="江苏省")
        organization = Organization(
            id="ustc",
            name="中国科学技术大学",
            short_name="中国科大",
            organization_type=OrganizationType.UNIVERSITY,
            location=anhui,
            created_at=NOW,
        )
        source = Source(
            id="recruitment",
            name="示例招聘渠道",
            publisher=organization,
            base_url="https://example.test/",
            region_tags=[anhui, jiangsu],
        )
        resource = Resource(
            original_url="https://EXAMPLE.test:443/notices?signtype=51#top",
            source=source,
        )
        session.add(
            Seed(
                id="notices",
                name="最新招聘公告",
                source=source,
                resource=resource,
                kind=SeedKind.LISTING,
            )
        )

    with Session(engine) as session:
        source = session.get(Source, "recruitment")
        seed = session.get(Seed, "notices")
        assert source.publisher.name == "中国科学技术大学"
        assert source.publisher.location.name == "安徽省"
        assert {region.name for region in source.region_tags} == {"安徽省", "江苏省"}
        assert source.publisher.created_at == NOW
        assert source.status == seed.status == RegistryStatus.CANDIDATE
        assert source.verified_at is None
        assert seed.resource.normalized_url == "https://example.test/notices?signtype=51"
        assert seed.resource.original_url.endswith("#top")


def test_database_rejects_broken_associations_and_unverified_activation(engine):
    with Session(engine) as session, session.begin():
        source = Source(id="first", name="来源一", base_url="https://one.example.test/")
        other = Source(id="second", name="来源二", base_url="https://two.example.test/")
        resource = Resource(original_url="https://one.example.test/list", source=source)
        version = snapshot(resource, b"synthetic")
        unrelated = Resource(original_url="https://two.example.test/page", source=other)
        session.add_all([version, unrelated])
        session.flush()

        # A source can only seed a resource assigned to that same source.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Seed(name="错误入口", source=other, resource=resource, kind=SeedKind.LISTING)
            )
            session.flush()

        # A fetch cannot claim evidence from a different resource.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                FetchAttempt(
                    resource=unrelated,
                    version=version,
                    started_at=NOW,
                    finished_at=NOW,
                    outcome=FetchOutcome.DOWNLOADED,
                    http_status=200,
                )
            )
            session.flush()

        with pytest.raises(IntegrityError), session.begin_nested():
            source.status = RegistryStatus.ACTIVE
            session.flush()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(Organization(name="孤立机构", parent_id="missing-organization"))
            session.flush()


def test_fetch_history_preserves_versions_and_discovery_provenance(engine):
    with Session(engine) as session, session.begin():
        page = Resource(original_url="https://example.test/notice?sign=1")
        attachment = Resource(original_url="https://example.test/岗位表.xlsx")
        original = snapshot(page, b"original")
        changed = snapshot(page, b"corrected")
        session.add_all([original, changed, attachment])
        for parent in (original, changed):
            session.add(
                Discovery(
                    parent_version=parent,
                    target_resource=attachment,
                    method=DiscoveryMethod.LINK,
                    raw_url="/岗位表.xlsx",
                    link_text="岗位表",
                )
            )
        for outcome, status, version, error in (
            (FetchOutcome.DOWNLOADED, 200, original, None),
            (FetchOutcome.NOT_MODIFIED, 304, original, None),
            (FetchOutcome.FAILED, None, None, "timeout"),
            (FetchOutcome.DOWNLOADED, 200, changed, None),
        ):
            session.add(
                FetchAttempt(
                    resource=page,
                    version=version,
                    outcome=outcome,
                    http_status=status,
                    error_code=error,
                    started_at=NOW,
                    finished_at=NOW,
                )
            )
        session.flush()
        assert session.scalar(select(func.count()).select_from(ResourceVersion)) == 2
        assert session.scalar(select(func.count()).select_from(FetchAttempt)) == 4
        assert session.scalar(select(func.count()).select_from(Discovery)) == 2
        failure = session.scalar(
            select(FetchAttempt).where(FetchAttempt.outcome == FetchOutcome.FAILED)
        )
        assert failure.version is None
        assert attachment.source is None  # A discovered link does not approve a new source.

        # Database triggers protect snapshots even when ORM attribute hooks are bypassed.
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                update(ResourceVersion)
                .where(ResourceVersion.id == original.id)
                .values(
                    body_ref="synthetic/overwritten",
                )
            )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(delete(ResourceVersion).where(ResourceVersion.id == changed.id))
