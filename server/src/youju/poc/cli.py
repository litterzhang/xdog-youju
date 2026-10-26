"""Local POC commands; the async worker is separate from HTTP request handling."""

import asyncio
import json
from pathlib import Path

import click
from flask import Flask

from youju.database import create_sqlite_engine

from .ledger import Ledger
from .queries import current_positions
from .worker import CrawlWorker


def _database_option(function):
    return click.option(
        "--database",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default="youju.sqlite3",
        show_default=True,
    )(function)


def register_poc_commands(app: Flask) -> None:
    @app.cli.command("crawl-approve")
    @click.argument("source_id")
    @click.option("--root-domain", required=True)
    @click.option("--evidence", required=True)
    @_database_option
    def approve(source_id, root_domain, evidence, database):
        """Explicitly approve a source, its candidate seeds and the organization-domain boundary."""
        engine = create_sqlite_engine(database)
        try:
            Ledger(engine).approve(source_id, root_domain, evidence)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        finally:
            engine.dispose()
        click.echo("Source and candidate seeds approved; no network requests made.")

    @app.cli.command("crawl-enqueue")
    @click.argument("source_id")
    @click.option("--revisit", is_flag=True, help="Also requeue previously completed resources.")
    @_database_option
    def enqueue(source_id, revisit, database):
        """Queue approved seeds/resources without running a crawler."""
        engine = create_sqlite_engine(database)
        try:
            count = Ledger(engine).enqueue(source_id, revisit)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        finally:
            engine.dispose()
        click.echo(f"Queued {count} resources; no network requests made.")

    @app.cli.command("crawl-work")
    @click.argument("source_id")
    @click.option("--limit", type=click.IntRange(1, 100), default=6, show_default=True)
    @click.option("--concurrency", type=click.IntRange(1, 8), default=2, show_default=True)
    @click.option("--retry-delay", type=click.FloatRange(min=0), default=60, show_default=True)
    @click.option("--timeout", type=click.FloatRange(min=1, max=180), default=45, show_default=True)
    @click.option(
        "--storage", type=click.Path(file_okay=False, path_type=Path), default=".data/blobs"
    )
    @_database_option
    def work(source_id, limit, concurrency, retry_delay, timeout, storage, database):
        """Run one bounded async worker pass. A URL is attempted at most once per pass."""
        engine = create_sqlite_engine(database)
        try:
            worker = CrawlWorker(
                engine, storage, retry_delay=retry_delay, timeout=timeout, concurrency=concurrency
            )
            result = asyncio.run(worker.run_once(source_id, limit))
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        finally:
            engine.dispose()
        click.echo(json.dumps(result, ensure_ascii=False))

    @app.cli.command("crawl-positions")
    @click.argument("source_id")
    @_database_option
    def positions(source_id, database):
        """Print currently extracted positions with evidence and explicit review requirements."""
        engine = create_sqlite_engine(database)
        try:
            result = current_positions(engine, source_id)
        finally:
            engine.dispose()
        click.echo(
            json.dumps({"count": len(result), "positions": result}, ensure_ascii=False, indent=2)
        )
