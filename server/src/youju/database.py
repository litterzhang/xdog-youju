"""Explicit SQLite setup; importing models or starting Flask never creates a database."""

from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import URL, Engine, create_engine, event, inspect

from youju.models import Base


def create_sqlite_engine(path: str | Path | None = None) -> Engine:
    """Use an in-memory database unless a file path is explicitly supplied."""
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path) if path is not None else ":memory:"),
        connect_args={"autocommit": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, connection_record):
        previous = connection.autocommit
        connection.autocommit = True
        try:
            cursor = connection.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        finally:
            connection.autocommit = previous

    return engine


def initialize_database(engine: Engine) -> None:
    """Create a fresh schema; this is not a migration mechanism for existing tables."""
    if engine.dialect.name != "sqlite":
        raise ValueError("Only SQLite is supported by this schema")
    inspector = inspect(engine)
    if inspector.has_table("positions"):
        columns = {column["name"] for column in inspector.get_columns("positions")}
        if not {"facts", "deadline_on", "application_start_on"} <= columns:
            raise ValueError(
                "Old position schema is unsupported. Initialize a fresh database path; "
                "no automatic deletion or migration is performed."
            )
    if inspector.has_table("source_pipelines"):
        columns = {column["name"] for column in inspector.get_columns("source_pipelines")}
        if columns & {"active_recipe_id", "candidate_recipe_id"}:
            raise ValueError(
                "Old recipe binding schema is unsupported. Initialize a fresh database path; "
                "no automatic deletion or migration is performed."
            )
    Base.metadata.create_all(engine)
