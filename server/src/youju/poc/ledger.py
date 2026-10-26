"""Short, serialized SQLite transactions for the single-process POC worker."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from youju.crawlers import CrawlResponse
from youju.crawlers.policy import origin
from youju.models import (
    Capture,
    CrawlJob,
    Discovery,
    DiscoveryMethod,
    FetchAttempt,
    FetchOutcome,
    OrganizationScope,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceKind,
    ResourceVersion,
    ReviewStatus,
    Source,
    SourcePipeline,
)
from youju.positions.parsing import table_row_facts

from .parsing import PARSER_KEY, ParsedPage
from .scope import DiscoveryScope


def now():
    return datetime.now(UTC)


@dataclass(frozen=True)
class Claim:
    resource_id: str
    source_id: str
    url: str
    mode: str
    token: str
    started_at: datetime
    scope: DiscoveryScope
    approved_origin: str


class Ledger:
    def __init__(self, engine):
        self.engine = engine
        self.lock = RLock()

    def approve(self, source_id: str, root_domain: str, evidence: str) -> None:
        scope = DiscoveryScope(root_domain)
        if not evidence.strip():
            raise ValueError("Source approval requires evidence")
        with self.lock, Session(self.engine) as session, session.begin():
            source = session.get(Source, source_id)
            if source is None or source.publisher_organization_id is None:
                raise ValueError("A registered source with a publisher is required")
            if not scope.contains(source.base_url):
                raise ValueError("Source is outside the organization root domain")
            existing = session.get(OrganizationScope, source.publisher_organization_id)
            if existing and existing.root_domain != scope.root_domain:
                raise ValueError("Changing an organization's discovery scope requires review")
            if not existing:
                session.add(
                    OrganizationScope(
                        organization_id=source.publisher_organization_id,
                        root_domain=scope.root_domain,
                    )
                )
            allowed = origin(httpx.URL(source.base_url))
            for seed in source.seeds:
                if (
                    not scope.contains(seed.resource.original_url)
                    or origin(httpx.URL(seed.resource.original_url)) != allowed
                ):
                    raise ValueError("A seed is outside the approved origin or domain")
                if seed.status == RegistryStatus.CANDIDATE:
                    seed.status = RegistryStatus.ACTIVE
                if seed.resource.review_status == ReviewStatus.PENDING:
                    seed.resource.review_status = ReviewStatus.APPROVED
            source.status = RegistryStatus.ACTIVE
            source.verified_at = now()
            source.authority_evidence = evidence

    @staticmethod
    def _scope(session, source: Source) -> DiscoveryScope:
        if session.get(SourcePipeline, source.id) is not None:
            raise ValueError("This source is owned by onboarding; use source-step/source-tick")
        registered = session.get(OrganizationScope, source.publisher_organization_id)
        if source.status != RegistryStatus.ACTIVE or registered is None:
            raise ValueError("Approve this source and its organization scope before crawling")
        return DiscoveryScope(registered.root_domain)

    def enqueue(self, source_id: str, revisit: bool = False) -> int:
        with self.lock, Session(self.engine) as session, session.begin():
            source = session.get(Source, source_id)
            if source is None:
                raise ValueError("Unknown source")
            scope = self._scope(session, source)
            if revisit:
                resources = session.scalars(
                    select(Resource).join(CrawlJob).where(Resource.source_id == source_id)
                ).all()
            else:
                resources = [
                    seed.resource for seed in source.seeds if seed.status == RegistryStatus.ACTIVE
                ]
            count = 0
            for resource in resources:
                if resource.review_status != ReviewStatus.APPROVED or not scope.contains(
                    resource.original_url
                ):
                    continue
                job = session.get(CrawlJob, resource.id)
                if job is None:
                    session.add(CrawlJob(resource_id=resource.id))
                    count += 1
                elif job.state == "done":
                    job.state, job.available_at = "ready", now()
                    count += 1
            return count

    def claim(self, source_id: str, excluded: set[str], timeout: float) -> Claim | None:
        with self.lock, Session(self.engine) as session, session.begin():
            source = session.get(Source, source_id)
            if source is None:
                raise ValueError("Unknown source")
            scope = self._scope(session, source)
            stamp = now()
            due = or_(
                (CrawlJob.state == "ready") & (CrawlJob.available_at <= stamp),
                (CrawlJob.state == "running") & (CrawlJob.lease_expires_at <= stamp),
            )
            statement = (
                select(CrawlJob)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.review_status == ReviewStatus.APPROVED,
                    due,
                    CrawlJob.resource_id.not_in(excluded),
                )
                .order_by(
                    (CrawlJob.mode == "dynamic").desc(),
                    CrawlJob.available_at,
                    Resource.first_seen_at,
                )
            )
            for job in session.scalars(statement):
                resource = session.get(Resource, job.resource_id)
                if not scope.contains(resource.original_url) or origin(
                    httpx.URL(resource.original_url)
                ) != origin(httpx.URL(source.base_url)):
                    job.state, job.last_error = "review", "outside_scope"
                    continue
                token = uuid4().hex
                changed = session.execute(
                    update(CrawlJob)
                    .where(
                        CrawlJob.resource_id == job.resource_id,
                        due,
                    )
                    .values(
                        state="running",
                        lease_token=token,
                        lease_expires_at=stamp + timedelta(seconds=timeout + 60),
                        attempts=CrawlJob.attempts + 1,
                    )
                )
                if changed.rowcount:
                    return Claim(
                        resource.id,
                        source.id,
                        resource.original_url,
                        job.mode,
                        token,
                        stamp,
                        scope,
                        origin(httpx.URL(source.base_url)),
                    )
            return None

    @staticmethod
    def _owned(session, claim: Claim) -> CrawlJob | None:
        job = session.get(CrawlJob, claim.resource_id)
        if job is None or job.state != "running" or job.lease_token != claim.token:
            return None
        return job

    @staticmethod
    def _release(job, claim, error, retry_delay, terminal=False):
        job.lease_token = job.lease_expires_at = None
        job.last_error = error
        if not terminal and claim.mode == "static":
            job.mode, job.state = "dynamic", "ready"
            job.available_at = now() + timedelta(seconds=retry_delay)
        else:
            job.state = "review"

    def fail(self, claim: Claim, code: str, retry_delay: float, terminal=False) -> dict:
        with self.lock, Session(self.engine) as session, session.begin():
            job = self._owned(session, claim)
            if job is None:
                return {"state": "stale", "positions": 0}
            session.add(
                FetchAttempt(
                    resource_id=claim.resource_id,
                    started_at=claim.started_at,
                    finished_at=now(),
                    outcome=FetchOutcome.FAILED,
                    error_code=code,
                )
            )
            resource = session.get(Resource, claim.resource_id)
            resource.consecutive_failures += 1
            job.last_capture_id = job.last_parse_id = None
            self._release(job, claim, code, retry_delay, terminal=terminal)
            return {"state": job.state, "positions": 0}

    def complete(
        self,
        claim: Claim,
        response: CrawlResponse,
        snapshot_ref: str,
        response_ref: str | None,
        parsed: ParsedPage | None,
        retry_delay: float,
    ) -> dict:
        with self.lock, Session(self.engine) as session, session.begin():
            job = self._owned(session, claim)
            if job is None:
                return {"state": "stale", "positions": 0}
            resource = session.get(Resource, claim.resource_id)
            version = session.scalar(
                select(ResourceVersion).where(
                    ResourceVersion.resource_id == resource.id,
                    ResourceVersion.content_sha256 == snapshot_ref,
                )
            )
            if version is None:
                version = ResourceVersion(
                    resource=resource,
                    content_sha256=snapshot_ref,
                    body_ref=snapshot_ref,
                    byte_length=len(response.html.encode("utf-8")),
                    media_type="text/html; charset=utf-8",
                    captured_at=response.captured_at,
                )
                session.add(version)
                session.flush()
            success = response.http_status is not None and 200 <= response.http_status < 300
            attempt = FetchAttempt(
                resource=resource,
                version=version if success else None,
                started_at=claim.started_at,
                finished_at=now(),
                http_status=response.http_status,
                final_url=response.final_url,
                outcome=FetchOutcome.DOWNLOADED if success else FetchOutcome.FAILED,
                error_code=None if success else f"http_{response.http_status or 'unknown'}",
            )
            session.add(attempt)
            session.flush()
            capture = Capture(
                attempt_id=attempt.id,
                version_id=version.id,
                mode=claim.mode,
                html_kind=response.html_kind.value,
                response_body_ref=response_ref,
            )
            session.add(capture)
            session.flush()
            job.last_capture_id, job.last_parse_id = capture.id, None
            resource.last_seen_at = now()
            resource.consecutive_failures = 0 if success else resource.consecutive_failures + 1
            if not success:
                self._release(job, claim, attempt.error_code, retry_delay)
                return {"state": job.state, "positions": 0}
            if parsed is None:
                raise ValueError("Successful HTML requires a parse result")
            parse = session.scalar(
                select(ParseResult).where(
                    ParseResult.version_id == version.id,
                    ParseResult.parser_key == PARSER_KEY,
                )
            )
            positions_created = 0
            if parse is None:
                parse = ParseResult(
                    version_id=version.id,
                    parser_key=PARSER_KEY,
                    status=parsed.status,
                    role=parsed.role,
                    title=parsed.title,
                    explanation=parsed.explanation,
                )
                session.add(parse)
                session.flush()
                for index, position in enumerate(parsed.positions, 1):
                    session.add(
                        Position(
                            parse_id=parse.id,
                            ordinal=index,
                            facts=table_row_facts(position).as_dict(),
                            evidence=position.get("evidence", {}),
                        )
                    )
                    positions_created += 1
            job.last_parse_id = parse.id
            self._discover(session, claim, version, parsed)
            if parse.status in {"valuable", "cancelled"}:
                job.state, job.last_error = "done", None
                job.lease_token = job.lease_expires_at = None
            else:
                self._release(job, claim, "no_valuable_content", retry_delay)
            return {"state": job.state, "positions": positions_created}

    @staticmethod
    def _discover(session, claim, version, parsed):
        stamp = now()
        source = session.get(Source, claim.source_id)
        for link in parsed.links:
            if not claim.scope.contains(link.url):
                continue
            same_origin = origin(httpx.URL(link.url)) == claim.approved_origin
            queueable = (
                same_origin
                and source.status == RegistryStatus.ACTIVE
                and not link.attachment
                and link.role in {"listing", "notice"}
            )
            target = session.scalar(select(Resource).where(Resource.normalized_url == link.url))
            if target is None:
                target = Resource(
                    original_url=link.url,
                    source_id=claim.source_id if same_origin else None,
                    kind=ResourceKind.ATTACHMENT if link.attachment else ResourceKind.PAGE,
                    review_status=ReviewStatus.APPROVED if queueable else ReviewStatus.PENDING,
                    first_seen_at=stamp,
                    last_seen_at=stamp,
                )
                session.add(target)
                session.flush()
            else:
                target.last_seen_at = stamp
            previous = session.scalar(
                select(Discovery.id).where(
                    Discovery.parent_version_id == version.id,
                    Discovery.target_resource_id == target.id,
                    Discovery.method == DiscoveryMethod.LINK,
                    Discovery.raw_url == link.raw_url,
                )
            )
            if previous is None:
                session.add(
                    Discovery(
                        parent_version_id=version.id,
                        target_resource_id=target.id,
                        method=DiscoveryMethod.LINK,
                        raw_url=link.raw_url,
                        link_text=link.label,
                        discovered_at=stamp,
                    )
                )
            if (
                queueable
                and source.status == RegistryStatus.ACTIVE
                and target.source_id == claim.source_id
                and target.review_status == ReviewStatus.APPROVED
                and session.get(CrawlJob, target.id) is None
            ):
                session.add(CrawlJob(resource_id=target.id, available_at=stamp))
