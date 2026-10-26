"""Explicit local CLI controls; no background service or paid model is started at import."""

import asyncio
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path

import click
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from youju.database import create_sqlite_engine, initialize_database

from .auditing import AuditLedger, current_source_positions
from .author_prompt import AUTHOR_MODEL
from .store import PipelineStore
from .xdog import DEFAULT_FLOW_MODEL


def _paths(function):
    function = click.option(
        "--storage",
        type=click.Path(file_okay=False, path_type=Path),
        default=".data/source-blobs",
        show_default=True,
    )(function)
    return click.option(
        "--database",
        type=click.Path(dir_okay=False, path_type=Path),
        default="youju.sqlite3",
        show_default=True,
    )(function)


def _models(function):
    for name in ("provider", "small-model", "large-model", "author-model", "flow-model"):
        function = click.option(
            f"--{name}",
            default=(
                AUTHOR_MODEL
                if name == "author-model"
                else DEFAULT_FLOW_MODEL
                if name == "flow-model"
                else ""
            ),
            envvar="YOUJU_FLOW_MODEL" if name == "flow-model" else None,
            show_default=True,
            help="Explicit xdog SDK configuration.",
        )(function)
    function = click.option(
        "--local-classifier",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Shared form/intent JSON classifier; source-specific binary artifacts are rejected.",
    )(function)
    function = click.option(
        "--classification-llm-fraction",
        type=click.FloatRange(0, 1),
        default=0.05,
        show_default=True,
        help="Maximum newly reserved LLM classification calls per observed page version.",
    )(function)
    return click.option(
        "--enable-models",
        is_flag=True,
        help="Allow live xdog calls; provider and classifier model IDs are required.",
    )(function)


@contextmanager
def _store(database, storage):
    if not database.is_file():
        raise click.ClickException("Database not found; run init-db and import-sources first")
    lock_path = database.resolve().with_suffix(database.suffix + ".source.lock")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise click.ClickException("Another source command owns this database") from error
        engine = create_sqlite_engine(database)
        try:
            initialize_database(engine)
            yield PipelineStore(engine, storage)
        except (ValueError, RuntimeError, SQLAlchemyError) as error:
            raise click.ClickException(str(error)) from error
        finally:
            engine.dispose()
            fcntl.flock(lock, fcntl.LOCK_UN)


def _print(value):
    click.echo(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def register_onboarding_commands(app):
    @app.cli.command("source-accuracy-migrate")
    @_paths
    def accuracy_migrate(database, storage):
        """Add the immutable rule cache; do not retag, publish or reparse old outputs."""
        from youju.models.onboarding import (
            AnnouncementChangeReview,
            ApplicationRuleReceipt,
            SemanticAuditCheck,
            SemanticCaseExposure,
            SemanticCaseLabel,
            SourceCalendarReview,
        )

        if not database.is_file():
            raise click.ClickException("Database not found")
        # _store retains the source process lock and existing schema safety checks.
        with _store(database, storage) as store:
            for model in (
                ApplicationRuleReceipt,
                SemanticCaseLabel,
                SemanticAuditCheck,
                SemanticCaseExposure,
                SourceCalendarReview,
                AnnouncementChangeReview,
            ):
                model.__table__.create(store.engine, checkfirst=True)
            _print(
                {
                    "rule_cache": "ready",
                    "semantic_case_ledger": "ready",
                    "temporal_policies": "ready",
                    "historical_results_changed": False,
                    "accuracy_enabled": False,
                    "re_audit_required": True,
                }
            )

    @app.cli.command("source-associate-change")
    @click.argument("source_id")
    @click.argument("target_bundle_id")
    @click.argument("event_bundle_id")
    @click.option("--effect", type=click.Choice(["extension", "cancellation"]), required=True)
    @click.option("--reviewer", required=True)
    @click.option("--reference", required=True)
    @click.option("--supersedes", default=None)
    @click.option("--confirm-reviewed", is_flag=True, required=True)
    @_paths
    def associate_change(
        source_id,
        target_bundle_id,
        event_bundle_id,
        effect,
        reviewer,
        reference,
        supersedes,
        confirm_reviewed,
        database,
        storage,
    ):
        """Review saved notice/event association; never fetch or start a recipe Agent."""
        from .temporal_context import register_change

        if not confirm_reviewed:
            raise click.ClickException("Explicit association review is required")
        with _store(database, storage) as store:
            _print(
                register_change(
                    store,
                    source_id,
                    target_bundle_id,
                    event_bundle_id,
                    effect=effect,
                    reviewer=reviewer,
                    reference=reference,
                    supersedes=supersedes,
                )
            )

    @app.cli.command("source-period-calendar")
    @click.argument("source_id")
    @click.option(
        "--calendar",
        "calendar_path",
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    @click.option("--reviewer", required=True)
    @click.option("--reference", required=True)
    @click.option("--confirm-reviewed", is_flag=True, required=True)
    @_paths
    def period_calendar(
        source_id, calendar_path, reviewer, reference, confirm_reviewed, database, storage
    ):
        """Register an explicit workday policy; no holiday download or model task."""
        from .temporal_context import register_calendar

        if not confirm_reviewed:
            raise click.ClickException("Explicit calendar review is required")
        try:
            with calendar_path.open("rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError("Calendar exceeds byte limit")
            value = json.loads(raw)
        except (ValueError, OSError) as error:
            raise click.ClickException(str(error)) from error
        with _store(database, storage) as store:
            _print(
                register_calendar(store, source_id, value, reviewer=reviewer, reference=reference)
            )

    @app.cli.command("source-semantic-label")
    @click.argument("source_id")
    @click.argument("bundle_id")
    @click.option(
        "--expected",
        "expected_path",
        required=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    @click.option("--reviewer", required=True, help="Named human reviewer; not a model alias.")
    @click.option("--split", type=click.Choice(["regression", "holdout"]), default="regression")
    @click.option(
        "--confirm-reviewed",
        is_flag=True,
        required=True,
        help="Confirm independent human review of these pinned expectations.",
    )
    @_paths
    def semantic_label(
        source_id, bundle_id, expected_path, reviewer, split, confirm_reviewed, database, storage
    ):
        """Register reviewed assertions, never queue models or publish a recipe."""
        from .semantic_labels import MAX_EXPECTATION_BYTES, register_label

        if not confirm_reviewed or expected_path.stat().st_size > MAX_EXPECTATION_BYTES:
            raise click.ClickException("Independent reviewed expectations are required")
        try:
            with expected_path.open("rb") as stream:
                body = stream.read(MAX_EXPECTATION_BYTES + 1)
            if len(body) > MAX_EXPECTATION_BYTES:
                raise ValueError("Semantic expectation exceeds its byte limit")
            expected = json.loads(body)
            if expected.get("basis") != "human-confirmed":
                raise ValueError("The CLI accepts only explicitly human-confirmed expectations")
        except (ValueError, AttributeError) as error:
            raise click.ClickException(str(error)) from error
        with _store(database, storage) as store:
            _print(
                register_label(
                    store, source_id, bundle_id, expected, reviewer=reviewer, split=split
                )
            )

    @app.cli.command("source-lifecycle-migrate")
    @_paths
    def lifecycle_migrate(database, storage):
        """Add independent monitoring tables and freeze historical onboarding; no network."""
        from .lifecycle import backfill_lifecycle

        with _store(database, storage) as store:
            count = backfill_lifecycle(store.engine)
            _print({"backfilled_sources": count, "incremental_default": "disabled"})

    @app.cli.command("source-onboard")
    @click.argument("source_id")
    @click.option("--max-resources", type=click.IntRange(1, 10000), default=1000)
    @click.option("--max-depth", type=click.IntRange(1, 100), default=100)
    @click.option("--retry-delay", type=click.IntRange(0, 86400), default=60)
    @click.option(
        "--allow-http",
        is_flag=True,
        help="Explicitly approve same-host HTTP attachments; pages remain HTTPS-only.",
    )
    @_paths
    def onboard(source_id, max_resources, max_depth, retry_delay, allow_http, database, storage):
        """Register resumable onboarding for an approved source; make no network/model calls."""
        with _store(database, storage) as store:
            _print(
                store.start(
                    source_id,
                    {
                        "max_resources": max_resources,
                        "max_depth": max_depth,
                        "retry_delay": retry_delay,
                        "allow_http": allow_http,
                    },
                )
            )

    @app.cli.command("source-reparse")
    @click.argument("source_id")
    @click.option(
        "--force",
        is_flag=True,
        help="Re-evaluate the selected stored documents even at the current reader key.",
    )
    @click.option(
        "--format",
        "formats",
        multiple=True,
        type=click.Choice(["html", "xml", "pdf", "doc", "docx", "xls", "xlsx"]),
    )
    @click.option("--limit", type=click.IntRange(1, 10000), default=500)
    @_paths
    def reparse(source_id, limit, force, formats, database, storage):
        """Replay saved originals with the current reader; make no new HTTP/model calls."""
        from .reparse import reparse_saved

        with _store(database, storage) as store:
            _print(
                asyncio.run(
                    reparse_saved(store, source_id, limit=limit, force=force, formats=formats)
                )
            )

    @app.cli.command("source-status")
    @click.argument("source_id")
    @_paths
    def status(source_id, database, storage):
        """Show current phase, queue counts, authoring coverage and the latest audit summary."""
        with _store(database, storage) as store:
            _print(store.status(source_id))

    @app.cli.command("source-classification-review")
    @click.argument("source_id")
    @click.option("--enable-models", is_flag=True)
    @click.option("--batch-size", type=click.IntRange(1, 20), default=8, show_default=True)
    @click.option("--max-batches", type=click.IntRange(1, 100), default=3, show_default=True)
    @click.option("--review-model", default="gpt-6.1-sol", show_default=True)
    @_paths
    def classification_review(
        source_id, enable_models, batch_size, max_batches, review_model, database, storage
    ):
        """Review one batch of saved ambiguous pages; never create human labels or fetch URLs."""
        from youju.background import configured_models

        from .classification_review import configured_reviewer

        if not enable_models:
            _print(
                {
                    "status": "dry_run",
                    "model": review_model,
                    "batch_size": batch_size,
                    "source_batch_budget": max_batches,
                    "provider_calls": 0,
                }
            )
            return
        with _store(database, storage) as store:
            status = store.status(source_id)
            if status["stage"] != "classification" or status["state"] != "ready":
                raise click.ClickException(
                    "Review requires idle classification; never change in-flight author/audit pins"
                )
            key = status["summary"].get("classification", {}).get("classifier_key")
            if not key:
                raise click.ClickException("Classify saved pages first")
            config = dict(
                current_app.config,
                CLASSIFICATION_REVIEW_ENABLED=True,
                CLASSIFICATION_REVIEW_MODEL=review_model,
                CLASSIFICATION_REVIEW_BATCH_SIZE=batch_size,
                CLASSIFICATION_REVIEW_MAX_BATCHES=max_batches,
            )
            reviewer = configured_reviewer(store, configured_models(config), key, config)
            _print(asyncio.run(reviewer.run_once(source_id)))

    @app.cli.command("source-repair")
    @click.argument("source_id")
    @click.option("--audit-id")
    @click.option("--previous-request-id")
    @_paths
    def repair(source_id, audit_id, previous_request_id, database, storage):
        """Schedule audit-driven xdog source repair; no direct file edits or model calls."""
        from .repair import request_repair

        with _store(database, storage) as store:
            _print(
                request_repair(
                    store, source_id, audit_id=audit_id, previous_request_id=previous_request_id
                )
            )

    @app.cli.command("source-reanalyze")
    @click.argument("source_id")
    @_paths
    def reanalyze(source_id, database, storage):
        """Restart analysis from saved bundles; no acquisition, parsing or model calls.

        Requires settled acquisition work and no operation lease. Use source-step
        to advance classification, fresh authoring and audit; this command does
        not validate or activate a new recipe.
        """
        with _store(database, storage) as store:
            store.reanalyze(source_id)
            _print(store.status(source_id))

    @app.cli.command("source-resume")
    @click.argument("source_id")
    @click.option(
        "--allow-http",
        is_flag=True,
        default=None,
        help="Explicitly add same-host legacy HTTP attachment approval.",
    )
    @click.option("--max-resources", type=click.IntRange(1, 10000))
    @click.option("--max-depth", type=click.IntRange(1, 100))
    @_paths
    def resume(source_id, max_resources, max_depth, allow_http, database, storage):
        """Explicitly retry a blocked phase; optionally increase inventory budgets."""
        with _store(database, storage) as store:
            store.resume(
                source_id, max_resources=max_resources, max_depth=max_depth, allow_http=allow_http
            )
            _print(store.status(source_id))

    @app.cli.command("source-reassess")
    @click.argument("source_id")
    @_paths
    def reassess(source_id, database, storage):
        """Reassess current-day saved outputs; no downloads, model calls or authoring."""
        with _store(database, storage) as store:
            _print(AuditLedger(store).reassess(source_id))

    @app.cli.command("source-positions")
    @click.argument("source_id")
    @click.option("--include-expired", is_flag=True)
    @click.option(
        "--include-partial",
        is_flag=True,
        help="Include parsed available material with explicit attachment warnings.",
    )
    @_paths
    def positions(source_id, include_expired, include_partial, database, storage):
        """Show current verified extraction (not guaranteed eligibility); history stays stored."""
        with _store(database, storage) as store:
            values = current_source_positions(
                store, source_id, include_expired=include_expired, include_partial=include_partial
            )
            _print({"count": len(values), "positions": values})

    def register_worker(name, *, incremental=False, watch=False):
        @app.cli.command(name)
        @click.argument("source_id")
        @click.option("--limit", type=click.IntRange(1, 500), default=50, show_default=True)
        @click.option("--concurrency", type=click.IntRange(1, 8), default=2)
        @click.option(
            "--poll-seconds",
            type=click.IntRange(1, 3600),
            default=60,
            help="Foreground watch delay; ignored by one-pass commands.",
        )
        @click.option(
            "--cycles",
            type=click.IntRange(0, 100000),
            default=0,
            help="Watch cycles, zero means until interrupted; ignored by one-pass commands.",
        )
        @_models
        @_paths
        def work(
            source_id,
            limit,
            concurrency,
            poll_seconds,
            cycles,
            enable_models,
            provider,
            small_model,
            large_model,
            author_model,
            flow_model,
            local_classifier,
            classification_llm_fraction,
            database,
            storage,
        ):
            from .pipeline import Pipeline
            from .xdog import XdogConfig, XdogModels

            models = None
            if enable_models:
                models = XdogModels(
                    XdogConfig(
                        provider=provider,
                        small_model=small_model,
                        large_model=large_model,
                        author_model=author_model,
                        flow_model=flow_model,
                        live_enabled=True,
                    )
                )
            local = None
            if local_classifier is not None:
                from .shared_classifier import load_runtime_model

                local = load_runtime_model(local_classifier)

            async def run():
                index = 0
                while True:
                    # Release the business-DB CLI lock between passes, including
                    # while waiting for the separate recipe service or poll delay.
                    with _store(database, storage) as store:
                        from .recipe_client import RecipeRPCClient

                        token = current_app.config.get("RECIPE_RPC_TOKEN")
                        rpc = (
                            RecipeRPCClient(current_app.config["RECIPE_RPC_URL"], token)
                            if enable_models and token
                            else None
                        )
                        pipeline = Pipeline(
                            store,
                            recipe_accuracy=current_app.config.get("SOURCE_RECIPE_ACCURACY", False),
                            recipe_rpc=rpc,
                            models=models,
                            concurrency=concurrency,
                            local_classifier=local,
                            classification_llm_fraction=classification_llm_fraction,
                        )
                        result = await (
                            pipeline.incremental(source_id, limit=limit)
                            if incremental
                            else pipeline.step(source_id, limit=limit)
                        )
                    _print(result)
                    index += 1
                    if not watch or result["state"] == "blocked" or (cycles and index >= cycles):
                        break
                    await asyncio.sleep(poll_seconds)

            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                click.echo("Stopped. Persisted leases and source progress remain recoverable.")

        work.__doc__ = (
            "Run a foreground source worker until interrupted; installs no service."
            if watch
            else "Process one bounded incremental pass for an activated source."
            if incremental
            else "Advance one bounded onboarding phase (active sources run an incremental pass)."
        )
        # Click has already captured the callback docstring when applying decorators.
        app.cli.commands[name].help = work.__doc__

    register_worker("source-step")
    register_worker("source-tick", incremental=True)
    register_worker("source-watch", watch=True)

    @app.cli.command("source-worker")
    @click.option("--watch", is_flag=True, help="Poll started sources until interrupted.")
    @click.option("--poll-seconds", type=click.IntRange(1, 3600), default=60)
    @click.option("--cycles", type=click.IntRange(0, 100000), default=0)
    @click.option("--limit", type=click.IntRange(1, 500), default=50)
    @click.option("--concurrency", type=click.IntRange(1, 8), default=2)
    @_models
    @_paths
    def source_worker(
        watch,
        poll_seconds,
        cycles,
        limit,
        concurrency,
        enable_models,
        provider,
        small_model,
        large_model,
        author_model,
        flow_model,
        local_classifier,
        classification_llm_fraction,
        database,
        storage,
    ):
        """Advance registered sources outside HTTP requests; no service is installed."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from youju.models import RegistryStatus, Source, SourcePipeline

        from .pipeline import Pipeline
        from .shared_classifier import load_runtime_model
        from .xdog import XdogConfig, XdogModels

        models = (
            XdogModels(
                XdogConfig(
                    provider=provider,
                    small_model=small_model,
                    large_model=large_model,
                    author_model=author_model,
                    flow_model=flow_model,
                    live_enabled=True,
                )
            )
            if enable_models
            else None
        )
        local = load_runtime_model(local_classifier) if local_classifier else None

        async def run():
            index = 0
            while True:
                with _store(database, storage) as store, Session(store.engine) as session:
                    ids = list(
                        session.scalars(
                            select(SourcePipeline.source_id)
                            .join(Source)
                            .where(
                                Source.status == RegistryStatus.ACTIVE,
                                SourcePipeline.state.in_(["ready", "running"]),
                            )
                            .order_by(SourcePipeline.started_at)
                        )
                    )
                for source_id in ids:
                    with _store(database, storage) as store:
                        from .recipe_client import RecipeRPCClient

                        token = current_app.config.get("RECIPE_RPC_TOKEN")
                        rpc = (
                            RecipeRPCClient(current_app.config["RECIPE_RPC_URL"], token)
                            if enable_models and token
                            else None
                        )
                        pipeline = Pipeline(
                            store,
                            recipe_accuracy=current_app.config.get("SOURCE_RECIPE_ACCURACY", False),
                            recipe_rpc=rpc,
                            models=models,
                            concurrency=concurrency,
                            local_classifier=local,
                            classification_llm_fraction=classification_llm_fraction,
                        )
                        recorded = (
                            store.status(source_id)["summary"]
                            .get("classification", {})
                            .get("classifier_key")
                        )
                        if recorded and recorded != pipeline.classifier_key:
                            _print(
                                {
                                    "source_id": source_id,
                                    "state": "skipped",
                                    "reason": "CLASSIFIER_PROFILE_MISMATCH",
                                }
                            )
                            continue
                        result = await pipeline.step(source_id, limit=limit)
                        _print(
                            {
                                "source_id": source_id,
                                "stage": result["stage"],
                                "state": result["state"],
                                "acquisition": result["acquisition"],
                            }
                        )
                index += 1
                if not watch or (cycles and index >= cycles):
                    return
                await asyncio.sleep(poll_seconds)

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            click.echo("Stopped. Source progress and recipe jobs remain persisted.")
