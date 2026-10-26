"""Opt-in bounded real-source/model verification; uses the application's existing pipeline."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import Discovery, FetchAttempt, Resource, ResourceScan
from youju.models.base import utc_now
from youju.onboarding.author_prompt import AUTHOR_MODEL
from youju.onboarding.cli import _store
from youju.onboarding.pipeline import Pipeline
from youju.onboarding.xdog import ModelError, XdogConfig, XdogModels


class RecordedModels(XdogModels):
    """Record real call accounting without prompts, responses or credentials."""

    def __init__(self, config, output, maximum):
        super().__init__(config)
        self.output, self.maximum, self.used = output, maximum, 0
        self.failed_in_a_row = 0

    async def complete(self, task, payload, schema, *, tier="small"):
        if self.used >= self.maximum:
            raise ModelError("VERIFICATION_BUDGET", "Verification model-call budget exhausted")
        self.used += 1
        index = len(self.calls)
        failed = True
        try:
            value = await super().complete(task, payload, schema, tier=tier)
            failed = False
            return value
        finally:
            self.failed_in_a_row = self.failed_in_a_row + 1 if failed else 0
            if index < len(self.calls):
                record = dict(self.calls[index])
                record["recorded_at"] = datetime.now(UTC).isoformat()
                bundle = payload.get("bundle", payload.get("page_digest", {}))
                record["bundle_id"] = bundle.get("id") if isinstance(bundle, dict) else None
                with self.output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def run_author_agent(self, **kwargs):
        # Authoring has no turn cap. The diagnostic driver's batch
        # call budget still controls whether a new run may be started.
        if self.used >= self.maximum:
            raise ModelError("VERIFICATION_BUDGET", "Verification model-call budget exhausted")
        self.used += 1
        index = len(self.calls)
        try:
            return await super().run_author_agent(**kwargs)
        finally:
            if index < len(self.calls):
                record = dict(self.calls[index], recorded_at=datetime.now(UTC).isoformat())
                with self.output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def run(args):
    with _store(args.database, args.storage) as store:
        await run_locked(args, store)


async def run_locked(args, store):
    args.output.mkdir(parents=True, exist_ok=True)
    models = RecordedModels(
        XdogConfig(
            provider=args.provider,
            small_model=args.small_model,
            large_model=args.large_model,
            author_model=args.author_model,
            live_enabled=True,
        ),
        args.output / "model-calls.jsonl",
        args.max_model_calls,
    )
    local = None
    if args.local_classifier:
        from youju.onboarding.local_classifier import LocalPageModel

        local = LocalPageModel.load(args.local_classifier)
    pipeline = Pipeline(
        store,
        models=models,
        concurrency=4,
        local_classifier=local,
        classification_llm_fraction=args.classification_llm_fraction,
    )
    reason = "pass_limit"
    try:
        for number in range(1, args.max_passes + 1):
            before = store.status(args.source)
            if before["state"] == "blocked":
                reason = "blocked"
                break
            if args.phase != "all" and before["stage"] != args.phase:
                reason = "phase_finished"
                break
            remaining = models.maximum - models.used
            if remaining < 10:
                reason = "model_call_budget"
                break
            state = (
                before
                if before["stage"] == "active"
                else await pipeline.step(args.source, limit=min(args.limit, remaining // 2))
            )
            summary = {
                "pass": number,
                "stage": state["stage"],
                "state": state["state"],
                "model_calls": models.used,
                "acquisition": state["acquisition"],
            }
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            with (args.output / "phase-passes.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(summary, ensure_ascii=False) + "\n")
            (args.output / "status.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2)
            )
            if state["state"] == "blocked":
                reason = "blocked"
                break
            if state["stage"] == "authoring" and state["state"] == "running":
                reason = "awaiting_recipe_worker"
                break
            if state["stage"] == "active":
                # This opt-in verifier requests a fresh check of the already
                # approved source base. Do not fake a past due/last-success time
                # or count an idle scheduler pass as a real incremental fetch.
                with store.lock, Session(store.engine) as session, session.begin():
                    source, _ = store.approved(session, args.source)
                    resource = session.scalar(
                        select(Resource).where(
                            Resource.source_id == args.source,
                            Resource.normalized_url == source.base_url,
                        )
                    )
                    scan = session.get(ResourceScan, resource.id) if resource else None
                    if scan is not None and scan.state == "done":
                        scan.state, scan.available_at = "ready", utc_now()
                incremental_started = utc_now()
                state = await pipeline.incremental(args.source, limit=args.limit)
                with Session(store.engine) as session:
                    attempts = session.execute(
                        select(FetchAttempt, Resource.normalized_url)
                        .join(Resource, FetchAttempt.resource_id == Resource.id)
                        .where(
                            Resource.source_id == args.source,
                            FetchAttempt.started_at >= incremental_started,
                        )
                    ).all()
                    discoveries = session.scalars(
                        select(Discovery).where(
                            Discovery.discovered_at >= incremental_started,
                            Discovery.target_resource_id.in_(
                                select(Resource.id).where(Resource.source_id == args.source)
                            ),
                        )
                    ).all()
                    observations = [
                        {
                            "attempt_id": attempt.id,
                            "url": url,
                            "outcome": attempt.outcome.value,
                            "http_status": attempt.http_status,
                            "version_id": attempt.version_id,
                            "error_code": attempt.error_code,
                        }
                        for attempt, url in attempts
                    ]
                successful = [
                    item
                    for item in observations
                    if item["outcome"] in {"downloaded", "not_modified"}
                    and item["version_id"]
                    and not item["error_code"]
                ]
                state["verification_acquisitions"] = observations
                state["verification_successful_acquisitions"] = len(successful)
                state["verification_discovery_rows"] = len(discoveries)
                state["verification_trigger"] = "operator_requested_approved_base_recheck"
                (args.output / "incremental-status.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2)
                )
                # A changed classifier profile needs bounded incremental passes
                # over all current versions, not an early "done" on the first fetch.
                classification_pending = (
                    state["summary"].get("classification", {}).get("unassessed", 0)
                )
                audit_pending = state["summary"].get("audit", {}).get("remaining", 0)
                if state["state"] == "ready" and (classification_pending or audit_pending):
                    continue
                reason = (
                    "activated_and_incremental_checked"
                    if successful and state["stage"] == "active" and state["state"] == "ready"
                    else "incremental_not_verified"
                )
                break
            if models.failed_in_a_row >= 5:
                reason = "provider_failure_circuit"
                break
            if state["stage"] == "inventory":
                await asyncio.sleep(5)
        report = {
            "stop_reason": reason,
            "model_calls": models.used,
            "models": models.metadata,
            "source_status": store.status(args.source),
        }
        (args.output / "run-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(
            json.dumps({"stop_reason": reason, "model_calls": models.used}, ensure_ascii=False),
            flush=True,
        )
    finally:
        (args.output / "last-status.json").write_text(
            json.dumps(store.status(args.source), ensure_ascii=False, indent=2)
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--storage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--small-model", required=True)
    parser.add_argument("--large-model", required=True)
    parser.add_argument("--author-model", default=AUTHOR_MODEL)
    parser.add_argument("--local-classifier", type=Path)
    parser.add_argument("--classification-llm-fraction", type=float, default=0.05)
    parser.add_argument("--enable-models", required=True, action="store_true")
    parser.add_argument(
        "--phase", choices=["all", "classification", "authoring", "audit"], default="all"
    )
    parser.add_argument("--max-model-calls", type=int, default=1000)
    parser.add_argument("--max-passes", type=int, default=100)
    parser.add_argument("--limit", type=int, default=25)
    options = parser.parse_args()
    if not options.database.is_file():
        parser.error("Use an existing approved/onboarded source database")
    if (
        not 1 <= options.limit <= 100
        or not 1 <= options.max_model_calls <= 3000
        or not 1 <= options.max_passes <= 200
    ):
        parser.error("Invalid verification budget")
    asyncio.run(run(options))
