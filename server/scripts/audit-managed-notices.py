"""Replay a managed script-only recipe on every classified saved notice.

Uses a private SQLite backup and immutable blob copies. No crawling, authoring,
model requests, callback delivery, source activation, or previous-output reuse.
"""

import argparse
import asyncio
import json
import re
import sqlite3
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from xdog.flow.events import NodeFailed, NodeFinished, NodeStarted

from youju.database import create_sqlite_engine
from youju.models import AuditResult, SourcePipeline, SourceRecipe, SourceRecipeBinding
from youju.models.base import utc_now
from youju.onboarding.auditing import AuditLedger, evaluation_day
from youju.onboarding.pipeline import RESOURCE_FAILURE_CODES
from youju.onboarding.recipe_versions import next_generation
from youju.onboarding.recipes import RecipeError, RecipeRunner, candidate_digest
from youju.onboarding.sandbox import Sandbox
from youju.onboarding.store import PipelineStore, json_bytes, toolkit_key
from youju.poc.storage import BlobStore
from youju.recipe_service import RecipeService


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


async def audit(args):
    candidate_result = RecipeService(args.recipe_storage).result(args.task_id)
    candidate = candidate_result["candidate"]
    recipe_digest = candidate_digest(candidate)
    if recipe_digest != candidate_result["digest"]:
        raise ValueError("Managed candidate digest mismatch")
    runner = RecipeRunner(Sandbox())
    errors = runner.validate(candidate)
    if errors:
        raise ValueError(f"Invalid recipe: {errors}")
    if any(n["run"] == "youju_toolkit:extract_detailed" for n in candidate["workflow"]["nodes"]):
        raise ValueError("This offline replay only supports fully script-based workflows")
    if not args.database.is_file() or not args.source_storage.is_dir():
        raise ValueError("Saved source database and blob directory must exist")
    # mkdir without exist_ok prevents accidentally overwriting an earlier audit.
    args.output.mkdir(parents=True)
    copied = args.output / "source-snapshot.sqlite3"
    with sqlite3.connect(f"{args.database.resolve().as_uri()}?mode=ro", uri=True) as source:
        with sqlite3.connect(copied) as destination:
            source.backup(destination)
    engine = create_sqlite_engine(copied)
    try:
        store = PipelineStore(engine, args.output / "blobs")
        original_blobs = BlobStore(args.source_storage)
        for path in original_blobs.root.iterdir():
            if re.fullmatch(r"[0-9a-f]{64}", path.name):
                store.blobs.put(original_blobs.read(path.name))
        with Session(engine) as session:
            pipeline = session.get(SourcePipeline, args.source)
            classifier_key = pipeline.summary["classification"]["classifier_key"]
            binding = session.get(SourceRecipeBinding, args.source)
            old_recipe = binding.active_recipe_id if binding else None
            old_generation = binding.generation if binding else 0
            generation = max(
                next_generation(session, args.source),
                old_generation + 1,
                pipeline.summary.get("candidate_generation", 0) + 1,
            )
            old_day = pipeline.summary.get("audit", {}).get("evaluated_on")
            previous = {
                row.bundle_id: row.status
                for row in session.scalars(
                    select(AuditResult).where(
                        AuditResult.recipe_id == old_recipe,
                        AuditResult.generation == old_generation,
                        AuditResult.evaluated_on == old_day,
                    )
                )
            }
        bundles = store.bundles(args.source, classifier_key=classifier_key, summaries=True)
        notices = [
            b
            for b in bundles
            if "article" in b.get("assessment", {}).get("page_roles", [])
            and b.get("assessment", {}).get("business_type")
            in {"recruitment", "correction", "cancellation"}
        ]
        if not notices:
            raise ValueError("No classified announcements found")
        body_ref = store.blobs.put(json_bytes(candidate))
        with Session(engine) as session, session.begin():
            recipe = session.scalar(
                select(SourceRecipe).where(
                    SourceRecipe.source_id == args.source, SourceRecipe.digest == recipe_digest
                )
            )
            if recipe is not None:
                raise ValueError("Recipe already exists in snapshot; use a fresh candidate")
            recipe = SourceRecipe(
                source_id=args.source,
                digest=recipe_digest,
                body_ref=body_ref,
                status="candidate",
                validation={"errors": [], "toolkit_key": toolkit_key(), "task_id": args.task_id},
            )
            session.add(recipe)
            session.flush()
            recipe_id = recipe.id
        ledger = AuditLedger(store)
        audit_id = ledger.begin(args.source, recipe_id, "saved-replay")
        stamp = utc_now()
        day = evaluation_day(stamp)
        save(args.output / "candidate.json", candidate)
        save(
            args.output / "request.json",
            {
                "source_id": args.source,
                "task_id": args.task_id,
                "recipe_digest": recipe_digest,
                "recipe_id": recipe_id,
                "audit_id": audit_id,
                "generation": generation,
                "classifier_key": classifier_key,
                "now_iso": stamp.isoformat(),
                "bundle_ids": [b["id"] for b in notices],
                "original_database": str(args.database.resolve()),
                "toolkit_key": toolkit_key(),
            },
        )
        semaphore = asyncio.Semaphore(args.concurrency)
        completed = 0

        async def one(summary):
            nonlocal completed
            async with semaphore:
                bundle = store.load_bundle(summary["id"], classifier_key=classifier_key)
                events = []
                extra = []
                try:
                    result = await runner.run(
                        candidate, bundle, now_iso=stamp.isoformat(), on_event=events.append
                    )
                except RecipeError as error:
                    extra = [
                        {
                            **detail,
                            "failure_code": error.code,
                            "scope": "resource"
                            if error.code in RESOURCE_FAILURE_CODES
                            else "source",
                        }
                        for detail in error.errors
                    ]
                    rejected = error.rejected_result or {}
                    result = {
                        "status": "failed",
                        "positions": deepcopy(rejected.get("positions", [])),
                        "errors": extra,
                        "validity": deepcopy(error.validated_validity)
                        if error.validated_validity is not None
                        else {
                            "status": "unknown",
                            "deadline": None,
                            "evidence_ids": [],
                            "reason": "Extraction failed; validity not established",
                        },
                    }
                outcome = ledger.record(
                    bundle, recipe_id, day, result, extra, generation=generation
                )
                outcome.update(
                    url=bundle["url"],
                    title=bundle["page"].get("title"),
                    previous_status=previous.get(bundle["id"]),
                    validity_detail=result["validity"],
                    node_starts=[e.node_id for e in events if isinstance(e, NodeStarted)],
                    node_finishes=[e.node_id for e in events if isinstance(e, NodeFinished)],
                    node_failures=[e.node_id for e in events if isinstance(e, NodeFailed)],
                )
                completed += 1
                print(
                    f"{completed}/{len(notices)} {outcome['status']} "
                    f"{outcome['position_count']} positions {bundle['url']}",
                    flush=True,
                )
                return outcome

        outcomes = await asyncio.gather(*(one(b) for b in notices))
        if {x["bundle_id"] for x in outcomes} != {b["id"] for b in notices}:
            raise RuntimeError("Announcement replay coverage mismatch")
        accepted = [
            x for x in outcomes if x["status"] in {"success", "partial"} and not x["errors"]
        ]
        report = {
            "source_id": args.source,
            "task_id": args.task_id,
            "recipe_digest": recipe_digest,
            "recipe_id": recipe_id,
            "audit_id": audit_id,
            "now_iso": stamp.isoformat(),
            "evaluated_on": day,
            "generation": generation,
            "total_announcements": len(notices),
            "processed": len(outcomes),
            "remaining": 0,
            "statuses": dict(Counter(x["status"] for x in outcomes)),
            "validity": dict(Counter(x["validity"] for x in outcomes)),
            "application_period": dict(
                Counter(
                    x["validity_detail"].get("application_period", {}).get("kind", "missing")
                    for x in outcomes
                )
            ),
            "accepted_position_count": sum(x["position_count"] for x in accepted),
            "complete_position_count": sum(
                x["position_count"] for x in accepted if x["status"] == "success"
            ),
            "partial_position_count": sum(
                x["position_count"] for x in accepted if x["status"] == "partial"
            ),
            "candidate_position_count": sum(x["position_count"] for x in outcomes),
            "errors_by_code": dict(Counter(e["code"] for x in outcomes for e in x["errors"])),
            "warnings_by_code": dict(Counter(e["code"] for x in outcomes for e in x["warnings"])),
            "regressions": [
                x["bundle_id"]
                for x in outcomes
                if x["previous_status"] in {"success", "partial"} and x not in accepted
            ],
            "full_execution_completed": True,
            "passed": len(accepted) == len(notices),
            "verification": "real-flow-and-sandbox-scripts-only",
            "model_calls": [],
            "new_downloads": 0,
            "source_activated": False,
            "results": outcomes,
        }
        ledger.finish(audit_id, report, complete=True)
        save(args.output / "audit-report.json", report)
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-storage", type=Path, required=True)
    parser.add_argument("--recipe-storage", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source", default="ustc-employment")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=2)
    asyncio.run(audit(parser.parse_args()))


if __name__ == "__main__":
    main()
