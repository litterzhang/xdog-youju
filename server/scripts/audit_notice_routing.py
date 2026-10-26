"""Re-evaluate a saved routing category using current rules and frozen local HTML.

No network/model calls, source writes, Recipe authoring or job-date changes. Output
is an inspection receipt, not authoritative publication/expiry or accuracy gold.
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from replay_source_accuracy import aware_iso, evidence_view, read_blob, write_report

from youju.notices import decide_route
from youju.onboarding.sandbox import Sandbox


async def audit(args):
    previous_bytes = args.report.read_bytes()
    previous = json.loads(previous_bytes)
    blobs = Path(previous["blobs"])
    sandbox = Sandbox()
    records = []
    for old in previous["records"]:
        if (old.get("routing") or {}).get("category") != args.category:
            continue
        bundle = json.loads(read_blob(blobs, old["bundle_body_ref"]))
        page = bundle.get("page") or {}
        metadata_reparsed = False
        if page.get("format") == "html" and page.get("sha256"):
            body = read_blob(blobs, page["sha256"])
            parsed = await sandbox.parse(
                body,
                media_type="text/html",
                url=page.get("url", old["url"]),
                material_key=f"{old['resource_id']}:{page['sha256']}",
            )
            if evidence_view(page) != evidence_view(parsed):
                raise ValueError("Saved evidence changed; do not silently rebind block IDs")
            # Date observations only; capture diagnostics and body evidence stay pinned.
            page["date_hints"] = parsed.get("date_hints", [])
            metadata_reparsed = True
        new = decide_route(bundle, old.get("classification"), now=datetime.fromisoformat(args.now))
        records.append(
            {
                "resource_id": old["resource_id"],
                "url": old["url"],
                "title": page.get("title"),
                "bundle_body_ref": old["bundle_body_ref"],
                "metadata_reparsed": metadata_reparsed,
                "before": old["routing"],
                "after": new.as_dict(),
            }
        )
    return {
        "schema_version": "notice-routing-inspection-v1",
        "previous_report_sha256": hashlib.sha256(previous_bytes).hexdigest(),
        "clock": args.now,
        "network_requests": 0,
        "model_calls": 0,
        "job_facts_modified": False,
        "input_category": args.category,
        "count": len(records),
        "categories": dict(Counter(row["after"]["category"] for row in records)),
        "reasons": dict(Counter(row["after"]["reason"] for row in records)),
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", type=aware_iso, required=True)
    parser.add_argument("--category", default="undated_high_priority")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; retain earlier inspection receipts")
    result = asyncio.run(audit(args))
    write_report(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))


if __name__ == "__main__":
    main()
