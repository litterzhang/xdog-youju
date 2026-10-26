"""Offline sidecar smoke/safety groups; all records and saved pages are synthetic."""

import copy
import hashlib
import json
import sqlite3

import pytest
from flask import Flask
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from youju.database import create_sqlite_engine, initialize_database
from youju.onboarding import classification_cli as cli
from youju.onboarding.classification_features import content_group, feature_text
from youju.poc.storage import BlobStore


@pytest.fixture
def runner():
    app = Flask(__name__)
    cli.register_classification_commands(app)
    return app.test_cli_runner()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return str(path)


def partitions():
    parts = {}
    for split, count in (("training", 10), ("calibration", 6), ("test", 3)):
        rows = []
        for role, business, base in (
            ("article", "recruitment", "公开招聘公告岗位报名学历专业资格报名材料截止时间"),
            ("navigation", "unrelated", "校园文化风景参观校史建筑图书馆交通线路校园介绍"),
        ):
            for index in range(count):
                text = f"{base} {split}独立示例{index}"
                rows.append(
                    {
                        "id": f"{split}:{role}:{index}",
                        "source_id": f"{split}-source",
                        "template_family": f"audited-{split}-cms",
                        "text": text,
                        "group": hashlib.sha256(text.encode()).hexdigest(),
                        "page_role": role,
                        "business_type": business,
                        "label_quality": "synthetic",
                    }
                )
        parts[split] = rows
    return parts


def train(runner, root):
    parts = partitions()
    for name, rows in parts.items():
        write(root / f"{name}.json", rows)
    result = runner.invoke(
        args=[
            "classifier-train",
            str(root / "training.json"),
            str(root / "calibration.json"),
            "--output",
            str(root / "model.json"),
            "--allow-synthetic",
        ]
    )
    assert result.exit_code == 0, result.output
    return parts


def test_offline_split_train_evaluate_explicit_synthetic_and_no_overwrite(runner, tmp_path):
    parts = partitions()
    input_path = write(tmp_path / "annotated.json", sum(parts.values(), []))
    split_args = ["classifier-split", input_path, "--output-dir", str(tmp_path / "split")]
    for name in parts:
        split_args += [f"--{name}-source", f"{name}-source"]
    assert runner.invoke(args=split_args).exit_code != 0
    result = runner.invoke(args=[*split_args, "--allow-synthetic"])
    assert result.exit_code == 0, result.output
    for name in parts:
        assert json.loads((tmp_path / "split" / f"{name}.json").read_text()) == parts[name]
    training, calibration = [
        str(tmp_path / "split" / f"{name}.json") for name in ("training", "calibration")
    ]
    model = tmp_path / "model.json"
    args = ["classifier-train", training, calibration, "--output", str(model)]
    assert runner.invoke(args=args).exit_code != 0
    assert not model.exists()
    result = runner.invoke(
        args=[
            *args,
            "--allow-synthetic",
            "--min-precision",
            "0.97",
            "--negative-precision",
            "0.999",
            "--min-support",
            "4",
        ]
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads(model.read_text())["model"]["metadata"]
    assert (metadata["min_precision"], metadata["negative_precision"], metadata["min_support"]) == (
        0.97,
        0.999,
        4,
    )
    original = model.read_bytes()
    assert runner.invoke(args=[*args, "--allow-synthetic"]).exit_code != 0
    assert model.read_bytes() == original
    report = tmp_path / "report.json"
    evaluate = [
        "classifier-evaluate",
        str(model),
        str(tmp_path / "split" / "test.json"),
        "--output",
        str(report),
    ]
    assert runner.invoke(args=evaluate).exit_code != 0
    assert not report.exists()
    result = runner.invoke(args=[*evaluate, "--allow-synthetic"])
    assert result.exit_code == 0, result.output
    metrics = json.loads(report.read_text())
    assert metrics["evaluation_kind"] == "synthetic_test"
    assert metrics["total"] == len(parts["test"])
    assert metrics["offline_cli"]["training_label_quality_counts"] == {"synthetic": 20}
    assert "not gold" in " ".join(metrics["offline_cli"]["caveats"])
    assert set(metrics["heads"]) == {"page_role", "business_type"}
    assert "synthetic is smoke-only" in result.output
    before = report.read_bytes()
    assert runner.invoke(args=[*evaluate, "--allow-synthetic"]).exit_code != 0
    assert report.read_bytes() == before


def test_evaluation_rejects_all_leakage_dimensions_silver_and_empty(runner, tmp_path):
    parts = train(runner, tmp_path)
    for field in ("id", "source_id", "template_family", "group", "text"):
        rows = copy.deepcopy(parts["test"])
        rows[0][field] = parts["training"][0][field]
        test = write(tmp_path / "leak.json", rows)
        result = runner.invoke(
            args=[
                "classifier-evaluate",
                str(tmp_path / "model.json"),
                test,
                "--output",
                str(tmp_path / "report.json"),
                "--allow-synthetic",
            ]
        )
        assert result.exit_code != 0 and "leakage" in result.output, result.output
        assert not (tmp_path / "report.json").exists()
    for rows, reason in (
        ([{**row, "label_quality": "silver"} for row in parts["test"]], "requires human"),
        ([], "nonempty"),
    ):
        result = runner.invoke(
            args=[
                "classifier-evaluate",
                str(tmp_path / "model.json"),
                write(tmp_path / "invalid-test.json", rows),
                "--output",
                str(tmp_path / "report.json"),
                "--allow-synthetic",
            ]
        )
        assert result.exit_code != 0 and reason in result.output, result.output
        assert not (tmp_path / "report.json").exists()


def saved_database(root):
    database = (
        root / "saved ?#.sqlite3"
    )  # URI metacharacters must be escaped, not parsed as options
    engine = create_sqlite_engine(database)
    initialize_database(engine)  # fixture setup only, never a CLI action
    engine.dispose()
    storage = root / "blobs"
    blobs = BlobStore(storage)
    bundle = {
        "id": "saved-page",
        "source_id": "source-a",
        "url": "https://metadata.example/a",
        "page": {
            "title": "公开招聘公告",
            "status": "ok",
            "errors": [],
            "truncated": False,
            "blocks": [
                {
                    "id": "body",
                    "text": "招聘岗位报名材料与专业条件说明。" * 10,
                    "locator": "body > p:1",
                }
            ],
            "links": [],
            "tables": [],
        },
        "attachments": [],
        "assessment": {"page_roles": ["navigation"], "business_type": "unrelated"},
    }
    reference = blobs.put(json.dumps(bundle, ensure_ascii=False).encode())
    # Minimal fixture rows in a complete schema. sqlite3 fixture connection does
    # not enable FK checks: acquisition/registry setup is outside this CLI test.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO resources (id, source_id, original_url, normalized_url, kind, "
            "review_status, first_seen_at, consecutive_failures) VALUES "
            "('resource-a', 'source-a', 'https://metadata.example/a', "
            "'https://metadata.example/a', 'unknown', 'pending', '2026-10-15', 0)"
        )
        connection.execute(
            "INSERT INTO resource_bundles (id, resource_id, page_version_id, digest, body_ref, "
            "status, dependencies, created_at) VALUES "
            "('saved-page', 'resource-a', 'version-a', ?, ?, 'complete', '[]', '2026-10-15')",
            (reference, reference),
        )
    return database, storage, bundle


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_export_is_sqlite_readonly_unlabeled_metadata_not_features(runner, tmp_path, monkeypatch):
    database, storage, bundle = saved_database(tmp_path)
    before = snapshot(tmp_path)
    statements = []
    original = cli.create_engine

    def readonly_engine(url):
        assert url.query == {"mode": "ro", "uri": "true"}
        engine = original(url)

        @event.listens_for(engine, "before_cursor_execute")
        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        with engine.connect() as connection, pytest.raises(OperationalError, match="readonly"):
            connection.exec_driver_sql("DELETE FROM resource_bundles")
        return engine

    monkeypatch.setattr(cli, "create_engine", readonly_engine)
    output = tmp_path / "annotation.json"
    args = [
        "classifier-export",
        "--database",
        str(database),
        "--storage",
        str(storage),
        "--source",
        "source-a",
        "--limit",
        "1",
        "--output",
        str(output),
    ]
    result = runner.invoke(args=args)
    assert result.exit_code == 0, result.output
    rows = json.loads(output.read_text())
    assert len(rows) == 1
    row = rows[0]
    assert row["template_family"] is row["page_role"] is row["business_type"] is None
    assert row["label_quality"] == "unlabeled"
    assert row["text"] == feature_text(bundle) and row["group"] == content_group(bundle)
    assert row["bundle_id"] == "saved-page" and row["url"] == bundle["url"]
    assert "metadata.example" not in row["text"] and "source-a" not in row["text"]
    assert all(sql.startswith("SELECT") for sql in statements[1:])  # test write failed above
    after = snapshot(tmp_path)
    assert {key: value for key, value in after.items() if key != "annotation.json"} == before
    assert runner.invoke(args=args).exit_code != 0
    filtered = [
        "classifier-export",
        "--database",
        str(database),
        "--storage",
        str(storage),
        "--source",
        "absent",
        "--output",
        str(tmp_path / "empty.json"),
    ]
    assert runner.invoke(args=filtered).exit_code == 0
    assert json.loads((tmp_path / "empty.json").read_text()) == []


def test_export_missing_or_empty_db_never_initializes_schema(runner, tmp_path):
    storage = tmp_path / "blobs"
    storage.mkdir()
    for name, exists in (("missing.sqlite3", False), ("empty.sqlite3", True)):
        database = tmp_path / name
        if exists:
            database.touch()
        before = snapshot(tmp_path)
        result = runner.invoke(
            args=[
                "classifier-export",
                "--database",
                str(database),
                "--storage",
                str(storage),
                "--output",
                str(tmp_path / "annotation.json"),
            ]
        )
        assert result.exit_code != 0
        assert snapshot(tmp_path) == before
        assert database.exists() == exists
        if exists:
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_strict_bounded_json_and_annotation_required(runner, tmp_path, monkeypatch):
    input_path = tmp_path / "invalid.json"
    output_dir = tmp_path / "split"
    args = [
        "classifier-split",
        str(input_path),
        "--training-source",
        "a",
        "--calibration-source",
        "b",
        "--test-source",
        "c",
        "--output-dir",
        str(output_dir),
    ]
    for data in ('[{"id":"a","id":"b"}]', '[{"score":NaN}]', '[{"score":1e999}]'):
        input_path.write_text(data)
        result = runner.invoke(args=args)
        assert result.exit_code != 0
        assert not output_dir.exists()
    unlabeled = {
        **partitions()["test"][0],
        "page_role": None,
        "business_type": None,
        "label_quality": "unlabeled",
        "template_family": None,
    }
    write(input_path, [unlabeled])
    assert runner.invoke(args=args).exit_code != 0
    assert not output_dir.exists()
    monkeypatch.setattr(cli, "MAX_CORPUS_BYTES", 32)
    input_path.write_text(" " * 33)
    result = runner.invoke(args=args)
    assert result.exit_code != 0 and "at most 32 bytes" in result.output
