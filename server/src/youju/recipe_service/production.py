"""Loopback-only Recipe RPC, managed Agent worker and reliable callback delivery."""

import asyncio
import fcntl
import threading
from pathlib import Path

from flask import Flask
from sqlalchemy import select
from sqlalchemy.orm import Session
from waitress import create_server

from youju.background import configured_models, emit, load_app, run_cancellable
from youju.onboarding.author_prompt import AUTHOR_MODEL

from .callbacks import CallbackDispatcher
from .errors import ServiceBusy, ServiceError
from .models import Job
from .native_runtime import XdogAgentRuntime
from .rpc import register_recipe_rpc
from .runtime_extension import HttpRecipeRuntimeExtension
from .service import RecipeService
from .task_worker import TaskWorker


def create_recipe_app(config):
    app = Flask("youju-recipe")
    storage = str(Path(config["SOURCE_STORAGE"]).resolve())

    def allowed(caller, source_id):
        # The authenticated runtime server owns registration/official-source
        # eligibility. RecipeService owns opaque owner-scoped workspaces only;
        # it must not open the runtime's private business database to authorize
        # an authoring request. Body fields cannot select this trusted identity.
        return caller == "source-server"

    targets = {
        "source-server": {
            "url": "http://127.0.0.1:8089/internal/source/recipe-events",
            "token": config["RECIPE_CALLBACK_TOKEN"],
        }
    }
    app.config.update(
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        RECIPE_RPC_ENABLED=True,
        RECIPE_SERVICE_STORAGE=storage,
        RECIPE_CALLBACK_TARGETS=targets,
        RECIPE_RPC_SOURCE_AUTHORIZER=allowed,
        RECIPE_RPC_CALLERS={
            "source-server": {
                "token": config["RECIPE_RPC_TOKEN"],
                "source_ids": [],
                "agent_profile": {
                    "provider": config["PROVIDER"],
                    "model": config.get("AUTHOR_MODEL", AUTHOR_MODEL),
                },
                "callback_target": "source-server",
            }
        },
    )
    register_recipe_rpc(app)

    @app.get("/internal/recipe-health")
    def health():
        return {"status": "ok", "service": "youju-recipe", "mode": "managed"}

    return app


def configured_runtime_extension_factory(config):
    """Host-owned connection settings only; never put secrets in task profiles.

    No URL means disabled (the source endpoint may still have a configured token).
    An explicitly configured URL requires a token. Caller defaults to the source
    endpoint's single configured service identity, not the source RPC owner.
    """
    endpoint = config.get("RECIPE_RUNTIME_QUERY_URL")
    if not endpoint:
        return None
    token = config.get("RECIPE_RUNTIME_QUERY_TOKEN")
    if not token:
        raise ValueError("Runtime query URL requires a server token")
    caller = config.get("RECIPE_RUNTIME_QUERY_CALLER", "recipe-service")

    def factory(binding):
        return HttpRecipeRuntimeExtension(binding, endpoint=endpoint, caller_id=caller, token=token)

    return factory


async def run_workers(service, models, targets, *, runtime_extension_factory=None):
    # Reclaim only abandoned managed jobs proven ownerless by the workspace OS lock.
    with Session(service.engine) as session:
        abandoned = list(
            session.scalars(
                select(Job.id).where(
                    Job.status == "running", Job.kind.in_(["init", "build", "repair"])
                )
            )
        )
    for key in abandoned:
        try:
            service.recover(key)
            emit(status="interrupted", task_id=key, code="WORKER_INTERRUPTED")
        except ServiceBusy:
            pass  # Another live owner is never interrupted based on age.
    worker = TaskWorker(
        service,
        lambda: XdogAgentRuntime(models, runtime_extension_factory=runtime_extension_factory),
    )
    dispatcher = CallbackDispatcher(service, targets)

    async def tasks():
        while True:
            try:
                result = await worker.run_once()
                if result:
                    emit(
                        task_id=result["id"],
                        kind=result["kind"],
                        status=result["status"],
                        code=result.get("error_code"),
                    )
            except ServiceError:
                emit(status="task_failed", code="MANAGED_TASK_FAILED")
            await asyncio.sleep(2)

    async def callbacks():
        while True:
            try:
                result = await dispatcher.run_once()
                if result:
                    emit(
                        event_id=result["event_id"],
                        status=result["status"],
                        code=result["error_code"],
                    )
            except ServiceError:
                emit(status="callback_retry", code="CALLBACK_STORAGE_UNAVAILABLE")
            await asyncio.sleep(1)

    async with asyncio.TaskGroup() as group:
        group.create_task(tasks())
        group.create_task(callbacks())


def main():
    config = load_app().config
    models = configured_models(config)
    runtime_extension_factory = configured_runtime_extension_factory(config)
    from youju.onboarding.sandbox import Sandbox

    asyncio.run(Sandbox().probe())
    app = create_recipe_app(config)
    storage = Path(config["SOURCE_STORAGE"]).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    with (storage / ".recipe-daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        service = RecipeService(storage)
        server = create_server(
            app, host="127.0.0.1", port=8090, threads=4, max_request_body_size=65536
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            emit(status="recipe_service_ready", mode="managed", listener="127.0.0.1:8090")
            run_cancellable(
                run_workers(
                    service,
                    models,
                    app.config["RECIPE_CALLBACK_TARGETS"],
                    runtime_extension_factory=runtime_extension_factory,
                )
            )
        finally:
            server.close()
            thread.join(timeout=5)
            service.close()


if __name__ == "__main__":
    main()
