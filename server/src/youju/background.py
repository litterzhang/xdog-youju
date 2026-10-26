"""Explicit production worker entry points; never started by a request or factory."""

import asyncio
import fcntl
import json
import signal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from youju import create_app
from youju.database import create_sqlite_engine
from youju.models import RegistryStatus, Source, SourcePipeline
from youju.onboarding.author_prompt import AUTHOR_MODEL
from youju.onboarding.auto_repair import pending as repair_pending
from youju.onboarding.lifecycle import IncrementalLedger
from youju.onboarding.pipeline import Pipeline
from youju.onboarding.recipe_client import RecipeRPCClient, RecipeRPCError
from youju.onboarding.shared_classifier import load_runtime_model
from youju.onboarding.store import PipelineStore
from youju.onboarding.xdog import DEFAULT_FLOW_MODEL, XdogConfig, XdogModels


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def load_app():
    load_dotenv(Path.cwd() / ".env", override=False)
    return create_app()


def configured_models(config):
    if config.get("WORKERS_ENABLE_MODELS") is not True:
        raise ValueError("Explicit WORKERS_ENABLE_MODELS=true is required")
    return XdogModels(
        XdogConfig(
            provider=config["PROVIDER"],
            small_model=config["SMALL_MODEL"],
            large_model=config["LARGE_MODEL"],
            flow_model=config.get("FLOW_MODEL", DEFAULT_FLOW_MODEL),
            author_model=config.get("AUTHOR_MODEL", AUTHOR_MODEL),
            live_enabled=True,
        )
    )


async def source_pass(config, models, *, pipeline_factory=Pipeline, rpc=None):
    path = Path(config["DATABASE"]).resolve()
    lock_path = path.with_suffix(path.suffix + ".source.lock")
    engine = create_sqlite_engine(path)
    try:
        with Session(engine) as session:
            ids = list(
                session.scalars(
                    select(SourcePipeline.source_id)
                    .join(Source)
                    .where(
                        Source.status == RegistryStatus.ACTIVE,
                        SourcePipeline.state.in_(["ready", "running", "blocked"]),
                    )
                    .order_by(SourcePipeline.started_at)
                )
            )
        for source_id in ids:
            # A concurrent registry mutation is a skipped pass, never a daemon crash.
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                store = PipelineStore(engine, config["SOURCE_STORAGE"])
                snapshot = store.status(source_id)
                awaiting_review = (
                    snapshot["summary"].get("revision_review", {}).get("status") == "pending"
                )
                if (
                    snapshot["state"] == "blocked"
                    and not repair_pending(store, source_id)
                    and not awaiting_review
                ):
                    continue
                needs_acceptance = rpc is not None and awaiting_review
                if (
                    snapshot["stage"] == "active"
                    and not needs_acceptance
                    and not repair_pending(store, source_id)
                    and not (IncrementalLedger(store).allowed(source_id))
                ):
                    continue
                pipeline = pipeline_factory(
                    store,
                    models=models,
                    recipe_rpc=rpc,
                    local_classifier=config.get("LOCAL_CLASSIFIER_INSTANCE"),
                    concurrency=config.get("WORKER_CONCURRENCY", 2),
                )
                from youju.onboarding.classification_review import configured_reviewer

                pipeline.classification_reviewer = configured_reviewer(
                    store, models, pipeline.classifier_key, config
                )
                recorded = (
                    store.status(source_id)["summary"]
                    .get("classification", {})
                    .get("classifier_key")
                )
                if recorded and recorded != pipeline.classifier_key:
                    emit(source_id=source_id, status="skipped", code="CLASSIFIER_PROFILE_MISMATCH")
                    continue
                try:
                    result = await pipeline.step(
                        source_id, limit=config.get("WORKER_PASS_LIMIT", 10)
                    )
                    emit(
                        source_id=source_id,
                        stage=result["stage"],
                        state=result["state"],
                        acquisition=result["acquisition"],
                    )
                except RecipeRPCError as error:
                    emit(
                        source_id=source_id,
                        status="retry" if error.transient else "blocked",
                        code=error.code,
                    )
                    if not error.transient:
                        if store.status(source_id)["stage"] == "active":
                            IncrementalLedger(store).block(source_id, error.code)
                        else:
                            pipeline.block(source_id, error.code, "Recipe service needs review")
                except Exception as error:
                    emit(source_id=source_id, error_type=type(error).__name__)
                    # Preserve data/model errors visibly; don't spin on permanently bad recipes.
                    if store.status(source_id)["stage"] == "active":
                        IncrementalLedger(store).block(source_id, "INCREMENTAL_WORKER_FAILED")
                    else:
                        pipeline.block(
                            source_id, "SOURCE_WORKER_FAILED", "Inspect private worker diagnostics"
                        )
                    emit(source_id=source_id, status="blocked", code="SOURCE_WORKER_FAILED")
    finally:
        engine.dispose()


async def watch_source(config, models, rpc):
    while True:
        try:
            await source_pass(config, models, rpc=rpc)
        except Exception:
            emit(status="retry", code="SOURCE_PASS_UNAVAILABLE")
        await asyncio.sleep(config.get("WORKER_POLL_SECONDS", 3))


def run_cancellable(coroutine):
    async def run():
        task = asyncio.create_task(coroutine)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            emit(status="stopped")

    asyncio.run(run())


def main():
    app = load_app()
    config = dict(app.config)
    if not Path(config["DATABASE"]).is_file():
        raise SystemExit("Source database is not initialized")
    models = configured_models(config)
    from youju.onboarding.sandbox import Sandbox

    asyncio.run(Sandbox().probe())
    if config.get("LOCAL_CLASSIFIER"):
        config["LOCAL_CLASSIFIER_INSTANCE"] = load_runtime_model(Path(config["LOCAL_CLASSIFIER"]))
    rpc = RecipeRPCClient(config["RECIPE_RPC_URL"], config["RECIPE_RPC_TOKEN"])
    # One daemon per source DB; lock survives idle gaps but does not block API writes.
    with Path(config["DATABASE"]).with_suffix(".worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        emit(status="source_worker_ready", mode="managed-rpc")
        run_cancellable(watch_source(config, models, rpc))


if __name__ == "__main__":
    main()
