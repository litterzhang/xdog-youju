"""Explicit legacy-job removal; material, genuine v3 output and audit history stay intact."""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from youju.models import AuditResult, ParsingFeedback, Position


def legacy_positions():
    migrated = select(AuditResult.parse_id).where(
        AuditResult.result["execution"]["path"].as_string() == "migration"
    )
    return or_(
        Position.facts["schema_version"]
        .as_string()
        .in_(["position-facts-v1", "position-facts-v2"]),
        Position.parse_id.in_(migrated),
    )


def purge_legacy_positions(session: Session):
    """Caller owns backup, runtime writer lock and transaction; never run at startup.

    Deleting user feedback implicitly is forbidden. Preserve newer genuine parsed
    rows, and never treat a schema-only migration as new extraction.
    """
    ids = select(Position.id).where(legacy_positions())
    feedback = session.scalar(
        select(func.count())
        .select_from(ParsingFeedback)
        .where(ParsingFeedback.position_id.in_(ids))
    )
    if feedback:
        raise ValueError(
            "Legacy positions have user feedback; archive that feedback explicitly first"
        )
    before = session.scalar(select(func.count()).select_from(Position))
    result = session.execute(delete(Position).where(legacy_positions()))
    remaining = session.scalar(select(func.count()).select_from(Position))
    return {"before": before, "deleted": result.rowcount, "remaining": remaining}
