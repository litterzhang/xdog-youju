"""Explicit small-batch independent Agent audit; dry-run by default.

Input is a list of at most three frozen cases. Live calls require --enable-models;
one request per new case identity, no automatic retries. The dedicated ledger has
no access to source/recipe publication tables. Never register these results as gold.
"""

import argparse
import asyncio
import json
from pathlib import Path

from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.recipe_service.model_audit import (
    AUDIT_MODEL,
    MAX_INPUT_BYTES,
    ModelAuditService,
    freeze_batch,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--provider", default="copilot")
    parser.add_argument("--max-cases", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--enable-models", action="store_true")
    args = parser.parse_args()
    with args.input.open("rb") as stream:
        body = stream.read(3 * MAX_INPUT_BYTES + 1)
    if len(body) > 3 * MAX_INPUT_BYTES:
        parser.error("Audit input exceeds the three-case bound; do not silently truncate")
    try:
        cases = freeze_batch(json.loads(body), max_cases=args.max_cases)
    except (ValueError, TypeError) as error:
        parser.error(str(error))
    if not args.enable_models:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "case_count": len(cases),
                    "model": AUDIT_MODEL,
                    "maximum_requests": len(cases),
                    "provider_calls": 0,
                    "ledger_created": False,
                    "next_action": "Explicit --enable-models is required",
                }
            )
        )
        return
    models = XdogModels(
        XdogConfig(
            provider=args.provider,
            small_model=AUDIT_MODEL,
            large_model=AUDIT_MODEL,
            author_model=AUDIT_MODEL,
            live_enabled=True,
        )
    )
    service = ModelAuditService(args.ledger, models, live_opt_in=True)
    try:
        result = asyncio.run(service.run_batch(cases, max_cases=args.max_cases))
        print(
            json.dumps(
                {
                    "model": AUDIT_MODEL,
                    "case_count": len(cases),
                    "results": result,
                    "publication_performed": False,
                    "human_gold_created": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
