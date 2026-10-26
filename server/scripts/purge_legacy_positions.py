"""Delete legacy job entities after an exclusive backup, never original acquisition."""

import argparse
import fcntl
import json
import sqlite3
from pathlib import Path

from sqlalchemy.orm import Session

from youju.database import create_sqlite_engine
from youju.legacy_positions import purge_legacy_positions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    source = args.database.resolve(strict=True)
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with source.with_suffix(source.suffix + ".source.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with args.backup.open("xb"):
            pass
        args.backup.chmod(0o600)
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old,
            sqlite3.connect(args.backup) as backup,
        ):
            old.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity validation failed")
        engine = create_sqlite_engine(source)
        try:
            with Session(engine) as session, session.begin():
                result = purge_legacy_positions(session)
            print(json.dumps(result))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
