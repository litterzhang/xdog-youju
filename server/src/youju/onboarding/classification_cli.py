"""Offline classifier sidecar: explicit files, read-only SQLite, no activation."""

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import click
from sqlalchemy import URL, create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from youju.models import Resource, ResourceBundle

from .classification_corpus import split_corpus, validate_records
from .classification_features import content_group, feature_text
from .local_classifier import (
    MAX_ARTIFACT_BYTES,
    MAX_CORPUS_BYTES,
    MAX_RECORDS,
    _object,
    _reject_constant,
)
from .store import PipelineStore

_INPUT = click.Path(exists=True, dir_okay=False, path_type=Path)
_OUTPUT = click.Path(dir_okay=False, path_type=Path)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _loads(data):
    return json.loads(
        data, object_pairs_hook=_object, parse_constant=_reject_constant, parse_float=_finite_float
    )


def _read_bytes(path, limit):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError(f"Expected a regular JSON file of at most {limit} bytes: {path}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("JSON byte limit exceeded")
    return data


def _records(path, *, allow_synthetic=False):
    return validate_records(
        _loads(_read_bytes(path, MAX_CORPUS_BYTES)), allow_synthetic=allow_synthetic
    )


def _new_output(path):
    # lexists also protects dangling symlinks; publication below is exclusive too.
    if os.path.lexists(path):
        raise ValueError(f"Output already exists (choose a new path): {path}")
    if not path.parent.is_dir():
        raise ValueError(f"Output parent directory must exist: {path.parent}")


@contextmanager
def _output_file(path):
    """Publish a completed file without replacing any existing file, including races."""
    _new_output(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".classifier-")
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path, value, *, limit=MAX_ARTIFACT_BYTES):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    if len(data) > limit:
        raise ValueError("Output JSON byte limit exceeded")
    with _output_file(path) as temporary:
        temporary.write_bytes(data)


def _errors(function):
    @wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (
            OSError,
            ValueError,
            TypeError,
            RecursionError,
            OverflowError,
            SQLAlchemyError,
        ) as exc:
            raise click.ClickException(str(exc)) from exc

    return run


class _ReadOnlyBlobs:
    """No mkdir/write methods; bound and strictly check saved JSON before store decoding."""

    def __init__(self, root):
        self.root = root.resolve()

    def read(self, reference):
        if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
            raise ValueError("Invalid blob reference")
        path = self.root / reference
        if path.is_symlink():
            raise ValueError("Blob references must not be symlinks")
        data = _read_bytes(path, MAX_CORPUS_BYTES)
        if hashlib.sha256(data).hexdigest() != reference:
            raise ValueError("Stored content digest mismatch")
        if not isinstance(_loads(data), dict):
            raise ValueError("Expected a saved bundle JSON object")
        return data


def _export(database, storage, sources, limit):
    if not database.is_file():
        raise ValueError("Existing SQLite database required; export never initializes a schema")
    if not storage.is_dir():
        raise ValueError("Existing blob storage directory required")
    # Deliberately do NOT use onboarding.cli._store/create_sqlite_engine: those
    # initialize schemas or configure a writable connection. URI mode is enforced
    # by SQLite itself, not merely a convention that this code issues SELECTs.
    engine = create_engine(
        URL.create(
            "sqlite+pysqlite",
            database=database.resolve().as_uri(),
            query={"mode": "ro", "uri": "true"},
        )
    )
    try:
        store = PipelineStore(engine, storage)  # storage already exists; no new directory
        store.blobs = _ReadOnlyBlobs(storage)
        query = (
            select(ResourceBundle.id, Resource.source_id, Resource.normalized_url)
            .join(Resource, ResourceBundle.resource_id == Resource.id)
            .order_by(Resource.source_id, ResourceBundle.id)
            .limit(limit)
        )
        if sources:
            query = query.where(Resource.source_id.in_(sources))
        with Session(engine) as session:
            identifiers = session.execute(query).all()
        rows, total_bytes = [], 0
        for bundle_id, source_id, url in identifiers:
            bundle = store.load_bundle(bundle_id)
            try:
                text, group = feature_text(bundle), content_group(bundle)
            except (AttributeError, KeyError, TypeError) as exc:
                raise ValueError(f"Malformed saved page features: {bundle_id}") from exc
            row = {
                "id": bundle_id,
                "source_id": source_id,
                "template_family": None,
                "group": group,
                "text": text,
                "page_role": None,
                "business_type": None,
                "label_quality": "unlabeled",
                "bundle_id": bundle_id,
                "url": url,
            }
            validate_records([row], require_labels=False)
            total_bytes += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if total_bytes > MAX_CORPUS_BYTES:
                raise ValueError("Export corpus byte limit exceeded; choose a smaller --limit")
            rows.append(row)
        validate_records(rows, require_labels=False)
        return rows
    finally:
        engine.dispose()


def register_classification_commands(app):
    """Register only offline commands; the application factory owns integration."""

    @app.cli.command("classifier-export")
    @click.option("--database", required=True, type=_INPUT)
    @click.option(
        "--storage", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option("--source", "sources", multiple=True, help="Filter registered source IDs.")
    @click.option("--limit", default=500, show_default=True, type=click.IntRange(1, MAX_RECORDS))
    @click.option("--output", required=True, type=_OUTPUT)
    @_errors
    def export(database, storage, sources, limit, output):
        """Export saved page features for manual annotation; never fetch or label pages."""
        _new_output(output)
        if output.resolve().is_relative_to(storage.resolve()):
            raise ValueError("Annotation output must be outside the read-only blob storage")
        rows = _export(database, storage, sources, limit)
        _write_json(output, rows, limit=MAX_CORPUS_BYTES)
        click.echo(f"Exported {len(rows)} unlabeled records. Annotate labels AND template_family.")

    @app.cli.command("classifier-split")
    @click.argument("input", type=_INPUT)
    @click.option("--training-source", multiple=True, required=True)
    @click.option("--calibration-source", multiple=True, required=True)
    @click.option("--test-source", multiple=True, required=True)
    @click.option("--output-dir", required=True, type=click.Path(file_okay=False, path_type=Path))
    @click.option("--allow-synthetic", is_flag=True)
    @_errors
    def split(input, training_source, calibration_source, test_source, output_dir, allow_synthetic):
        """Split annotated records by explicit disjoint source/template/content identities."""
        partitions = split_corpus(
            _records(input, allow_synthetic=allow_synthetic),
            training_sources=training_source,
            calibration_sources=calibration_source,
            test_sources=test_source,
            allow_synthetic=allow_synthetic,
        )
        if not all(partitions.values()):
            raise ValueError("Training, calibration and test partitions must all be nonempty")
        if not output_dir.exists():
            output_dir.mkdir()  # explicit output only; do not create arbitrary parent trees
        for name in partitions:
            _new_output(output_dir / f"{name}.json")
        for name, rows in partitions.items():
            _write_json(output_dir / f"{name}.json", rows, limit=MAX_CORPUS_BYTES)
        click.echo("Wrote training.json, calibration.json, test.json; no model activated.")

    @app.cli.command("classifier-train")
    @click.argument("training", type=_INPUT)
    @click.argument("calibration", type=_INPUT)
    @click.option("--output", required=True, type=_OUTPUT)
    @click.option("--min-precision", default=0.98, show_default=True, type=click.FloatRange(0, 1))
    @click.option(
        "--negative-precision", default=0.995, show_default=True, type=click.FloatRange(0, 1)
    )
    @click.option(
        "--min-support", default=5, show_default=True, type=click.IntRange(1, MAX_RECORDS)
    )
    @click.option("--allow-synthetic", is_flag=True)
    @_errors
    def train(
        training,
        calibration,
        output,
        min_precision,
        negative_precision,
        min_support,
        allow_synthetic,
    ):
        """Fit and calibrate a candidate JSON artifact locally; never install/activate it."""
        from .shared_classifier import SharedPageModel

        _new_output(output)
        model = SharedPageModel.fit(
            _records(training, allow_synthetic=allow_synthetic),
            _records(calibration, allow_synthetic=allow_synthetic),
            min_precision=min_precision,
            negative_precision=negative_precision,
            min_support=min_support,
            allow_synthetic=allow_synthetic,
        )
        with _output_file(output) as temporary:
            model.save(temporary)
        click.echo("Saved offline candidate only. Calibration precision is not held-out accuracy.")

    @app.cli.command("classifier-evaluate")
    @click.argument("model", type=_INPUT)
    @click.argument("test", type=_INPUT)
    @click.option("--output", required=True, type=_OUTPUT)
    @click.option("--allow-synthetic", is_flag=True)
    @_errors
    def evaluate(model, test, output, allow_synthetic):
        """Evaluate true unseen human holdout (or explicitly opted-in synthetic smoke data)."""
        from .shared_classifier import MAX_ARTIFACT_BYTES as MAX_MODEL_BYTES
        from .shared_classifier import SharedPageModel, evaluate_model

        _new_output(output)
        # Check JSON syntax and size here as well as the model's artifact schema.
        _loads(_read_bytes(model, MAX_MODEL_BYTES))
        rows = _records(test, allow_synthetic=allow_synthetic)
        if not rows:
            raise ValueError("Test holdout must be nonempty")
        candidate = SharedPageModel.load(model)
        report = evaluate_model(candidate, rows, allow_synthetic=allow_synthetic)
        report["offline_cli"] = {
            "training_label_quality_counts": candidate.metadata["training"]["label_quality_counts"],
            "calibration_label_quality_counts": candidate.metadata["calibration"][
                "label_quality_counts"
            ],
            "caveats": [
                "Human label provenance and template families are declarations requiring audit.",
                "Silver labels may train but are not human-gold calibration or evaluation.",
                "Synthetic data is smoke-test evidence, not gold or generic accuracy evidence.",
                "Revisit is host policy, not a classifier prediction; no model was activated.",
                "Old binary USTC results do not prove shared cross-site accuracy.",
                "95% cheap coverage is a cost target only; coverage is not accuracy.",
            ],
        }
        _write_json(output, report)
        click.echo(
            "Saved offline report. Declared human provenance still requires audit; "
            "silver is not human gold, synthetic is smoke-only. Coverage is not accuracy."
        )

    @app.cli.command("classifier-evaluate-pipeline")
    @click.argument("model", type=_INPUT)
    @click.argument("test", type=_INPUT)
    @click.option("--database", required=True, type=_INPUT)
    @click.option(
        "--storage", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
    )
    @click.option("--output", required=True, type=_OUTPUT)
    @click.option("--allow-synthetic", is_flag=True)
    @_errors
    def evaluate_pipeline_command(model, test, database, storage, output, allow_synthetic):
        """Evaluate rules/shared/host gates on frozen saved material; no LLM or writes."""
        from .classification_evaluation import evaluate_pipeline
        from .shared_classifier import MAX_ARTIFACT_BYTES as SHARED_LIMIT
        from .shared_classifier import SharedPageModel

        _new_output(output)
        selected = SharedPageModel.load(model)
        rows = _records(test, allow_synthetic=allow_synthetic)
        if not rows:
            raise ValueError("Nonempty unseen test corpus required")
        engine = create_engine(
            URL.create(
                "sqlite+pysqlite",
                database=database.resolve().as_uri(),
                query={"mode": "ro", "uri": "true"},
            )
        )
        try:
            store = PipelineStore(engine, storage)
            store.blobs = _ReadOnlyBlobs(storage)
            cache = {}

            def load_bundle(identifier, source_id):
                if identifier not in cache:
                    with Session(engine) as session:
                        actual = session.scalar(
                            select(Resource.source_id)
                            .join(ResourceBundle)
                            .where(ResourceBundle.id == identifier)
                        )
                    if actual != source_id:
                        raise ValueError("Test source identity does not match the saved bundle")
                    cache[identifier] = store.load_bundle(identifier)
                return cache[identifier]

            report = evaluate_pipeline(selected, rows, load_bundle, allow_synthetic=allow_synthetic)
            _write_json(output, report, limit=SHARED_LIMIT)
            click.echo(
                "Combined rules/shared/host evaluation saved; no external calls or activation."
            )
        finally:
            engine.dispose()
