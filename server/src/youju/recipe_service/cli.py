"""Same-host managed workspace and task controls; execution stays opt-in."""

import json
from contextlib import contextmanager
from pathlib import Path

import click
from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from .service import RecipeService, ServiceError


def _storage(function):
    return click.option(
        "--storage",
        type=click.Path(file_okay=False, path_type=Path),
        default=".data/source-blobs",
        show_default=True,
        help="Shared blob root; the service uses authoring/service.sqlite3, not the source DB.",
    )(function)


def _print(value):
    # JSON Lines remains machine-readable when stdout is redirected to a progress log.
    stream = click.get_text_stream("stdout")
    click.echo(json.dumps(value, ensure_ascii=False, allow_nan=False), file=stream)
    stream.flush()


@contextmanager
def _service(storage):
    service = None
    try:
        service = RecipeService(storage)
        yield service
    except ServiceError as error:
        raise click.ClickException(str(error)) from error
    except (OSError, ValueError, SQLAlchemyError) as error:
        # Do not print SQL parameters, filesystem contents or provider responses.
        raise click.ClickException("Recipe service storage operation failed") from error
    finally:
        if service is not None:
            service.close()


def register_recipe_service_commands(app: Flask) -> None:
    from .managed_cli import register_managed_commands

    register_managed_commands(app)

    @app.cli.command("recipe-workspace-create")
    @click.argument("source_id")
    @_storage
    def create_workspace(source_id, storage):
        """Register a persistent workspace; do not prepare input, enqueue or start an Agent."""
        with _service(storage) as service:
            _print(service.create_workspace(source_id))

    @app.cli.command("recipe-job-status")
    @click.argument("job_id")
    @click.option("--after", type=click.IntRange(min=0), default=0, show_default=True)
    @click.option("--limit", type=click.IntRange(1, 500), default=100, show_default=True)
    @_storage
    def status(job_id, after, limit, storage):
        """Show a job and one bounded page of progress events after a sequence cursor."""
        with _service(storage) as service:
            _print(service.status(job_id, after=after, limit=limit))

    @app.cli.command("recipe-job-cancel")
    @click.argument("job_id")
    @_storage
    def cancel(job_id, storage):
        """Cancel a queued job or request cooperative cancellation of a running Agent."""
        with _service(storage) as service:
            _print(service.cancel(job_id))

    @app.cli.command("recipe-job-recover")
    @click.argument("job_id")
    @_storage
    def recover(job_id, storage):
        """Mark an abandoned running job interrupted; a live owner's OS lock prevents recovery."""
        with _service(storage) as service:
            _print(service.recover(job_id))
