"""Explicit POC contract migration: retain acquisition, archive incompatible derivations.

The input database is read-only and is never changed. A new output is created
exclusively after integrity validation. Old outputs are not relabelled as v2
facts or fabricated Recipe service revisions. Operators retain the source DB
and authoring workspace backup before switching configured paths.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from youju.database import create_sqlite_engine, initialize_database
from youju.models import Base
from youju.onboarding.store import digest

DERIVED = frozenset(
    {
        "positions",
        "parse_results",
        "source_recipes",
        "source_audits",
        "announcement_audits",
        "onboarding_results",
        "incremental_runs",
        "parsing_feedback",
        "recipe_repair_requests",
        "recipe_repair_incidents",
        "source_recipe_bindings",
        "source_pipelines",
        "incremental_controls",
    }
)
CONFIG_KEYS = {
    "max_resources",
    "max_depth",
    "max_bytes",
    "retry_delay",
    "allow_http",
    "origin_approval",
}
SUMMARY_KEYS = {
    "inventory_incomplete",
    "inventory_blockers",
    "inventory_issues",
    "classification_budgets",
    "reader_version",
}


def rebuild_contract_database(source, destination):
    """Migrate registered/raw material state; explicit re-onboarding is required."""
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError("Migration needs an existing input and an unused output path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name("." + destination.name + "." + uuid4().hex)
    report = {
        "policy": "preserve-acquisition-rebuild-derived-v2",
        "preserved": {},
        "archived_derived": {},
        "sources_requiring_onboarding": [],
    }
    try:
        engine = create_sqlite_engine(temporary)
        try:
            initialize_database(engine)
        finally:
            engine.dispose()
        os.chmod(temporary, 0o600)
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old,
            sqlite3.connect(temporary) as new,
        ):
            old.row_factory = sqlite3.Row
            old.execute("BEGIN")
            if old.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Input database integrity failed")
            tables = {
                r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "source_pipelines" not in tables:
                raise ValueError("No source lifecycle records to migrate")
            if old.execute(
                "SELECT 1 FROM source_pipelines WHERE state='running' "
                "OR lease_token IS NOT NULL OR lease_expires_at IS NOT NULL LIMIT 1"
            ).fetchone():
                raise ValueError("A source operation is still leased")
            if (
                "resource_scans" in tables
                and old.execute(
                    "SELECT 1 FROM resource_scans WHERE state='running' LIMIT 1"
                ).fetchone()
            ):
                raise ValueError("Acquisition is still running")
            new.execute("BEGIN IMMEDIATE")
            for name, table in Base.metadata.tables.items():
                if name in DERIVED or name not in tables:
                    continue
                old_columns = {row[1] for row in old.execute(f'PRAGMA table_info("{name}")')}
                columns = [col.name for col in table.columns if col.name in old_columns]
                records = []
                for row in old.execute(f'SELECT * FROM "{name}"'):
                    values = {column: row[column] for column in columns}
                    if name == "crawl_jobs":
                        values["last_parse_id"] = None
                    records.append(tuple(values[column] for column in columns))
                fields = ",".join('"' + column + '"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                if records:
                    new.executemany(
                        f'INSERT INTO "{name}" ({fields}) VALUES ({placeholders})', records
                    )
                report["preserved"][name] = len(records)
            stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
            controls = (
                {
                    row["source_id"]: dict(row)
                    for row in old.execute("SELECT * FROM incremental_controls")
                }
                if "incremental_controls" in tables
                else {}
            )
            for row in old.execute("SELECT * FROM source_pipelines"):
                row = dict(row)
                config = {
                    key: value
                    for key, value in json.loads(row["config"]).items()
                    if key in CONFIG_KEYS
                }
                previous = json.loads(row["summary"])
                summary = {key: value for key, value in previous.items() if key in SUMMARY_KEYS}
                source_id = row["source_id"]
                summary.update(
                    migration={
                        "policy": report["policy"],
                        "previous_active_recipe_id": row.get("active_recipe_id"),
                        "previous_incremental_enabled": bool(
                            controls.get(source_id, {}).get("enabled")
                        ),
                        "requires_new_recipe_and_audit": True,
                    },
                    error={"code": "RECIPE_REGENERATION_REQUIRED"},
                )
                stage = "inventory" if summary.get("inventory_incomplete") else "classification"
                new.execute(
                    "INSERT INTO source_pipelines (source_id,stage,state,config,summary,"
                    "lease_token,"
                    "lease_expires_at,started_at,activated_at,updated_at) "
                    "VALUES (?,?,?,?,?,NULL,NULL,?,NULL,?)",
                    (
                        source_id,
                        stage,
                        "blocked",
                        json.dumps(config),
                        json.dumps(summary),
                        row["started_at"],
                        stamp,
                    ),
                )
                new.execute(
                    "INSERT INTO source_recipe_bindings "
                    "(source_id,workspace_id,generation,updated_at) VALUES (?,?,0,?)",
                    (source_id, digest({"source_id": source_id}), stamp),
                )
                new.execute(
                    "INSERT INTO incremental_controls (source_id,enabled,state,error,updated_at) "
                    "VALUES (?,0,'idle',NULL,?)",
                    (source_id, stamp),
                )
                report["sources_requiring_onboarding"].append(source_id)
            for name in sorted(DERIVED & tables):
                report["archived_derived"][name] = old.execute(
                    f'SELECT count(*) FROM "{name}"'
                ).fetchone()[0]
            if new.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Migrated database references are inconsistent")
            new.commit()
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Migrated database integrity failed")
            new.execute("PRAGMA foreign_keys=ON")
            if new.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Migrated database foreign key validation failed")
        # Exclusive publication: neither retries nor a concurrent caller overwrite data.
        os.link(temporary, destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)
