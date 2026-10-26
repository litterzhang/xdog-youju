"""Create a new v2 source DB without mutating the archived input or running models."""

import argparse
import json

from youju.deployment_migration import rebuild_contract_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild_contract_database(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
