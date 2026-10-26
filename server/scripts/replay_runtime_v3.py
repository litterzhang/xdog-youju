"""Read-only saved-source host-template replay, never a source activation or accuracy test."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from youju.notices.routing import decide_route
from youju.onboarding.sandbox import Sandbox
from youju.positions.normalization import normalize_extraction
from youju.recipe_runtime.contracts import candidate_digest, template_candidate
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_runtime.runner import RecipeRunner


class Recorder:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.draft = None

    async def run_script(self, files, module, function, inputs):
        self.draft = await self.sandbox.run_script(files, module, function, inputs)
        return self.draft


async def replay(args):
    prior = json.loads(args.selection.read_text())
    snapshot = Path(prior["snapshot"])
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if snapshot_hash != prior["snapshot_sha256"]:
        raise ValueError("Saved-source snapshot changed")
    if datetime.fromisoformat(args.now).utcoffset() is None:
        raise ValueError("An aware evaluation clock is required")
    blobs = Path(prior["blobs"])
    sandbox = Sandbox()
    await sandbox.probe()
    candidate = template_candidate()
    selected = [row for row in prior["records"] if row.get("host_period") is not None]
    semaphore = asyncio.Semaphore(4)

    async def one(row):
        async with semaphore:
            reference = row["bundle_body_ref"]
            body = (blobs / reference).read_bytes()
            if len(body) > 16 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != reference:
                raise ValueError("Pinned material digest changed")
            bundle = json.loads(body)
            bundle.update(
                id=row["bundle_id"],
                source_id=row["source_id"],
                resource_id=row["resource_id"],
                url=row["url"],
            )
            # Reuse bound classification selection only, never period, route, facts or draft.
            bundle["assessment"] = row["classification"]
            routing = decide_route(
                bundle, row["classification"], now=datetime.fromisoformat(args.now)
            ).as_dict()
            recorder = Recorder(sandbox)
            runner = RecipeRunner(recorder)
            record = {
                "resource_id": row["resource_id"],
                "bundle_id": row["bundle_id"],
                "material_sha256": reference,
                "routing": routing,
            }
            try:
                result = await runner.run(candidate, bundle, routing=routing)
                record.update(execution="completed", result=result)
            except RecipeError as error:
                # Preserve available draft facts as diagnostics, not accepted runtime output.
                preview = normalize_extraction(recorder.draft) if recorder.draft else None
                record.update(
                    execution="unfinished", error=error.as_dict(), diagnostic_normalization=preview
                )
            return record

    records = await asyncio.gather(*(one(row) for row in selected))
    routes = Counter(row["routing"]["category"] for row in records)
    executions = Counter(row["execution"] for row in records)
    errors = Counter(row["error"]["code"] for row in records if row.get("error"))
    entries = Counter()
    exact_deadlines = 0
    for row in records:
        result = row.get("result") or row.get("diagnostic_normalization") or {"positions": []}
        entries[row["routing"]["category"]] += len(result["positions"])
        exact_deadlines += sum(
            position["deadline_on"]["tag"] == "source" for position in result["positions"]
        )
    report = {
        "schema_version": "runtime-boundary-replay-v1",
        "clock": args.now,
        "selection": str(args.selection.resolve()),
        "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
        "snapshot_sha256": snapshot_hash,
        "candidate_digest": candidate_digest(candidate),
        "recipe_origin": "host_template_not_new_agent_authored_source_recipe",
        "input_inventory": prior["inventory"],
        "notice_candidates": len(records),
        "routing_categories": dict(routes),
        "runtime_executions": dict(executions),
        "errors": dict(errors),
        "position_entries_by_route_including_unpublished_diagnostics": dict(entries),
        "source_deadline_entries_including_unpublished_diagnostics": exact_deadlines,
        "network_requests": 0,
        "model_calls": 0,
        "prior_temporal_or_position_outputs_reused": 0,
        "limits": [
            "Unlabelled saved-source observations, not measured accuracy",
            "Unfinished diagnostic positions are not completed/accepted/public results",
            "Route categories are fallible cost decisions, not precise job validity",
            "No new downloads, production changes or source activation",
        ],
        "records": records,
    }
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == snapshot_hash
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "records"},
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.selection.resolve() or args.output.exists():
        raise ValueError("A new explicit output path is required")
    asyncio.run(replay(args))


if __name__ == "__main__":
    main()
