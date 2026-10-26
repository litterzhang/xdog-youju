"""Report a saved-source funnel to stdout; no network, model, migration or writes."""

import argparse
import json
from pathlib import Path

from youju.onboarding.funnel import report_source_funnel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Consistent SQLite backup")
    parser.add_argument("--blobs", type=Path, required=True, help="Existing saved blob directory")
    parser.add_argument("--source", required=True, help="Registered source ID")
    clocks = parser.add_mutually_exclusive_group(required=True)
    clocks.add_argument("--clock", help="Explicit ISO-8601 instant with UTC offset")
    clocks.add_argument("--clock-file", type=Path, help="UTF-8 file containing that ISO instant")
    parser.add_argument("--observations", type=Path, help="Normalized new-contract replay JSON")
    args = parser.parse_args()
    clock = args.clock if args.clock is not None else args.clock_file.read_text().strip()
    report = report_source_funnel(
        snapshot=args.snapshot,
        blobs=args.blobs,
        source_id=args.source,
        clock=clock,
        replay=json.loads(args.observations.read_bytes()) if args.observations else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
