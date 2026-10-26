"""Explicit local registry commands; normal Flask startup performs no database I/O."""

import json
from pathlib import Path

import click
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from youju.api.common import ApiError
from youju.catalog import import_catalog
from youju.database import create_sqlite_engine, initialize_database


def register_commands(app: Flask) -> None:
    @app.cli.command("auth-provision")
    def auth_provision():
        """Explicitly declare Youju roles/permissions in zdog-auth; grant no users."""
        try:
            app.extensions["auth"].provision()
        except ApiError as error:
            raise click.ClickException(f"Auth provisioning failed: {error.code}") from None
        # Do not print provider payloads or machine credentials.
        click.echo("Youju authorization model declared. No user grants were assigned.")

    @app.cli.command("init-db")
    @click.option(
        "--database",
        type=click.Path(path_type=Path, dir_okay=False),
        default="youju.sqlite3",
        show_default=True,
    )
    def init_db(database: Path):
        """Create missing tables in a local SQLite database (not a schema migration)."""
        engine = create_sqlite_engine(database)
        try:
            initialize_database(engine)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        except SQLAlchemyError as error:
            raise click.ClickException(
                "Database initialization failed; check path and schema"
            ) from error
        finally:
            engine.dispose()
        click.echo(f"Database initialized: {database.resolve()}")

    @app.cli.command("feedback-migrate")
    @click.option(
        "--database",
        type=click.Path(exists=True, path_type=Path, dir_okay=False),
        default="youju.sqlite3",
        show_default=True,
    )
    def feedback_migrate(database: Path):
        """Explicitly add feedback storage; no recipe, source or model task is started."""
        from youju.admin import database as controlled_database
        from youju.models import ParsingFeedback

        with controlled_database({"DATABASE": str(database)}, write=True) as engine:
            ParsingFeedback.__table__.create(engine, checkfirst=True)
        click.echo("Parsing feedback table ready. No Agent/recipe task was submitted.")

    @app.cli.command("import-sources")
    @click.argument("catalog", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option(
        "--database",
        type=click.Path(path_type=Path, dir_okay=False),
        default="youju.sqlite3",
        show_default=True,
    )
    def import_sources(catalog: Path, database: Path):
        """Import candidate sources from JSON without fetching or overwriting existing records."""
        if not database.is_file():
            raise click.ClickException("Database not found; run init-db first")
        try:
            data = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise click.ClickException("Cannot read catalog as UTF-8 JSON") from error
        engine = create_sqlite_engine(database)
        try:
            with Session(engine) as session, session.begin():
                created = import_catalog(session, data)
        except ValueError as error:
            raise click.ClickException(f"Catalog import rejected: {error}") from error
        except SQLAlchemyError as error:
            raise click.ClickException(
                "Catalog import failed; check schema, references and constraints. "
                "No changes committed."
            ) from error
        finally:
            engine.dispose()
        click.echo(
            "Registry import complete. Created: "
            + ", ".join(f"{table}={count}" for table, count in created.items())
        )
        click.echo("No sources were approved and no URLs were fetched.")
