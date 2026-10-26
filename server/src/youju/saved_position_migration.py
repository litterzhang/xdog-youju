"""Explicit one-shot v2 -> v3 display migration, never a runtime compatibility path.

Copy the real runtime DB, retain every original row, append review-only derived
facts, and pause incompatible monitoring. This is not Recipe execution/admission.
"""

import hashlib
import sqlite3
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine
from youju.models import (
    AuditResult,
    Base,
    IncrementalControl,
    ParseResult,
    Position,
    ResourceBundle,
    SourcePipeline,
)
from youju.models.base import utc_now
from youju.positions.conditions import conditions_from_texts
from youju.positions.facts import (
    EMPLOYMENT_TYPES,
    POSITION_FIELDS,
    Condition,
    PositionFacts,
    TaggedField,
)

POLICY = "saved-v2-facts-migration-v1"


def convert_v2(raw):
    """Preserve known fields/logic; no date promotion or eligibility inference.

    Reuse only unambiguous single-atom generic condition recognition. Ambiguous
    atoms remain literal `other` values with the same identity, scope and tag.
    """
    if raw.get("schema_version") != "position-facts-v2":
        raise ValueError("Expected explicitly archived position-facts-v2 input")
    fields = {key: TaggedField() for key in POSITION_FIELDS}
    for key in POSITION_FIELDS:
        if key in raw and key not in {
            "deadline_on",
            "application_start_on",
            "application_dates",
            "staffing_status",
        }:
            fields[key] = TaggedField.from_dict(raw[key])
    employment = fields["employment_type"]
    if employment.value == "public_institution_staff":
        fields["staffing_status"] = TaggedField("established", employment.tag)
        fields["employment_type"] = TaggedField()
    elif employment.value is not None and employment.value not in EMPLOYMENT_TYPES:
        raise ValueError("Unknown legacy employment identity; explicit review required")
    conditions = []
    for item in raw.get("conditions", []):
        fact = TaggedField(item["value"], item["tag"])
        kind, operator = item["type"], item["operator"]
        if fact.value is not None:
            if not isinstance(fact.value, str):
                raise ValueError("Legacy conditions must retain their literal text")
            parsed, _ = conditions_from_texts([(kind, fact.value)])
            if len(parsed) == 1 and parsed[0].type == kind:
                operator = parsed[0].operator if operator == "unspecified" else operator
                fact = TaggedField(parsed[0].fact.value, fact.tag)
            else:
                kind, operator = "other", "unspecified"
                fact = TaggedField({"text": fact.value}, fact.tag)
        conditions.append(Condition(item["id"], kind, operator, fact, item["scope"]))
    return PositionFacts(fields, tuple(conditions), deepcopy(raw.get("condition_logic")))


def _identifier(value):
    return hashlib.sha256(f"{POLICY}:{value}".encode()).hexdigest()[:32]


def migrate_saved_positions(source, destination):
    """Exclusive output copy. Failure removes only this call's uncommitted output.

    Caller stops runtime writers and backs up Recipe workspaces before cutover.
    Existing output paths, leased sources and unexpected partial schemas fail closed.
    """
    source, destination = Path(source).resolve(strict=True), Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents retries/concurrent callers overwriting real data.
    fd = destination.open("xb")
    fd.close()
    destination.chmod(0o600)
    engine = None
    try:
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old,
            sqlite3.connect(destination) as new,
        ):
            if old.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Source integrity check failed")
            if old.execute(
                "SELECT 1 FROM source_pipelines WHERE state='running' OR lease_token IS NOT NULL "
                "OR lease_expires_at IS NOT NULL LIMIT 1"
            ).fetchone():
                raise ValueError("Source operation is still leased")
            old.backup(new)
        engine = create_sqlite_engine(destination)
        report = {
            "policy": POLICY,
            "original_positions": 0,
            "appended_positions": 0,
            "appended_audits": 0,
            "paused_sources": [],
            "audit_mapping": [],
        }
        with engine.begin() as connection:
            inspector = inspect(connection)
            for name in inspector.get_table_names():
                if name not in Base.metadata.tables:
                    continue
                columns = {c["name"] for c in inspector.get_columns(name)}
                missing = set(Base.metadata.tables[name].columns.keys()) - columns
                if missing and (
                    name != "positions"
                    or missing
                    - {
                        "deadline_on",
                        "application_start_on",
                    }
                ):
                    raise ValueError(f"Unexpected schema migration required: {name}")
                for column in sorted(missing):
                    connection.exec_driver_sql(f"ALTER TABLE positions ADD COLUMN {column} DATE")
            Base.metadata.create_all(connection)
            for index in Position.__table__.indexes:
                index.create(connection, checkfirst=True)
        now = utc_now()
        with Session(engine) as session, session.begin():
            originals = list(
                session.scalars(select(Position).order_by(Position.parse_id, Position.ordinal))
            )
            report["original_positions"] = len(originals)
            by_parse = {}
            for position in originals:
                if position.facts.get("schema_version") == "position-facts-v2":
                    by_parse.setdefault(position.parse_id, []).append(position)
            audits = list(
                session.scalars(
                    select(AuditResult)
                    .where(AuditResult.parse_id.in_(by_parse))
                    .order_by(AuditResult.created_at, AuditResult.id)
                )
            )
            # Preserve cancellation from a newer no-row historical attempt too.
            cancelled = {}
            for old in session.scalars(
                select(AuditResult).order_by(AuditResult.created_at, AuditResult.id)
            ):
                bundle = session.get(ResourceBundle, old.bundle_id)
                validity = old.result.get("validity", {})
                cancelled[bundle.resource_id] = (
                    old.status == "cancelled"
                    or old.result.get("disposition") == "cancelled"
                    or validity.get("disposition") == "cancelled"
                )
            offset = max((row.generation for row in audits), default=0) + 1
            converted = set()
            for number, old in enumerate(audits, 1):
                if old.parse_id in converted:
                    raise ValueError(
                        "Multiple legacy audits share a parse; explicit review required"
                    )
                converted.add(old.parse_id)
                previous = session.get(ParseResult, old.parse_id)
                positions = by_parse[old.parse_id]
                facts = [convert_v2(row.facts) for row in positions]
                parse = ParseResult(
                    id=_identifier("parse:" + old.id),
                    version_id=previous.version_id,
                    parser_key=hashlib.sha256(f"{POLICY}:{old.id}".encode()).hexdigest(),
                    status="review",
                    role=previous.role,
                    title=previous.title,
                    explanation=f"{POLICY}; original_audit={old.id}; not Recipe execution",
                    created_at=now + timedelta(microseconds=number),
                )
                session.add(parse)
                session.flush()
                issue = {
                    "code": "LEGACY_FACTS_MIGRATED",
                    "message": "Historical fields were migrated, not re-extracted or admitted. "
                    "Legacy flat dates need structured re-extraction.",
                    "scope": "domain",
                    "owner": "migration",
                    "repairable": False,
                }
                result = {
                    "schema_version": "position-facts-v3",
                    "status": "review",
                    "positions": [item.as_dict() for item in facts],
                    "errors": [issue],
                    "coverage": {"supported": False, "complete": False, "needs_detailed": True},
                    "execution": {
                        "path": "migration",
                        "model_used": False,
                        "runtime_contract": POLICY,
                        "parser": None,
                        "script_issues": [],
                    },
                }
                bundle = session.get(ResourceBundle, old.bundle_id)
                if cancelled.get(bundle.resource_id):
                    result["disposition"] = "cancelled"
                audit = AuditResult(
                    id=_identifier("audit:" + old.id),
                    bundle_id=old.bundle_id,
                    recipe_id=old.recipe_id,
                    generation=offset + number,
                    evaluated_on=now.date().isoformat(),
                    status="review",
                    result=result,
                    errors=[issue],
                    parse_id=parse.id,
                    created_at=now + timedelta(microseconds=number),
                )
                session.add(audit)
                for original, item in zip(positions, facts, strict=True):
                    session.add(
                        Position(
                            id=_identifier("position:" + original.id),
                            parse_id=parse.id,
                            ordinal=original.ordinal,
                            facts=item.as_dict(),
                            evidence=deepcopy(original.evidence),
                            review_status="needs_review",
                        )
                    )
                report["appended_positions"] += len(facts)
                report["audit_mapping"].append({"original": old.id, "migrated": audit.id})
            if len(converted) != len(by_parse):
                raise ValueError("Unbound legacy parses cannot be silently omitted")
            report["appended_audits"] = len(audits)
            for pipeline in session.scalars(select(SourcePipeline)):
                control = session.get(IncrementalControl, pipeline.source_id)
                error = {
                    "code": "RECIPE_UPGRADE_REQUIRED",
                    "message": "Saved jobs are visible with migration tags. Regenerate and audit "
                    "the legacy Recipe before resuming incremental work.",
                }
                pipeline.state = "blocked"
                pipeline.summary = {
                    **pipeline.summary,
                    "migration": {
                        "policy": POLICY,
                        "previous_incremental_enabled": bool(control and control.enabled),
                    },
                    "error": error,
                }
                if control:
                    control.enabled, control.state, control.error = False, "blocked", error
                report["paused_sources"].append(pipeline.source_id)
        with engine.connect() as connection:
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("Foreign key validation failed")
            if connection.exec_driver_sql("PRAGMA integrity_check").scalar() != "ok":
                raise ValueError("Migrated integrity check failed")
        return report
    except BaseException:
        if engine:
            engine.dispose()
        destination.unlink(missing_ok=True)
        raise
    finally:
        if engine:
            engine.dispose()
