"""Small CLI checks for repeatable imports and atomic rejection; no URLs are fetched."""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju import create_app
from youju.database import create_sqlite_engine
from youju.models import Region, RegistryStatus, Resource, ReviewStatus, Source

CATALOG = Path(__file__).resolve().parents[1] / "catalogs" / "ustc.json"


def test_cli_import_is_repeatable_and_preserves_review_state(tmp_path):
    database = tmp_path / "test.sqlite3"
    runner = create_app({"TESTING": True}).test_cli_runner()
    initialized = runner.invoke(args=["init-db", "--database", str(database)])
    assert initialized.exit_code == 0, initialized.output
    args = ["import-sources", str(CATALOG), "--database", str(database)]
    imported = runner.invoke(args=args)
    assert imported.exit_code == 0, imported.output
    assert "sources=1" in imported.output

    engine = create_sqlite_engine(database)
    try:
        with Session(engine) as session, session.begin():
            source = session.get(Source, "ustc-employment")
            assert source.publisher.name == "中国科学技术大学"
            assert source.status == RegistryStatus.CANDIDATE
            assert source.region_tags[0].name == "安徽省"
            source.status = RegistryStatus.PAUSED
            source.resources[0].review_status = ReviewStatus.REJECTED

        repeated = runner.invoke(args=args)
        assert repeated.exit_code == 0, repeated.output
        assert "sources=0" in repeated.output
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Resource)) == 1
            source = session.get(Source, "ustc-employment")
            assert source.status == RegistryStatus.PAUSED
            assert source.resources[0].review_status == ReviewStatus.REJECTED
    finally:
        engine.dispose()


def test_conflict_or_approval_field_rolls_back_the_entire_import(tmp_path):
    database = tmp_path / "test.sqlite3"
    runner = create_app({"TESTING": True}).test_cli_runner()
    assert runner.invoke(args=["init-db", "--database", str(database)]).exit_code == 0
    assert (
        runner.invoke(
            args=[
                "import-sources",
                str(CATALOG),
                "--database",
                str(database),
            ]
        ).exit_code
        == 0
    )
    original = json.loads(CATALOG.read_text())
    conflicting = tmp_path / "conflict.json"
    engine = create_sqlite_engine(database)
    try:
        # The region is inserted first; a later conflict or forbidden approval must undo it.
        for field, value in (("base_url", "https://other.example.test/"), ("status", "active")):
            data = json.loads(CATALOG.read_text())
            data["regions"].append({"code": "new-region", "name": "测试地区"})
            data["sources"][0][field] = value
            conflicting.write_text(json.dumps(data, ensure_ascii=False))
            result = runner.invoke(
                args=[
                    "import-sources",
                    str(conflicting),
                    "--database",
                    str(database),
                ]
            )
            assert result.exit_code != 0
            assert "Catalog import rejected" in result.output
            with Session(engine) as session:
                assert session.get(Region, "new-region") is None
                source = session.get(Source, "ustc-employment")
                assert source.base_url == original["sources"][0]["base_url"]
                assert source.status == RegistryStatus.CANDIDATE
    finally:
        engine.dispose()
