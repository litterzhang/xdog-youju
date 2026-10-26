"""Operator controls for the SDK-independent managed lifecycle; no automatic execution."""

import asyncio
import json
from pathlib import Path

import click
from flask import current_app

from youju.onboarding.author_prompt import AUTHOR_MODEL
from youju.onboarding.xdog import DEFAULT_FLOW_MODEL

from .cli import _print, _service, _storage


def register_managed_commands(app):
    @app.cli.command("recipe-prepare-fresh-session")
    @click.argument("source_id")
    @click.option(
        "--snapshot-storage",
        required=True,
        type=click.Path(exists=True, file_okay=False, path_type=Path),
    )
    @click.option("--new-storage", required=True, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--owner", default="local", show_default=True)
    @click.option("--profile", type=click.File("rb"), default=None)
    @click.option("--confirm-offline-snapshot", is_flag=True, required=True)
    def prepare_fresh(
        source_id, snapshot_storage, new_storage, owner, profile, confirm_offline_snapshot
    ):
        """Preserve old source context and prepare a NEW empty native session; never execute it."""
        from .cutover import prepare_fresh_session

        old, new = snapshot_storage.resolve(), new_storage.resolve()
        if old == new or old in new.parents or new in old.parents:
            raise click.ClickException("Cutover requires disjoint snapshot/new roots")
        # Resolve/reject aliases BEFORE constructing a service that could migrate metadata.
        old_db, new_db = old / "authoring/service.sqlite3", new / "authoring/service.sqlite3"
        if new_db.exists() and old_db.samefile(new_db):
            raise click.ClickException("New metadata cannot alias the offline snapshot")
        value = None
        if profile is not None:
            body = profile.read(16385)
            if len(body) > 16384:
                raise click.ClickException("Profile exceeds size limit")
            try:
                value = json.loads(body)
            except ValueError as exc:
                raise click.ClickException("Profile must be JSON") from exc
        with _service(new) as service:
            _print(
                prepare_fresh_session(
                    service,
                    source_id,
                    snapshot_storage=old,
                    owner_id=owner,
                    agent_profile=value,
                    confirm_offline=confirm_offline_snapshot,
                )
            )

    @app.cli.command("recipe-workspace-status")
    @click.argument("workspace_id")
    @_storage
    def workspace_status(workspace_id, storage):
        """Inspect recorded workspace status/path/profile, not just directory existence."""
        with _service(storage) as service:
            _print(service.workspace_info(workspace_id))

    @app.cli.command("recipe-input-completed")
    @click.argument("workspace_id")
    @click.option("--revision", required=True)
    @click.option("--manifest-digest", required=True)
    @click.option("--request-id", required=True)
    @_storage
    def input_completed(workspace_id, revision, manifest_digest, request_id, storage):
        """Freeze declared input files and enqueue init if needed; make no model calls."""
        with _service(storage) as service:
            _print(
                service.input_completed(
                    workspace_id,
                    revision=revision,
                    manifest_digest=manifest_digest,
                    request_id=request_id,
                )
            )

    @app.cli.command("recipe-start-build")
    @click.argument("workspace_id")
    @click.option("--input-revision", required=True)
    @click.option("--prompt", required=True)
    @click.option("--request-id", required=True)
    @_storage
    def start_build(workspace_id, input_revision, prompt, request_id, storage):
        """Queue a build against a committed input; it waits for successful init."""
        with _service(storage) as service:
            _print(
                service.start_build(
                    workspace_id,
                    input_revision=input_revision,
                    prompt=prompt,
                    request_id=request_id,
                )
            )

    @app.cli.command("recipe-review-revision")
    @click.argument("workspace_id")
    @click.option("--revision-id", required=True)
    @click.option("--decision", type=click.Choice(("accept", "reject")), required=True)
    @click.option("--feedback", type=click.File("rb"), required=True)
    @click.option("--request-id", required=True)
    @_storage
    def review_revision(workspace_id, revision_id, decision, feedback, request_id, storage):
        """Record a revision-bound audit decision; never queue or execute a repair."""
        body = feedback.read(256 * 1024 + 1)
        if len(body) > 256 * 1024:
            raise click.ClickException("Feedback exceeds size limit")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise click.ClickException("Feedback must be a JSON object") from None
        if not isinstance(value, dict) or not value:
            raise click.ClickException("Feedback must be a nonempty JSON audit object")
        with _service(storage) as service:
            _print(
                service.review_revision(
                    workspace_id,
                    revision_id=revision_id,
                    decision=decision,
                    feedback=value,
                    request_id=request_id,
                )
            )

    @app.cli.command("recipe-task-worker")
    @click.option("--job-id")
    @click.option("--enable-models", is_flag=True)
    @click.option(
        "--approve-guidance-update",
        is_flag=True,
        help="Rebind only changed host guidance; preserve conversation and all other pins.",
    )
    @click.option("--provider", envvar="YOUJU_PROVIDER", default="")
    @click.option("--small-model", envvar="YOUJU_SMALL_MODEL", default="")
    @click.option("--large-model", envvar="YOUJU_LARGE_MODEL", default="")
    @click.option("--flow-model", envvar="YOUJU_FLOW_MODEL", default=None)
    @click.option("--author-model", default=AUTHOR_MODEL, show_default=True)
    @click.option(
        "--watch", is_flag=True, help="Foreground managed queue worker until interrupted."
    )
    @click.option("--poll-seconds", type=click.IntRange(1, 3600), default=2, show_default=True)
    @click.option(
        "--cycles",
        type=click.IntRange(0, 100000),
        default=0,
        help="Queue passes; zero means until interrupted. Not an Agent turn limit.",
    )
    @_storage
    def task_worker(
        job_id,
        enable_models,
        approve_guidance_update,
        provider,
        small_model,
        large_model,
        author_model,
        flow_model,
        watch,
        poll_seconds,
        cycles,
        storage,
    ):
        """Execute managed init/build/repair using native xdog 2.4.9 skills and sessions.

        Live model calls require --enable-models. Each task creates/restores an Agent,
        persists checkpoints and closes it; idle workspaces hold no live Agent.
        A task has no author turn/token/whole-run time cap.
        """
        from .task_worker import TaskWorker

        if not enable_models:
            raise click.ClickException(
                "recipe-task-worker requires explicit --enable-models opt-in"
            )
        factory = current_app.config.get("RECIPE_RUNTIME_FACTORY")
        if factory is not None and not callable(factory):
            raise click.ClickException("Invalid server-side runtime factory")
        if factory is not None and approve_guidance_update:
            raise click.ClickException("Custom factories must approve guidance updates themselves")
        if factory is None:
            from youju.onboarding.xdog import XdogConfig, XdogModels

            from .native_runtime import XdogAgentRuntime

            configured = current_app.config.get("RECIPE_NATIVE_MODELS", {})
            profile = {
                "provider": provider or configured.get("provider", ""),
                "small_model": small_model or configured.get("small_model", ""),
                "large_model": large_model or configured.get("large_model", ""),
                "author_model": configured.get("author_model", author_model),
                "flow_model": flow_model or configured.get("flow_model", DEFAULT_FLOW_MODEL),
            }
            if not all(isinstance(value, str) and value.strip() for value in profile.values()):
                raise click.ClickException("Provide provider, small-model and large-model settings")
            models = XdogModels(XdogConfig(**profile, live_enabled=True))

            def factory():
                return XdogAgentRuntime(models, allow_guidance_update=approve_guidance_update)

        with _service(storage) as service:

            async def run():
                worker = TaskWorker(service, factory)
                index = 0
                while True:
                    result = await worker.run_once(job_id)
                    _print(result or {"status": "idle"})
                    index += 1
                    if (
                        not watch
                        or job_id
                        or (cycles and index >= cycles)
                        or (result and result["status"] == "runtime_unavailable")
                    ):
                        return
                    if result is None:
                        await asyncio.sleep(poll_seconds)

            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                _print({"status": "worker_stopped", "reason": "keyboard_interrupt"})

    @app.cli.command("recipe-callbacks")
    @click.option("--enable-callbacks", is_flag=True)
    @click.option("--limit", type=click.IntRange(1, 100), default=1, show_default=True)
    @_storage
    def callbacks(enable_callbacks, limit, storage):
        """Explicitly deliver pending callback events to operator-configured targets."""
        from .callbacks import CallbackDispatcher

        if not enable_callbacks:
            raise click.ClickException("Callback delivery requires explicit --enable-callbacks")
        targets = current_app.config.get("RECIPE_CALLBACK_TARGETS", {})
        if not targets:
            raise click.ClickException("No callback targets are configured")
        with _service(storage) as service:
            dispatcher = CallbackDispatcher(service, targets)

            async def run():
                for _ in range(limit):
                    result = await dispatcher.run_once()
                    _print(result or {"status": "idle"})
                    if result is None:
                        break

            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                _print({"status": "dispatcher_stopped"})
