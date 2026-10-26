"""Audit fresh saved-source runtime results into a NEW PRIVATE SQLite copy only.

This is verification, not a production migration/adoption command. The copied old
position table receives Date projection columns, while all prior data/history and
active bindings remain untouched. Models, downloads, queues and callbacks are absent.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from replay_source_accuracy import connect_snapshot, read_blob
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    AuditResult,
    FetchAttempt,
    Position,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.onboarding.auditing import AUDIT_POLICY, AuditLedger
from youju.onboarding.diagnostics import failed_result, failure_record
from youju.onboarding.store import PipelineStore
from youju.poc.storage import BlobStore
from youju.recipe_runtime.contracts import candidate_digest, template_candidate
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_runtime.versioning import runtime_fingerprint
from youju.serialization import json_bytes


class OverlayBlobs:
    """New diagnostics stay private; immutable original material is read-only."""

    def __init__(self, original, private):
        self.original = Path(original).resolve(strict=True)
        self.private = BlobStore(private)
        self.root = self.private.root

    def read(self, reference):
        if (self.root / reference).is_file():
            return self.private.read(reference)
        return read_blob(self.original, reference)

    def put(self, data):
        return self.private.put(data)


def verify(args):
    report = json.loads(args.report.read_bytes())
    if report.get("schema_version") != "source-runtime-replay-v1":
        raise ValueError("Expected a fresh current-runtime replay report")
    key = runtime_fingerprint()
    if report.get("runtime_fingerprint") != key:
        raise ValueError("Runtime changed since replay; produce a fresh report first")
    snapshot = Path(report["snapshot"]).resolve(strict=True)
    before_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if before_hash != report["snapshot_sha256"]:
        raise ValueError("Original snapshot digest changed")
    candidate = template_candidate()
    if report["candidate"]["digest"] != candidate_digest(candidate):
        raise ValueError("This verifier requires the exact current host template")
    artifact_root = Path(__file__).resolve().parents[1] / ".artifacts"
    output = args.output.resolve()
    if not output.is_relative_to(artifact_root) or output.exists():
        raise ValueError("Choose a NEW private directory below server/.artifacts")
    output.mkdir(parents=True)
    copy = output / "source-copy.sqlite3"
    with connect_snapshot(snapshot) as original, sqlite3.connect(copy) as target:
        original.backup(target)
    # Explicit verification-only copy extension. initialize_database still rejects
    # old live schemas; no production migration or data relabelling is provided.
    with sqlite3.connect(copy) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(positions)")}
        for name in ("deadline_on", "application_start_on"):
            if name not in columns:
                database.execute(f"ALTER TABLE positions ADD COLUMN {name} DATE")
            database.execute(f"CREATE INDEX IF NOT EXISTS ix_positions_{name} ON positions({name})")
    engine = create_sqlite_engine(copy)
    store = PipelineStore(engine, output / "objects")
    store.blobs = OverlayBlobs(report["blobs"], output / "objects")
    summaries = []
    try:
        initialize_database(engine)
        source_id = report["source_id"]
        with Session(engine) as session:
            before_positions = session.scalar(select(func.count()).select_from(Position))
            before_attempts = session.scalar(select(func.count()).select_from(FetchAttempt))
            binding_before = session.get(SourceRecipeBinding, source_id)
            active_before = binding_before.active_recipe_id
            generation_before = binding_before.generation
        with Session(engine) as session, session.begin():
            artifact = SourceRecipe(
                source_id=source_id,
                digest=candidate_digest(candidate),
                body_ref=store.blobs.put(json_bytes(candidate)),
                status="validated",
                validation={
                    "toolkit_key": key,
                    "errors": [],
                    "mode": "offline_storage_verification_not_activation",
                },
                coverage={"recipe_origin": "host_template_not_agent_authored"},
            )
            session.add(artifact)
            session.flush()
            recipe_id = artifact.id
        ledger = AuditLedger(store)
        day = report["clock"][:10]
        generation = generation_before + 1
        not_executed = []
        for row in report["records"]:
            if row.get("routing") is None:
                continue
            if not row.get("recipe_execution_attempted"):
                # Classification review is not a failed parser/model execution.
                not_executed.append(
                    {"bundle_id": row["bundle_id"], "reason": "classification_review_not_executed"}
                )
                continue
            bundle = store.load_bundle(row["bundle_id"])
            bundle["assessment"] = row["classification"]
            result = row.get("result")
            evidence = None
            if result is None:
                error = RecipeError(
                    row.get("error_code") or "CLASSIFICATION_REVIEW",
                    "Saved runtime execution is unfinished or unclassified",
                    errors=row.get("errors")
                    or [{"code": "CLASSIFICATION_REVIEW", "message": "Unresolved page intent"}],
                    routing=row["routing"],
                    execution=(row.get("runtime_error") or {}).get("execution"),
                    rejected_result=row.get("rejected_result"),
                    rejected_origin=row.get("rejected_origin") or "host_validation",
                )
                evidence = failure_record(
                    bundle,
                    candidate_digest(candidate),
                    report["clock"],
                    error,
                    bindings={"recipe_id": recipe_id, "generation": generation},
                )
                reference = store.blobs.put(json_bytes(evidence))
                result, _ = failed_result(error, failure_ref=reference)
            summary = ledger.record(
                bundle, recipe_id, day, result, generation=generation, failure_evidence=evidence
            )
            summaries.append(summary)
        with Session(engine) as session:
            audits = list(
                session.scalars(select(AuditResult).where(AuditResult.recipe_id == recipe_id))
            )
            parse_ids = [row.parse_id for row in audits if row.parse_id]
            positions = list(
                session.scalars(select(Position).where(Position.parse_id.in_(parse_ids)))
            )
            for position in positions:
                tagged = position.facts["deadline_on"]
                if tagged["tag"] == "source" and tagged["value"]:
                    assert position.deadline_on.isoformat() == tagged["value"]
                else:
                    assert position.deadline_on is None
            binding_after = session.get(SourceRecipeBinding, source_id)
            assert (binding_after.active_recipe_id, binding_after.generation) == (
                active_before,
                generation_before,
            )
            assert session.scalar(select(func.count()).select_from(FetchAttempt)) == before_attempts
            assert session.scalar(
                select(func.count()).select_from(Position)
            ) == before_positions + len(positions)
            dates = session.scalars(
                select(Position.deadline_on).where(
                    Position.parse_id.in_(parse_ids), Position.deadline_on.is_not(None)
                )
            ).all()
            receipt = {
                "result": "passed",
                "mode": "copied_source_storage_no_activation",
                "source_id": source_id,
                "clock": report["clock"],
                "audit_policy": AUDIT_POLICY,
                "runtime_fingerprint": key,
                "replay_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
                "original_snapshot_sha256": before_hash,
                "copy": str(copy),
                "recipe_id": recipe_id,
                "audit_rows": len(audits),
                "not_executed": not_executed,
                "audit_statuses": dict(Counter(row.status for row in audits)),
                "new_position_rows": len(positions),
                "source_date_rows": len(dates),
                "unknown_sql_date_rows": len(positions) - len(dates),
                "exact_sql_date_range": [min(dates).isoformat(), max(dates).isoformat()]
                if dates
                else None,
                "active_binding_unchanged": True,
                "fetch_attempts_unchanged": True,
                "models": 0,
                "downloads": 0,
                "publication": False,
                "limitations": [
                    "Copied DB verification, not deployment or source acceptance",
                    "Unfinished draft diagnostics are not accepted/public positions",
                    "Schema/SQL correctness is not measured semantic accuracy",
                ],
                "results": summaries,
            }
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before_hash
        (output / "verification.json").write_bytes(json_bytes(receipt))
        print(
            json.dumps(
                {k: v for k, v in receipt.items() if k != "results"}, ensure_ascii=False, indent=2
            )
        )
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
