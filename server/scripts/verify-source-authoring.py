"""Opt-in coding-Agent replay plus SQLite audit of saved official source samples.

This does not advance an incomplete source-wide baseline or claim activation.
The verification DB is a snapshot; it reuses the original immutable blob storage.
"""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine
from youju.models import AuditResult, ParseResult, Position, SourceRecipe
from youju.models.base import utc_now
from youju.onboarding.auditing import AuditLedger, evaluation_day
from youju.onboarding.author_prompt import AUTHOR_MODEL
from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.cli import _store
from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, candidate_digest
from youju.onboarding.sandbox import Sandbox
from youju.onboarding.store import PipelineStore, digest, json_bytes, toolkit_key
from youju.onboarding.xdog import XdogConfig, XdogModels


class ObservedRunner(RecipeRunner):
    """Keep successful real results for audit, avoiding another model request."""

    def __init__(self, sandbox, models):
        super().__init__(sandbox, models)
        self.results = {}

    async def run(self, candidate, bundle, **kwargs):
        result = await super().run(candidate, bundle, **kwargs)
        self.results[(candidate_digest(candidate), bundle["id"])] = result
        return result


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def audit_snapshot(args, store, samples, authored, runner, stamp):
    database = args.output / "verification.sqlite3"
    with sqlite3.connect(args.database) as source, sqlite3.connect(database) as target:
        source.backup(target)
    engine = create_sqlite_engine(database)
    proof = PipelineStore(engine, store.blobs.root)
    candidate = authored["candidate"]
    identity = digest({"candidate": candidate_digest(candidate), "toolkit": toolkit_key()})
    try:
        with Session(engine) as session, session.begin():
            recipe = session.scalar(
                select(SourceRecipe).where(
                    SourceRecipe.source_id == args.source, SourceRecipe.digest == identity
                )
            )
            if recipe is None:
                recipe = SourceRecipe(
                    source_id=args.source,
                    digest=identity,
                    body_ref=proof.blobs.put(json_bytes(candidate)),
                    status="validated",
                    samples=[sample["id"] for sample in samples],
                    coverage={"diagnostic_only": True},
                    validation={"errors": [], "toolkit_key": toolkit_key()},
                )
                session.add(recipe)
                session.flush()
            recipe_id = recipe.id
        ledger = AuditLedger(proof)
        results = [
            ledger.record(
                sample,
                recipe_id,
                evaluation_day(stamp),
                runner.results[(candidate_digest(candidate), sample["id"])],
            )
            for sample in samples
        ]
        with Session(engine) as session:
            positions = session.scalar(
                select(func.count(Position.id))
                .join(ParseResult)
                .join(AuditResult, AuditResult.parse_id == ParseResult.id)
                .where(AuditResult.recipe_id == recipe_id)
            )
        return {
            "database": str(database.resolve()),
            "storage": str(store.blobs.root),
            "recipe_id": recipe_id,
            "results": results,
            "stored_positions": positions,
            "passed": all(item["status"] == "success" and not item["errors"] for item in results),
            "source_activated": False,
        }
    finally:
        engine.dispose()


async def verify(args, store):
    urls = set(args.sample_url)
    if not 1 <= len(urls) <= 3:
        raise ValueError("Select one to three distinct saved announcement URLs")
    selected = [item for item in store.bundles(args.source, summaries=True) if item["url"] in urls]
    if len(selected) != len(urls) or any(item["status"] != "complete" for item in selected):
        raise ValueError("All selected URLs must have complete saved bundles in this source")
    samples = [store.load_bundle(item["id"]) for item in sorted(selected, key=lambda b: b["url"])]
    models = XdogModels(
        XdogConfig(
            provider=args.provider,
            small_model=args.small_model,
            large_model=args.large_model,
            author_model=args.author_model,
            live_enabled=True,
        )
    )
    workspace = AuthorWorkspace(args.output / "authoring")
    # Diagnostic replay gets its own directory; never bypass the live assignment lock.
    live_root = store.blobs.root / "authoring" / digest({"source_id": args.source})
    if live_root.is_dir():
        from youju.recipe_service import RecipeService

        service = RecipeService(store.blobs.root)
        try:
            with service._assignment(live_root.name):
                previous = AuthorWorkspace(live_root)
                workspace.write_candidate(previous.load_candidate())
        finally:
            service.close()
    runner, stamp = ObservedRunner(Sandbox(), models), utc_now()
    report = {
        "started_at": stamp.isoformat(),
        "source_id": args.source,
        "sample_urls": sorted(urls),
        "workspace": str(workspace.root),
        "diagnostic_only": True,
        "full_source_audit": False,
        "max_turns": None,
    }

    async def on_attempt(attempt):
        save_json(args.output / f"attempt-{attempt['number']}.json", attempt)
        save_json(args.output / "model-calls.json", models.calls)
        print(
            json.dumps(
                {
                    "attempt": attempt["number"],
                    "status": attempt["status"],
                    "errors": sorted({error["code"] for error in attempt["errors"]}),
                }
            ),
            flush=True,
        )

    try:
        authored = await RecipeAuthor(models, runner, workspace=workspace).author(
            samples, now_iso=stamp.isoformat(), on_attempt=on_attempt
        )
        save_json(args.output / "accepted-candidate.json", authored["candidate"])
        report.update(authored=True, attempts=len(authored["attempts"]))
        report["database_audit"] = audit_snapshot(args, store, samples, authored, runner, stamp)
        report["success"] = (
            report["database_audit"]["passed"] and report["database_audit"]["stored_positions"] > 0
        )
    except RecipeError as error:
        report.update(success=False, error=error.as_dict())
    except asyncio.CancelledError:
        report.update(success=False, error={"code": "CANCELLED"})
        raise
    finally:
        report.update(
            finished_at=utc_now().isoformat(),
            model_calls=models.calls,
            source_stage=store.status(args.source)["stage"],
        )
        save_json(args.output / "report.json", report)
        print(
            json.dumps(
                {
                    "success": report.get("success"),
                    "error": report.get("error", {}).get("code"),
                    "stored_positions": report.get("database_audit", {}).get("stored_positions"),
                    "report": str(args.output / "report.json"),
                }
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-url", action="append", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--small-model", required=True)
    parser.add_argument("--large-model", required=True)
    parser.add_argument("--author-model", default=AUTHOR_MODEL)
    parser.add_argument("--enable-models", action="store_true", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with _store(args.database, args.storage) as store:
        asyncio.run(verify(args, store))


if __name__ == "__main__":
    main()
