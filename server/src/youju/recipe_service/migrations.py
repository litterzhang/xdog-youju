"""Additive, transactional SQLite service migration; no source DB or data resets."""

from sqlalchemy import inspect

from youju.models.base import utc_now

from .models import Base

SCHEMA_VERSION = 5
WORKSPACE_COLUMNS = {
    "physical_path": "VARCHAR(2048)",
    "owner_id": "VARCHAR(128) NOT NULL DEFAULT 'local'",
    "lifecycle": "VARCHAR(16) NOT NULL DEFAULT 'legacy'",
    "status": "VARCHAR(24) NOT NULL DEFAULT 'waiting_input'",
    "agent_profile": "JSON NOT NULL DEFAULT '{}'",
    "profile_revision": "VARCHAR(64)",
    "init_contract_digest": "VARCHAR(64)",
    "init_task_id": "VARCHAR(32)",
    "checkpoint_ref": "VARCHAR(64)",
    "prior_context_ref": "VARCHAR(64)",
    "session_revision": "INTEGER NOT NULL DEFAULT 0",
    "latest_candidate_id": "VARCHAR(32)",
    "accepted_revision_id": "VARCHAR(32)",
    "latest_revision_id": "VARCHAR(32)",
    "accepted_recipe_digest": "VARCHAR(64)",
    "created_at": "VARCHAR(40) NOT NULL DEFAULT ''",
    "updated_at": "VARCHAR(40) NOT NULL DEFAULT ''",
}
JOB_COLUMNS = {
    "kind": "VARCHAR(24) NOT NULL DEFAULT 'legacy_build'",
    "input_revision_id": "VARCHAR(32) REFERENCES input_revisions(id)",
    "depends_on": "VARCHAR(32) REFERENCES jobs(id)",
    "prompt_ref": "VARCHAR(64)",
    "request_digest": "VARCHAR(64)",
    "callback_target": "VARCHAR(128)",
    "base_session_revision": "INTEGER NOT NULL DEFAULT 0",
    "checkpoint_ref": "VARCHAR(64)",
    "recovery_ref": "VARCHAR(64)",
    "base_recipe_digest": "VARCHAR(64)",
    "revision_id": "VARCHAR(32) REFERENCES recipe_revisions(id)",
    "base_revision_id": "VARCHAR(32) REFERENCES recipe_revisions(id)",
    "accepted_base_revision_id": "VARCHAR(32)",
    "artifact_digest": "VARCHAR(64)",
    "admission": "VARCHAR(16)",
}


def migrate(engine, root):
    """Called under the existing short metadata writer lock, before ORM reads."""
    with engine.begin() as connection:
        version = connection.exec_driver_sql("PRAGMA user_version").scalar()
        if version > SCHEMA_VERSION:
            raise ValueError("Recipe service database schema is newer than this application")
        tables = set(inspect(connection).get_table_names())
        if "workspaces" in tables:
            columns = {c["name"] for c in inspect(connection).get_columns("workspaces")}
            if "accepted_revision_id" not in columns:
                job_columns = (
                    {c["name"] for c in inspect(connection).get_columns("jobs")}
                    if "jobs" in tables
                    else set()
                )
                accepted = (
                    connection.exec_driver_sql(
                        "SELECT 1 FROM workspaces WHERE accepted_recipe_digest IS NOT NULL LIMIT 1"
                    ).first()
                    if "accepted_recipe_digest" in columns
                    else None
                )
                published = (
                    connection.exec_driver_sql(
                        "SELECT 1 FROM jobs WHERE kind IN ('build','repair') "
                        "AND status='candidate_ready' LIMIT 1"
                    ).first()
                    if "kind" in job_columns
                    else None
                )
                if accepted or published:
                    raise ValueError(
                        "Legacy managed candidates lack immutable recipe revisions; "
                        "use a fresh storage path. Existing database is unchanged."
                    )
        for table, additions in (("workspaces", WORKSPACE_COLUMNS), ("jobs", JOB_COLUMNS)):
            if table not in tables:
                continue
            columns = {c["name"] for c in inspect(connection).get_columns(table)}
            for name, declaration in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                    )
        if "workspaces" in tables:
            now = utc_now().isoformat()
            for row in connection.exec_driver_sql("SELECT id, physical_path FROM workspaces").all():
                key, path = row
                if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                    raise ValueError("Invalid legacy workspace identifier")
                if path is None:
                    connection.exec_driver_sql(
                        "UPDATE workspaces SET physical_path=? WHERE id=?", (str(root / key), key)
                    )
            connection.exec_driver_sql(
                "UPDATE workspaces SET created_at=? WHERE created_at=''", (now,)
            )
            connection.exec_driver_sql(
                "UPDATE workspaces SET updated_at=? WHERE updated_at=''", (now,)
            )
        Base.metadata.create_all(connection)
        # Metadata snapshots and audit decisions are append-only, including direct SQL.
        for table in ("recipe_revisions", "revision_reviews"):
            for operation in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'immutable recipe revision or review'); END"
                )
        connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
