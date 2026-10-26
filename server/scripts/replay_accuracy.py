"""Replay pinned saved cases with official Flow and real sandbox, without models/network.

Example (all paths explicit):
    uv run python scripts/replay_accuracy.py --corpus /audit/offline-corpus \
      --snapshot /audit/source-snapshot.sqlite3 --blobs /saved/source-blobs \
      --output /audit/accuracy-replay.json

The read-only source DB and immutable originals are never updated. Existing
predictions are diagnostics, not gold; this command does not measure accuracy.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from youju.onboarding.classification import Classifier
from youju.onboarding.periods import evaluate_period, extract_application_period
from youju.onboarding.recipes import RecipeError, RecipeRunner, template_candidate
from youju.onboarding.sandbox import Sandbox


def read_blob(root, reference):
    if not re.fullmatch(r"[0-9a-f]{64}", reference):
        raise ValueError("Invalid saved blob reference")
    path = root / reference
    if path.is_symlink():
        raise ValueError("Saved blob cannot be a symlink")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != reference:
        raise ValueError("Saved blob digest mismatch")
    return body


async def replay(args):
    root, blobs = args.corpus.resolve(), args.blobs.resolve()
    manifest = json.loads((root / "manifest.json").read_bytes())
    candidate = template_candidate(accuracy=True)
    sandbox = Sandbox()
    await sandbox.probe()
    originals = {}
    with sqlite3.connect(f"file:{args.snapshot.resolve()}?mode=ro", uri=True) as database:
        for row in database.execute("SELECT id, body_ref, media_type FROM resource_versions"):
            originals[row[0]] = (row[1], row[2])
    semaphore = asyncio.Semaphore(2)

    async def one(case):
        async with semaphore:
            filename = case["file"]
            if Path(filename).name != filename or not filename.endswith(".json"):
                raise ValueError("Invalid frozen case filename")
            raw = (root / filename).read_bytes()
            if hashlib.sha256(raw).hexdigest() != case["sha256"]:
                raise ValueError("Frozen corpus changed")
            bundle = json.loads(raw)
            page = bundle["page"]
            reparse = "not_html"
            if page.get("format") == "html" and page.get("version_id") in originals:
                reference, media = originals[page["version_id"]]
                body = read_blob(blobs, reference)
                parsed = await sandbox.parse(
                    body,
                    media_type=media or "text/html",
                    url=page["url"],
                    material_key=f"{page['resource_id']}:{reference}",
                )
                old_index = {b["id"]: b["text"] for b in page["blocks"]}
                new_index = {b["id"]: b["text"] for b in parsed["blocks"]}
                if old_index != new_index:
                    # A new parser namespace/rebinding is needed; do not silently
                    # attach old evidence IDs to different text.
                    reparse = "evidence_namespace_changed_saved_view_retained"
                else:
                    bundle["page"] = {**page, **parsed}
                    reparse = "same_original_same_evidence_region_metadata_added"
            assessment = await Classifier().classify(bundle)
            period = extract_application_period(bundle)
            validity, errors = evaluate_period(bundle, period, manifest["clock"])
            result = {
                "case_id": case["case_id"],
                "material_sha256": case["sha256"],
                "gold_status": case["gold_status"],
                "reparse": reparse,
                "classification": {
                    key: assessment[key]
                    for key in ("page_roles", "business_type", "requires_review", "method")
                },
                "period_kind": period["kind"],
                "expiry": validity["status"],
                "coarse_eligible": validity["coarse_eligible"],
                "period_errors": errors,
            }
            if (
                assessment["business_type"] == "recruitment"
                and "article" in assessment["page_roles"]
            ):
                try:
                    output = await RecipeRunner(sandbox).run(
                        candidate, bundle, now_iso=manifest["clock"]
                    )
                    result.update(
                        execution="completed",
                        quality_tier=output["quality_tier"],
                        extraction_status=output["status"],
                        position_count=len(output["positions"]),
                    )
                except RecipeError as error:
                    result.update(
                        execution="unfinished",
                        error_code=error.code,
                        diagnostic_codes=sorted({e["code"] for e in error.errors}),
                    )
            else:
                result["execution"] = "not_a_resolved_recruitment_article"
            return result

    cases = await asyncio.gather(*(one(case) for case in manifest["cases"]))
    return {
        "clock": manifest["clock"],
        "case_count": len(cases),
        "source_count": manifest["source_count"],
        "network_requests": 0,
        "model_calls": 0,
        "gold_status": "unlabeled_not_accuracy_evidence",
        "execution_counts": dict(Counter(row["execution"] for row in cases)),
        "expiry_counts": dict(Counter(row["expiry"] for row in cases)),
        "reparse_counts": dict(Counter(row["reparse"] for row in cases)),
        "quality_tier_counts": dict(
            Counter(row["quality_tier"] for row in cases if "quality_tier" in row)
        ),
        "unfinished_counts": dict(
            Counter(row["error_code"] for row in cases if "error_code" in row)
        ),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--blobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(replay(args))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
