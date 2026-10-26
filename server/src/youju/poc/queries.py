"""Read current useful snapshots; never present old parses as current."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    Capture,
    CrawlJob,
    FetchAttempt,
    FetchOutcome,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceVersion,
    ReviewStatus,
    Source,
)
from youju.positions.facts import PositionFacts


def current_positions(engine, source_id: str) -> list[dict]:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    statement = (
        select(Position, Resource, ResourceVersion, FetchAttempt, Capture)
        .join(
            ParseResult,
            Position.parse_id == ParseResult.id,
        )
        .join(CrawlJob, CrawlJob.last_parse_id == ParseResult.id)
        .join(
            Resource,
            Resource.id == CrawlJob.resource_id,
        )
        .join(Source, Resource.source_id == Source.id)
        .join(
            Capture,
            Capture.id == CrawlJob.last_capture_id,
        )
        .join(FetchAttempt, FetchAttempt.id == Capture.attempt_id)
        .join(
            ResourceVersion,
            ResourceVersion.id == ParseResult.version_id,
        )
        .where(
            Source.id == source_id,
            Source.status == RegistryStatus.ACTIVE,
            Resource.review_status == ReviewStatus.APPROVED,
            CrawlJob.state == "done",
            ParseResult.status == "valuable",
            Capture.version_id == ParseResult.version_id,
            FetchAttempt.outcome == FetchOutcome.DOWNLOADED,
        )
        .order_by(Resource.normalized_url, Position.ordinal)
    )
    result = []
    with Session(engine) as session:
        for position, resource, version, attempt, capture in session.execute(statement):
            facts = PositionFacts.from_dict(position.facts)
            deadline = facts.fields["deadline_on"]
            result.append(
                {
                    "id": position.id,
                    "facts": facts.as_dict(),
                    "availability": "past_deadline"
                    if deadline.tag == "source" and date.fromisoformat(deadline.value) < today
                    else "unverified",
                    "review_status": position.review_status,
                    "source_url": resource.original_url,
                    "final_url": attempt.final_url,
                    "version_sha256": version.content_sha256,
                    "last_checked_at": attempt.finished_at.isoformat(),
                    "capture_mode": capture.mode,
                    "evidence": position.evidence,
                }
            )
    return result
