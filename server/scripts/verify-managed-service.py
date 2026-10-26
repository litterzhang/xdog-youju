"""Explicit native RecipeService replay from saved source material; no baseline download.

Commands execute separate processes so build/repair must restore the init session.
This diagnostic does not activate a source or alter the original source database.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine
from youju.models import SourcePipeline
from youju.models.base import utc_now
from youju.onboarding.author_prompt import AUTHOR_MODEL
from youju.onboarding.recipes import RecipeRunner
from youju.onboarding.sandbox import Sandbox
from youju.onboarding.store import PipelineStore, digest, json_bytes
from youju.onboarding.toolkit import extract_positions
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.recipe_service import RecipeService
from youju.recipe_service.callbacks import CallbackDispatcher
from youju.recipe_service.native_runtime import XdogAgentRuntime
from youju.recipe_service.task_worker import TaskWorker


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
    )


def prepare(args, service):
    # Read from a diagnostic backup, never create/initialize/mutate the source DB.
    copied = args.output / "source-snapshot.sqlite3"
    with sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True) as source:
        with sqlite3.connect(copied) as destination:
            source.backup(destination)
    engine = create_sqlite_engine(copied)
    try:
        store = PipelineStore(engine, args.source_storage)
        with Session(engine) as session:
            pipeline = session.get(SourcePipeline, args.source)
            classifier_key = pipeline.summary["classification"]["classifier_key"]
        chosen = []
        for bundle in store.bundles(args.source, classifier_key=classifier_key, summaries=True):
            if bundle["status"] != "complete":
                continue
            assessment = bundle.get("assessment", {})
            if (
                "article" not in assessment.get("page_roles", [])
                or assessment.get("business_type") != "recruitment"
            ):
                continue
            material = store.load_bundle(bundle["id"], classifier_key=classifier_key)
            if "/cn/indexnews.aspx?" not in material["url"]:
                continue  # A real announcement, not a generic login or vacancy listing.
            deterministic = extract_positions(material)
            if not deterministic["positions"] or deterministic["errors"]:
                continue
            size = len(json_bytes(material))
            chosen.append((size, material))
        if not chosen:
            raise ValueError("No complete saved table notice is available for native replay")
        chosen.sort(key=lambda item: item[0])
        samples = [item[1] for item in chosen[: args.samples]]
    finally:
        engine.dispose()
    location = service.create_workspace(args.source)
    payload = {
        "source_id": args.source,
        "objective": "Generate and maintain evidence-backed recruitment recipes for "
        "the approved USTC employment source, using only saved parsed materials. "
        "Keep validity scripts and expired model-free routing; never invent evidence.",
        "samples": samples,
        "now_iso": utc_now().isoformat(),
    }
    body = json_bytes(payload)
    folder = Path(location["input_dir"])
    (folder / "payload.json").write_bytes(body)
    manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
    (folder / "manifest.json").write_bytes(json_bytes(manifest))
    submitted = service.input_completed(
        location["workspace_id"],
        revision="baseline-v1",
        manifest_digest=digest(manifest),
        request_id="input-v1",
        callback_target="diagnostic",
    )
    build = service.start_build(
        location["workspace_id"],
        input_revision="baseline-v1",
        prompt="Build and validate the source parser/workflow on every supplied sample. "
        "Use the existing template where appropriate and inspect real cell/evidence layouts.",
        request_id="build-v1",
        callback_target="diagnostic",
    )
    report = {
        "workspace": location,
        "input": submitted,
        "build": build,
        "sample_ids": [sample["id"] for sample in samples],
        "sample_urls": [sample["url"] for sample in samples],
        "source_activated": False,
        "new_downloads": 0,
    }
    save(args.output / "request.json", report)
    return report


async def worker(args, service):
    if not args.enable_models:
        raise ValueError("Real model execution requires --enable-models")
    models = XdogModels(
        XdogConfig(
            provider=args.provider,
            small_model=args.small_model,
            large_model=args.large_model,
            author_model=AUTHOR_MODEL,
            live_enabled=True,
        )
    )
    created = []

    def factory():
        runtime = XdogAgentRuntime(models, allow_guidance_update=args.approve_guidance_update)
        created.append(runtime)
        return runtime

    result = await TaskWorker(service, factory).run_once(args.task_id)
    save(args.output / (args.phase + "-status.json"), result)
    assert all(runtime.closed for runtime in created)
    if result and result["status"] in {"succeeded", "candidate_ready"}:
        save(args.output / (args.phase + "-result.json"), service.result(result["id"]))
    return result


def repair(args, service):
    report = json.loads((args.output / "request.json").read_text())
    build_id = (
        args.task_id
        or service.workspace_info(report["workspace"]["workspace_id"])["latest_candidate_id"]
    )
    original = service.result(build_id)
    # Independent request, not a hand edit: exercise feedback version binding
    # and require a substantive traceability field in generated source config.
    requested = service.submit_feedback(
        report["workspace"]["workspace_id"],
        task_id=build_id,
        recipe_digest=original["digest"],
        decision="repair",
        feedback={
            "errors": [
                {
                    "code": "SOURCE_TRACEABILITY_REQUIRED",
                    "message": "Add config.source_id matching the supplied source identity "
                    "and make "
                    "the generated fast_parse/validity entry points check that bundle.source_id "
                    "matches config.source_id before extraction. Preserve samples and evidence; "
                    "report a mismatch explicitly rather than parsing a different source.",
                }
            ],
            "scope": "diagnostic-host-contract-update",
        },
        request_id="repair-v1",
        callback_target="diagnostic",
    )
    save(args.output / "repair-request.json", requested)
    return requested


async def audit(args, service):
    report = json.loads((args.output / "request.json").read_text())
    task_id = (
        args.task_id
        or service.workspace_info(report["workspace"]["workspace_id"])["latest_candidate_id"]
    )
    result = service.result(task_id)
    inputs = service.input_snapshot(service.status(task_id)["input_revision_id"])
    payload = json.loads(inputs["files"]["payload.json"])
    models = XdogModels(
        XdogConfig(
            provider=args.provider,
            small_model=args.small_model,
            large_model=args.large_model,
            author_model=AUTHOR_MODEL,
            live_enabled=args.enable_models,
        )
    )
    runner = RecipeRunner(Sandbox(), models)
    results = [
        await runner.run(result["candidate"], sample, now_iso=payload["now_iso"])
        for sample in payload["samples"]
    ]
    passed = all(value["status"] == "success" and not value["errors"] for value in results)
    evidence = {
        "task_id": task_id,
        "recipe_digest": result["digest"],
        "sample_audit_passed": passed,
        "results": results,
        "source_activated": False,
        "full_source_audit": False,
        "model_calls": models.calls,
    }
    save(args.output / "sample-audit.json", evidence)
    if passed:
        service.submit_feedback(
            report["workspace"]["workspace_id"],
            task_id=task_id,
            recipe_digest=result["digest"],
            decision="accept",
            feedback={"audit": "saved sample audit", "full_source_audit": False},
            request_id="accept-" + task_id,
        )
    return {
        "sample_audit_passed": passed,
        "position_count": sum(len(value["positions"]) for value in results),
    }


async def callbacks(args, service):
    if not args.enable_callbacks:
        raise ValueError("Loopback callback verification requires --enable-callbacks")
    token = secrets.token_hex(32)
    database = args.output / "callback-inbox.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT)")

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + token):
                self.send_response(401)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                self.send_response(413)
                self.end_headers()
                return
            value = json.loads(self.rfile.read(length))
            assert value["event_id"] == self.headers["Idempotency-Key"]
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?)",
                    (value["event_id"], json.dumps(value, ensure_ascii=False)),
                )
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        dispatcher = CallbackDispatcher(
            service,
            {
                "diagnostic": {
                    "url": f"http://127.0.0.1:{server.server_port}/events",
                    "token": token,
                }
            },
        )
        deliveries = []
        while True:
            value = await dispatcher.run_once()
            if value is None:
                break
            deliveries.append(value)
            if value["status"] != "delivered":
                raise ValueError("Loopback delivery did not complete")
        with sqlite3.connect(database) as connection:
            received = connection.execute("SELECT payload FROM events ORDER BY id").fetchall()
        report = {
            "loopback_only": True,
            "deliveries": deliveries,
            "events": [json.loads(row[0]) for row in received],
            "external_notifications": 0,
        }
        save(args.output / "callbacks.json", report)
        return {"status": "delivered", "count": len(deliveries)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=("prepare", "init", "build", "repair-request", "repair", "audit", "callbacks"),
    )
    parser.add_argument("--source", default="ustc-employment")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source-storage", type=Path)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--task-id")
    parser.add_argument("--provider", default="copilot")
    parser.add_argument("--small-model", default="gpt-5-mini")
    parser.add_argument("--large-model", default="gpt-5.5")
    parser.add_argument("--enable-models", action="store_true")
    parser.add_argument("--approve-guidance-update", action="store_true")
    parser.add_argument("--enable-callbacks", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    service = RecipeService(args.storage)
    try:
        if args.phase == "prepare":
            if not args.database or not args.source_storage:
                parser.error("prepare requires --database and --source-storage")
            value = prepare(args, service)
        elif args.phase == "repair-request":
            value = repair(args, service)
        elif args.phase == "audit":
            value = asyncio.run(audit(args, service))
        elif args.phase == "callbacks":
            value = asyncio.run(callbacks(args, service))
        else:
            value = asyncio.run(worker(args, service))
        # Only IDs/status/counts, not original material or provider/account data.
        print(
            json.dumps(
                {
                    "phase": args.phase,
                    "status": value.get("status"),
                    "id": value.get("id"),
                    "sample_audit_passed": value.get("sample_audit_passed"),
                    "repair_task_id": value.get("repair_task_id"),
                }
            ),
            flush=True,
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
