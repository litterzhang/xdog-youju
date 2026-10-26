"""Synthetic saved material and real isolated Flow; no live DB or provider."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_jobs_api import saved  # noqa: F401
from test_recipe_accuracy import SandboxDouble, material
from test_source_funnel import snapshot as funnel_snapshot  # noqa: F401

from youju.onboarding.classification import Classifier
from youju.onboarding.materials import parse_material
from youju.onboarding.recipes import template_candidate
from youju.onboarding.regions import REGION_VERSION
from youju.onboarding.sandbox import Sandbox, ScriptExecutionError
from youju.positions.parsing import extract_position_facts

SPEC = importlib.util.spec_from_file_location(
    "replay_source_accuracy", Path(__file__).parents[1] / "scripts/replay_source_accuracy.py"
)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)
NOW = "2026-10-24T13:55:03+08:00"


def blob(root, value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    key = hashlib.sha256(raw).hexdigest()
    (root / key).write_bytes(raw)
    return key


def snapshot(tmp_path, count=45):
    root = tmp_path / "blobs"
    root.mkdir()
    path = tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE sources (id TEXT PRIMARY KEY);
            CREATE TABLE resources (id TEXT PRIMARY KEY,source_id TEXT,normalized_url TEXT);
            CREATE TABLE resource_versions (id TEXT PRIMARY KEY,resource_id TEXT,
                body_ref TEXT,media_type TEXT);
            CREATE TABLE resource_bundles (id TEXT PRIMARY KEY,resource_id TEXT,
                page_version_id TEXT,body_ref TEXT,status TEXT,created_at TEXT);
            CREATE TABLE resource_scans (resource_id TEXT PRIMARY KEY,bundle_id TEXT,
                state TEXT,last_version_id TEXT,last_document_id TEXT);
            INSERT INTO sources VALUES ('source-1');
            INSERT INTO resources VALUES ('gap','source-1','https://example.org/missing');
        """)
        for i in range(count):
            value = material()
            value.update(id=f"b{i}", resource_id=f"r{i}", source_id="source-1")
            value["assessment"] = {"business_type": "unrelated"}
            value["page"].pop("version_id", None)
            value["page"]["structure"] = {"version": REGION_VERSION, "regions": []}
            connection.execute(
                "INSERT INTO resources VALUES (?, 'source-1', ?)",
                (f"r{i}", f"https://example.org/{i}"),
            )
            connection.execute(
                "INSERT INTO resource_versions VALUES (?, ?, ?, 'text/html')",
                (f"v{i}", f"r{i}", blob(root, b"<p>unused</p>")),
            )
            connection.execute(
                "INSERT INTO resource_bundles VALUES (?, ?, ?, ?, 'complete', ?)",
                (f"b{i}", f"r{i}", f"v{i}", blob(root, value), "2026-10-24"),
            )
            connection.execute(
                "INSERT INTO resource_scans VALUES (?, ?, 'done', ?, NULL)",
                (f"r{i}", f"b{i}", f"v{i}"),
            )
    return path, root


class ReplaySandbox(SandboxDouble):
    async def probe(self):
        return {"ok": True}


@pytest.mark.parametrize("value", ["2026-10-24", "2026-10-24T12:00:00", "invalid", None])
def test_clock_requires_aware_iso(value):
    with pytest.raises(argparse.ArgumentTypeError, match="timezone-aware"):
        replay.aware_iso(value)


@pytest.mark.parametrize("value", ["0", "5", "-1", "1.5"])
def test_concurrency_bound(value):
    with pytest.raises(argparse.ArgumentTypeError):
        replay.concurrency(value)


def test_snapshot_ro_and_wal_rejected(tmp_path):
    path, _ = snapshot(tmp_path, 1)
    before = path.read_bytes()
    connection = replay.connect_snapshot(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("INSERT INTO sources VALUES ('not-allowed')")
    connection.close()
    assert path.read_bytes() == before
    assert not Path(str(path) + "-shm").exists()
    Path(str(path) + "-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="WAL"):
        replay.connect_snapshot(path)


def test_blob_rejects_traversal_corruption_symlink(tmp_path):
    reference = blob(tmp_path, b"saved")
    assert replay.read_blob(tmp_path, reference) == b"saved"
    with pytest.raises(ValueError, match="reference"):
        replay.read_blob(tmp_path, "../outside")
    (tmp_path / reference).write_bytes(b"modified")
    with pytest.raises(ValueError, match="digest"):
        replay.read_blob(tmp_path, reference)
    (tmp_path / reference).unlink()
    (tmp_path / reference).symlink_to(tmp_path / "outside")
    with pytest.raises(OSError):
        replay.read_blob(tmp_path, reference)


def test_all_latest_saved_not_40_and_reclassified(tmp_path, monkeypatch):
    path, root = snapshot(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO resource_bundles SELECT 'unselected',resource_id,"
            "page_version_id,body_ref,status,'2099-01-01' "
            "FROM resource_bundles WHERE id='b0'"
        )
        connection.execute("UPDATE resource_scans SET bundle_id=NULL WHERE resource_id='r1'")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(replay, "Sandbox", ReplaySandbox)
    # The compact synthetic schema is intentionally not the production ORM schema.
    monkeypatch.setattr(replay, "normalized_funnel", lambda *args: {"tested_separately": True})
    args = SimpleNamespace(
        snapshot=path,
        blobs=root,
        source_id="source-1",
        now=NOW,
        candidate=None,
        concurrency=4,
        local_classifier=None,
        output=tmp_path / "new-report.json",
    )
    report = asyncio.run(replay.replay(args))
    assert len(report["records"]) == 45
    assert report["inventory"]["saved_bundle_version_count"] == 46
    assert report["inventory"]["resources_without_saved_bundle"] == 1
    assert report["candidate"]["origin"] == "host_template_not_agent_authored_source_recipe"
    assert report["gold_status"] == "unlabelled_observations_not_accuracy"
    assert report["counts"]["accepted_positions"] == 45
    assert report["counts"]["execution"] == {"completed": 45}
    first = next(row for row in report["records"] if row["resource_id"] == "r0")
    assert first["bundle_id"] == "b0"
    assert first["classification"]["business_type"] == "recruitment"
    assert first["result"]["schema_version"] == "position-facts-v3"
    assert first["routing"]["category"] == "fixed_active"
    assert first["execution"]["runtime_contract"] == "recipe-runtime-v3"
    assert "host_period" not in first and "recipe_validity" not in first
    assert report["counts"]["denominators"]["source_resources"] == 46
    assert report["counts"]["denominators"]["unclassified_saved_bundles"] == 0
    assert report["inventory"]["resources_without_saved_version"] == 1
    assert report["counts"]["position_calendar_status"] == {"not_expired": 45}
    assert report["model_calls"] == report["network_requests"] == 0
    assert report["runtime_fingerprint"] == replay.runtime_fingerprint()
    assert report["source_audit_policy"] == "position-facts-admission-v1"
    assert all(
        row["executed_runtime_fingerprint"] == report["runtime_fingerprint"]
        and row["executed_candidate_digest"] == report["candidate"]["digest"]
        for row in report["records"]
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    replay.write_report(tmp_path / "report.json", report)
    assert not list(tmp_path.glob(".source-replay-*"))


def run_record(tmp_path, sandbox, value=None, classifier=None):
    value = value or material()
    value["page"]["structure"] = {"version": REGION_VERSION, "regions": []}
    saved_record = {
        "id": "bundle-v1",
        "resource_id": "notice-1",
        "page_version_id": "page-v1",
        "body_ref": blob(tmp_path, value),
        "url": "https://example.org/notice",
        "status": value["status"],
        "selection": "scan_bundle_pointer",
        "scan_state": "done",
        "warnings": [],
    }
    return asyncio.run(
        replay.replay_record(
            saved_record,
            source_id="source-1",
            originals={},
            blobs=tmp_path,
            candidate=template_candidate(),
            now_iso=NOW,
            classifier=classifier or Classifier(),
            sandbox=sandbox,
        )
    )


def test_model_disabled_preserves_raw_rejected_rows(tmp_path):
    row = run_record(tmp_path, ReplaySandbox(capability="unsupported"))
    assert row["error_code"] == "MODEL_DISABLED"
    assert row["execution_status"] == "unfinished"
    assert row["result"] is None
    assert row["execution"] == row["runtime_error"]["execution"]
    assert row["execution"]["model_used"] is False
    assert row["execution"]["path"] == "detailed"
    assert row["routing"]["mode"] == "detailed"
    assert row["accepted_position_count"] == 0
    assert row["extracted_position_count"] == row["diagnostic_position_count"] == 1
    assert row["rejected_origin"] == "script_draft"
    assert row["rejected_draft"] == row["rejected_result"] == row["raw_outputs"][0]["output"]
    assert row["rejected_draft"]["schema_version"] == "position-draft-v1"
    assert row["diagnostic_normalization"]["schema_version"] == "position-facts-v3"
    assert row["diagnostic_normalization"]["positions"][0]["title"]["value"] == "管理助理"
    assert row["position_calendar"] == []
    assert row["diagnostic_position_calendar"][0]["status"] == "not_expired"


def test_script_failure_retained(tmp_path):
    class BrokenSandbox(ReplaySandbox):
        async def run_script(self, files, module, function, inputs):
            raise ScriptExecutionError("synthetic broken script")

    row = run_record(tmp_path, BrokenSandbox(), material("2026-10-22"))
    assert row["error_code"] == "SCRIPT_PARSE_FAILED"
    assert row["raw_outputs"][0]["status"] == "failed"
    assert row["accepted_position_count"] == 0
    assert row["routing"]["mode"] == "quick"
    detailed = run_record(tmp_path, BrokenSandbox())
    assert detailed["error_code"] == "MODEL_DISABLED"  # Actual runtime fallback ownership.
    assert detailed["raw_outputs"][0]["status"] == "failed"
    assert detailed["diagnostic_normalization"]["positions"] == []
    assert detailed["accepted_position_count"] == 0 and detailed["result"] is None


def test_quick_cost_route_keeps_completed_rows_and_independent_calendar(tmp_path):
    row = run_record(tmp_path, ReplaySandbox(), material("2026-10-22"))
    assert row["execution_status"] == "completed"
    assert row["routing"]["category"] == "fixed_expired"
    assert row["execution"]["path"] == "quick" and row["execution"]["model_used"] is False
    assert row["accepted_position_count"] == row["extracted_position_count"] == 1
    assert row["position_calendar"][0]["status"] == "expired"
    assert row["diagnostic_position_count"] == 0
    assert "quality_tier" not in row and "recipe_validity" not in row


def test_mixed_runs_review_does_not(tmp_path):
    class Assessment:
        def __init__(self, review=False):
            self.review = review

        async def classify(self, bundle):
            return {
                "business_type": "recruitment",
                "page_roles": ["article", "notice_list"],
                "requires_review": self.review,
                "method": "rules",
            }

    row = run_record(tmp_path, ReplaySandbox(), classifier=Assessment())
    assert row["execution_status"] == "completed"
    row = run_record(tmp_path, ReplaySandbox(), classifier=Assessment(review=True))
    assert row["execution_status"] == "not_a_resolved_recruitment_article"
    assert row["raw_outputs"] == []


def test_reparse_preserves_ids_or_retains_saved_view(tmp_path):
    raw = b"<html><body><article><p>Saved recruitment text</p></article></body></html>"
    reference = blob(tmp_path, raw)
    parsed = parse_material(
        raw,
        media_type="text/html",
        url="https://example.org/notice",
        material_key=f"resource:{reference}",
    )
    old = deepcopy(parsed)
    old.pop("structure")
    old.update(version_id="original", resource_id="resource", status="failed")
    value = {"page": old, "url": "https://example.org/notice", "attachments": []}

    class Parser:
        async def parse(self, body, **kwargs):
            assert body == raw and kwargs["material_key"] == f"resource:{reference}"
            return deepcopy(parsed)

    originals = {
        "original": {"resource_id": "resource", "body_ref": reference, "media_type": "text/html"}
    }
    observations = asyncio.run(replay.reparse_materials(value, originals, tmp_path, Parser()))
    assert observations[0]["status"] == "same_evidence_current_metadata_added"
    assert value["page"]["status"] == "failed"
    value["page"].pop("structure")
    value["page"]["blocks"][0]["text"] = "different old content"
    before = deepcopy(value)
    observations = asyncio.run(replay.reparse_materials(value, originals, tmp_path, Parser()))
    assert observations[0]["status"] == "saved_view_retained"
    assert value == before


def test_candidate_and_atomic_bounded_report(tmp_path, monkeypatch):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(template_candidate()))
    candidate, metadata = replay.load_candidate(path)
    assert metadata["generated_for_source_verified"] is False
    assert metadata["runtime_contract"] == "recipe-runtime-v3"
    assert set(candidate) == {"workflow", "parser_py", "undated_parser_py", "config"}
    obsolete = template_candidate()
    obsolete["config"]["position_contract"] = "position-facts-v2"
    path.write_text(json.dumps(obsolete))
    with pytest.raises(ValueError, match="recipe-runtime-v3"):
        replay.load_candidate(path)
    path.write_text(json.dumps({"candidate": template_candidate()}))
    with pytest.raises(ValueError, match="canonical"):
        replay.load_candidate(path)
    output = tmp_path / "out.json"
    output.write_text("previous")
    with pytest.raises(ValueError, match="overwrite"):
        replay.write_report(output, {"long": "new report"})
    assert output.read_text() == "previous"
    monkeypatch.setattr(replay, "MAX_REPORT_BYTES", 4)
    fresh = tmp_path / "too-large.json"
    with pytest.raises(ValueError, match="no partial report"):
        replay.write_report(fresh, {"long": "new report"})
    assert not fresh.exists()
    assert not list(tmp_path.glob(".source-replay-*"))


def test_real_sandbox_flow(tmp_path):
    row = run_record(tmp_path, Sandbox(), material("2026-10-22"))
    assert row["execution_status"] == "completed", row
    assert row["execution"]["path"] == "quick"
    assert row["extracted_position_count"] == row["accepted_position_count"] == 1
    assert [item["module"] for item in row["raw_outputs"]] == ["parser"]
    assert row["raw_outputs"][0]["output"]["schema_version"] == "position-draft-v1"
    assert row["result"]["schema_version"] == "position-facts-v3"


def test_normalized_funnel_all_resource_bindings_no_saved_labels(funnel_snapshot):  # noqa: F811
    from youju.onboarding.funnel import report_source_funnel

    engine, blobs = funnel_snapshot
    path = Path(engine.url.database)
    _, candidate = replay.load_candidate(None)
    result = extract_position_facts(material())
    result["positions"].append(deepcopy(result["positions"][0]))
    record = {
        "resource_id": "future",
        "bundle_id": "future",
        "classification": {
            "business_type": "recruitment",
            "page_roles": ["article", "notice_list"],
            "requires_review": False,
        },
        "execution_status": "completed",
        "result": result,
        "accepted_position_count": 2,
        "extracted_position_count": 2,
        "error_code": None,
        "routing": {"category": "fixed_expired", "mode": "quick"},
        "position_calendar": replay.position_calendar(result, NOW),
    }
    report = {
        "source_id": "source",
        "clock": NOW,
        "candidate": candidate,
        "classification": {"local_model_key": None},
        "records": [record],
    }
    before = path.read_bytes()
    envelope = replay.normalized_funnel(report, path, blobs)
    assert envelope["denominators"]["unique_resources"] == 7
    assert envelope["positions"]["accepted"] == 2
    assert envelope["positions"]["public_default"] == 0
    assert envelope["classification"]["unclassified_or_stale"] == 6
    assert envelope["cost_routing"] == {"fixed_expired": 1}
    assert envelope["position_calendar_status"] == {"not_expired": 2}
    assert envelope["context"]["contract"] == "recipe-runtime-v3"
    rows = {row["resource_id"]: row for row in envelope["observations"]}
    assert rows["future"]["version_id"] == "version-future"
    assert rows["future"]["document_id"] == "doc-future"
    assert rows["expired"]["classified"] is False  # Old DB label must NOT leak.
    validated = report_source_funnel(
        snapshot=path,
        blobs=blobs,
        source_id="source",
        clock=NOW,
        replay=envelope,
    )
    assert validated["positions"]["accepted"] == 2
    assert path.read_bytes() == before
    # A parse-shaped output is not permission to bypass classification uncertainty.
    record["classification"]["requires_review"] = True
    uncertain = replay.normalized_funnel(report, path, blobs)
    observed = next(row for row in uncertain["observations"] if row["resource_id"] == "future")
    assert observed["state"] == "review" and observed["accepted_positions"] == 0
    assert observed["reported_positions"] == 2
    record["classification"]["requires_review"] = False
    record["bundle_id"] = "historical-bundle"
    stale = replay.normalized_funnel(report, path, blobs)
    observed = next(row for row in stale["observations"] if row["resource_id"] == "future")
    assert observed["state"] == "stale" and observed["accepted_positions"] == 0
    assert observed["classified"] is False and observed["validity"] == "unknown"
    record["bundle_id"] = "future"
    record["classification"]["business_type"] = "cancellation"
    record.update(
        accepted_position_count=0,
        extracted_position_count=0,
        result=None,
        routing=None,
        position_calendar=[],
        execution_status="not_a_resolved_recruitment_article",
    )
    cancelled = replay.normalized_funnel(report, path, blobs)
    assert cancelled["classification"]["cancellation_pages"] == 1
    assert cancelled["position_calendar_status"] == {}
    assert sum(cancelled["recruitment"]["validity"].values()) == 0


@pytest.fixture
def cache_fixture(tmp_path, monkeypatch):
    path, blobs = snapshot(tmp_path, 2)
    monkeypatch.setattr(replay, "Sandbox", ReplaySandbox)

    def envelope(report, *args):
        with replay.closing(replay.connect_snapshot(path)) as connection:
            scans = connection.execute(
                "SELECT r.id,s.last_version_id,s.last_document_id,s.bundle_id "
                "FROM resources r LEFT JOIN resource_scans s ON s.resource_id=r.id"
            ).fetchall()
        return {
            "context": {"classifier_key": replay.classification_key(None)},
            "observations": [
                dict(
                    zip(("resource_id", "version_id", "document_id", "bundle_id"), row, strict=True)
                )
                for row in scans
            ],
        }

    monkeypatch.setattr(replay, "normalized_funnel", envelope)
    args = SimpleNamespace(
        snapshot=path,
        blobs=blobs,
        source_id="source-1",
        now=NOW,
        candidate=None,
        concurrency=2,
        local_classifier=None,
        output=tmp_path / "new-report.json",
    )
    report = asyncio.run(replay.replay(args))
    # Synthetic upstream review page: it has never executed any candidate.
    upstream = next(row for row in report["records"] if row["resource_id"] == "r1")
    upstream["classification"]["requires_review"] = True
    upstream.update(
        execution_status="not_a_resolved_recruitment_article",
        result=None,
        execution=None,
        raw_outputs=[],
        rejected_result=None,
        diagnostic_normalization=None,
        accepted_position_count=0,
        extracted_position_count=0,
        diagnostic_position_count=0,
        position_calendar=[],
        diagnostic_position_calendar=[],
        recipe_execution_attempted=False,
        executed_candidate_digest=None,
        executed_runtime_fingerprint=None,
    )
    old = tmp_path / "old-report.json"
    replay.write_report(old, report)
    with replay.closing(replay.connect_snapshot(path)) as connection:
        saved_rows, _, inventory = replay.saved_inventory(connection, "source-1")
    kwargs = dict(
        snapshot=path.resolve(),
        blobs=blobs.resolve(),
        source_id="source-1",
        clock=NOW,
        local_key=None,
        saved=saved_rows,
        inventory=inventory,
    )
    return args, old, report, kwargs


def test_cached_revalidation_reruns_candidate_never_old_outputs(cache_fixture, monkeypatch):
    args, old, report, _ = cache_fixture
    # An old candidate's result must never be sent as previous_result or accepted.
    selected = next(row for row in report["records"] if row["resource_id"] == "r0")
    selected["result"] = {"status": "success", "positions": ["OLD_RESULT_DO_NOT_REUSE"]}
    old.write_text(json.dumps(report))  # Deliberate hostile/prior-cache fixture mutation.
    before = old.read_bytes()

    class NeverClassify:
        async def classify(self, bundle):
            raise AssertionError("Cached classification must not invoke a new classifier")

    monkeypatch.setattr(replay, "Classifier", lambda **kwargs: NeverClassify())
    actual_run = replay.RecipeRunner.run
    executions = []

    async def checked_run(runner, candidate, bundle, **kwargs):
        assert "previous_result" not in kwargs, "Old results must not even be sent to the runtime"
        executions.append(bundle["resource_id"])
        return await actual_run(runner, candidate, bundle, **kwargs)

    monkeypatch.setattr(replay.RecipeRunner, "run", checked_run)
    args.reuse_classification_from = old
    current = asyncio.run(replay.replay(args))
    assert old.read_bytes() == before
    assert current["actual_recipe_execution_count"] == 1
    assert executions == ["r0"]
    assert current["cache"]["candidate_results_reused"] == 0
    assert current["cache"]["upstream_only_count"] == 1
    assert current["current_reparse_counts"] == {"current_metadata": 2}
    selected = next(row for row in current["records"] if row["resource_id"] == "r0")
    assert selected["accepted_position_count"] == 1
    assert selected["result"]["positions"] != ["OLD_RESULT_DO_NOT_REUSE"]
    assert selected["executed_candidate_digest"] == current["candidate"]["digest"]
    assert (
        selected["executed_runtime_fingerprint"]
        == current["runtime_fingerprint"]
        == replay.runtime_fingerprint()
    )
    assert len(selected["raw_outputs"]) == 1  # Actual official Flow with script double.
    upstream = next(row for row in current["records"] if row["resource_id"] == "r1")
    assert upstream["executed_candidate_digest"] is None
    assert upstream["executed_runtime_fingerprint"] is None
    assert upstream["observation_origin"] == "classification_only_revalidation"
    assert upstream["routing_evaluation"] == "evaluated"
    assert upstream["routing"] is not None
    assert upstream["result"] is upstream["execution"] is None and upstream["raw_outputs"] == []


@pytest.mark.parametrize(
    "mismatch",
    [
        "source",
        "snapshot",
        "clock",
        "local",
        "classifier",
        "llm",
        "coverage",
        "scan",
        "bundle",
        "material",
        "implementation",
        "record_source",
        "classification_metadata",
        "classification_missing",
        "classification_type",
        "classification_roles",
        "classification_evidence",
        "classification_no_evidence",
        "classification_llm",
        "blob_root",
        "network",
        "models_enabled",
        "report_schema",
        "runtime_contract",
        "position_contract",
        "duplicate_observation",
        "duplicate_record",
    ],
)
def test_cached_bindings_fail_closed(cache_fixture, mismatch):
    _, old, report, kwargs = cache_fixture
    if mismatch == "source":
        report["source_id"] = "wrong-source"
    elif mismatch == "snapshot":
        report["snapshot_sha256"] = "0" * 64
    elif mismatch == "clock":
        report["clock"] = "2026-10-25T12:00:00+08:00"
    elif mismatch == "local":
        report["classification"]["local_model_key"] = "other-model"
    elif mismatch == "classifier":
        report["funnel"]["context"]["classifier_key"] = "changed-code"
    elif mismatch == "llm":
        report["model_calls"] = 1
    elif mismatch == "coverage":
        report["funnel"]["observations"].pop()
    elif mismatch == "scan":
        report["funnel"]["observations"][0]["document_id"] = "old-document"
    elif mismatch == "bundle":
        report["records"][0]["bundle_body_ref"] = "0" * 64
    elif mismatch == "material":
        reference = report["records"][0]["bundle_body_ref"]
        (kwargs["blobs"] / reference).write_bytes(b"changed blob")
    elif mismatch == "record_source":
        report["records"][1]["source_id"] = "other-source"
    elif mismatch == "classification_metadata":
        report["classification"] = None
    elif mismatch == "implementation":
        report["classification"]["implementation_key"] = "different-implementation"
    elif mismatch == "classification_missing":
        report["records"][1]["classification"] = None
    elif mismatch == "classification_type":
        report["records"][1]["classification"]["requires_review"] = "false"
    elif mismatch == "classification_roles":
        report["records"][1]["classification"]["page_roles"] = [["article"]]
    elif mismatch == "classification_evidence":
        report["records"][1]["classification"]["evidence_ids"] = ["invented-block"]
    elif mismatch == "classification_no_evidence":
        report["records"][1]["classification"]["evidence_ids"] = []
    elif mismatch == "classification_llm":
        report["records"][1]["classification"]["method"] = "small"
    elif mismatch == "blob_root":
        report["blobs"] = str(old.parent)
    elif mismatch == "network":
        report["network_requests"] = 1
    elif mismatch == "models_enabled":
        report["models_enabled"] = True
    elif mismatch == "report_schema":
        report["schema_version"] = "source-accuracy-replay-v1"
    elif mismatch == "runtime_contract":
        report["runtime_contract"] = "obsolete-contract"
    elif mismatch == "position_contract":
        report["position_contract"] = "position-facts-v2"
    elif mismatch == "duplicate_observation":
        report["funnel"]["observations"].append(deepcopy(report["funnel"]["observations"][0]))
    elif mismatch == "duplicate_record":
        report["records"].append(deepcopy(report["records"][0]))
    old.write_text(json.dumps(report))  # Tamper with a cache, never publish over a baseline.
    with pytest.raises(ValueError):
        replay.upstream_cache(old, **kwargs)


def test_changed_cost_policy_reuses_only_bound_classification_and_recomputes_routes(
    cache_fixture, monkeypatch
):
    args, old, prior, kwargs = cache_fixture
    prior["routing_policy"] = "prior-cost-policy"
    for row in prior["records"]:
        row.update(
            routing={"category": "fixed_expired", "reason": "NEVER_REUSE"},
            execution={"path": "OLD_EXECUTION_DO_NOT_REUSE"},
            result={"positions": ["OLD_RESULT_DO_NOT_REUSE"]},
            raw_outputs=[{"output": "OLD_RAW_DO_NOT_REUSE"}],
            rejected_result={"positions": ["OLD_REJECTED_DO_NOT_REUSE"]},
            rejected_draft={"positions": ["OLD_DRAFT_DO_NOT_REUSE"]},
            diagnostic_normalization={"positions": ["OLD_DIAGNOSTIC_DO_NOT_REUSE"]},
            position_calendar=[{"status": "OLD_CALENDAR_DO_NOT_REUSE"}],
            accepted_position_count=999,
            extracted_position_count=999,
            errors=["OLD_ERROR_DO_NOT_REUSE"],
        )
    old.write_text(json.dumps(prior))
    before = old.read_bytes()
    cached, metadata = replay.upstream_cache(old, **kwargs)
    assert all(set(row) == {"classification"} for row in cached.values())
    assert metadata["reuse_mode"] == "classification_only"
    assert metadata["prior_routing_policy"] == "prior-cost-policy"
    assert metadata["routing_policy"] == replay.RoutingPolicy().version
    assert metadata["routing_results_reused"] == metadata["position_results_reused"] == 0
    assert metadata["diagnostic_results_reused"] == metadata["candidate_results_reused"] == 0
    evaluated = []
    route = replay.decide_route

    def current_cost_policy(bundle, classification, *, now):
        assert now.isoformat() == NOW
        evaluated.append(bundle["resource_id"])
        return route(bundle, classification, now=now)

    monkeypatch.setattr(replay, "decide_route", current_cost_policy)
    args.reuse_classification_from = old
    current = asyncio.run(replay.replay(args))
    assert set(evaluated) == {"r0", "r1"}
    assert current["actual_recipe_execution_count"] == 1
    denominators = current["counts"]["denominators"]
    assert (
        denominators["latest_saved_bundles"] == denominators["recruitment_article_candidates"] == 2
    )
    assert denominators["resolved_recruitment_articles"] == 1
    assert denominators["classification_review"] == 1
    assert current["counts"]["routing_evaluation"] == {"evaluated": 2}
    review = next(row for row in current["records"] if row["resource_id"] == "r1")
    assert review["classification"]["requires_review"] is True
    assert review["routing"]["evaluated_at"] == NOW
    assert review["recipe_execution_attempted"] is False
    assert review["accepted_position_count"] == review["extracted_position_count"] == 0
    assert review["result"] is review["execution"] is review["rejected_result"] is None
    assert review["raw_outputs"] == []
    assert "NEVER_REUSE" not in json.dumps(current) and "DO_NOT_REUSE" not in json.dumps(current)
    assert old.read_bytes() == before


def test_non_recruitment_cache_has_no_routes_or_candidate_outputs(cache_fixture, monkeypatch):
    args, old, prior, _ = cache_fixture
    upstream = next(row for row in prior["records"] if row["resource_id"] == "r1")
    upstream["classification"].update(business_type="unknown", requires_review=True)
    upstream.update(
        routing={"category": "fixed_expired"},
        execution={"path": "old path"},
        result={"positions": ["old position"]},
        errors=["old error"],
        reparse=[{"status": "old metadata"}],
        diagnostic_normalization={"positions": ["old position"]},
        extracted_position_count=1,
    )
    old.write_text(json.dumps(prior))
    before = old.read_bytes()
    route = replay.decide_route

    def recruitment_only(bundle, classification, *, now):
        assert bundle["resource_id"] == "r0", "Non-recruitment must not get a cost route"
        return route(bundle, classification, now=now)

    monkeypatch.setattr(replay, "decide_route", recruitment_only)
    args.reuse_classification_from = old
    current = asyncio.run(replay.replay(args))
    upstream = next(row for row in current["records"] if row["resource_id"] == "r1")
    assert upstream["routing_evaluation"] == "not_applicable"
    assert upstream["routing"] is upstream["result"] is upstream["execution"] is None
    assert upstream["diagnostic_normalization"] is None
    assert upstream["raw_outputs"] == upstream["reparse"] == upstream["errors"] == []
    assert upstream["accepted_position_count"] == upstream["extracted_position_count"] == 0
    assert current["actual_recipe_execution_count"] == 1
    assert current["counts"]["routing_evaluation"] == {"evaluated": 1, "not_applicable": 1}
    assert old.read_bytes() == before


def test_fresh_non_recruitment_skips_routing_and_flow(tmp_path, monkeypatch):
    class Unknown:
        async def classify(self, bundle):
            return {
                "business_type": "unknown",
                "page_roles": ["article"],
                "requires_review": True,
                "method": "rules",
            }

    def forbidden(*args, **kwargs):
        raise AssertionError("Non-recruitment must not route or extract positions")

    monkeypatch.setattr(replay, "decide_route", forbidden)
    row = run_record(tmp_path, ReplaySandbox(), classifier=Unknown())
    assert row["routing_evaluation"] == "not_applicable"
    assert row["routing"] is row["result"] is None
    assert row["raw_outputs"] == [] and row["errors"] == []


def test_replay_refuses_prior_report_as_output(cache_fixture):
    args, old, _, _ = cache_fixture
    args.reuse_classification_from = args.output = old
    before = old.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        asyncio.run(replay.replay(args))
    assert old.read_bytes() == before


@pytest.mark.parametrize("mode", ["quick", "detailed"])
def test_cost_route_is_not_a_job_deadline_or_acceptance_veto(tmp_path, monkeypatch, mode):
    from datetime import datetime

    from youju.notices.contracts import RoutingCategory, RoutingDecision

    # Deliberately wrong cost category, independently precise active job date.
    monkeypatch.setattr(
        replay,
        "decide_route",
        lambda *args, **kwargs: RoutingDecision(
            RoutingCategory.FIXED_EXPIRED if mode == "quick" else RoutingCategory.FIXED_ACTIVE,
            mode,
            "Fallible synthetic cost policy",
            "test-v1",
            datetime.fromisoformat(NOW),
        ),
    )
    value = material("2026-10-31 14:00")
    value.update(
        host_period={"date": "OLD_DATE_DO_NOT_REUSE"},
        routing={"reason": "OLD_ROUTE_DO_NOT_REUSE"},
        result={"positions": ["OLD_FACTS_DO_NOT_REUSE"]},
    )
    row = run_record(tmp_path, ReplaySandbox(), value)
    assert row["execution_status"] == "completed"
    assert row["accepted_position_count"] == 1
    position = row["result"]["positions"][0]
    assert position["deadline_on"] == {"value": "2026-10-31", "tag": "source"}
    assert position["application_dates"]["value"]["end"]["time"] == "14:00"
    assert row["position_calendar"][0]["status"] == "not_expired"
    assert "DO_NOT_REUSE" not in json.dumps(row)


@pytest.mark.parametrize(
    "endpoint, clock, expected",
    [
        ("2026-10-24 14:00", "2026-10-24T14:00:59+08:00", "not_expired"),
        ("2026-10-24 14:00", "2026-10-24T14:01:00+08:00", "expired"),
        ("2026-10-24 24:00", "2026-10-25T00:00:00+08:00", "expired"),
        ("2026-10-24", "2026-10-24T23:59:59+08:00", "not_expired"),
        ("10月24日", NOW, "unknown"),
    ],
)
def test_position_calendar_uses_domain_precision_not_notice_or_cost_policy(
    endpoint, clock, expected
):
    result = extract_position_facts(material(endpoint))
    assert replay.position_calendar(result, clock)[0]["status"] == expected


@pytest.mark.parametrize("conflict", ["id", "source_id", "resource_id", "page_version"])
def test_saved_blob_identity_cannot_be_rebound_to_selected_bundle(tmp_path, conflict):
    value = material()
    if conflict == "page_version":
        value["page"]["version_id"] = "another-version"
    else:
        value[conflict] = "another-owner"
    row = run_record(tmp_path, ReplaySandbox(), value)
    assert row["execution_status"] == "error"
    assert row["error_code"] == "OFFLINE_REPLAY_ERROR"
    assert row["errors"][0]["phase"] == "load_bundle"
    assert row["raw_outputs"] == [] and row["accepted_position_count"] == 0


@pytest.mark.parametrize(
    "target", ["snapshot", "blob", "cache", "candidate", "snapshot-wal", "symlink"]
)
def test_replay_rejects_protected_destinations_before_execution(cache_fixture, monkeypatch, target):
    args, old, _, _ = cache_fixture
    args.reuse_classification_from = old
    protected = old
    if target == "snapshot":
        args.output = protected = args.snapshot
    elif target == "blob":
        args.output = args.blobs / "new-report.json"
    elif target == "cache":
        args.output = old
    elif target == "candidate":
        args.candidate = protected = old.parent / "candidate.json"
        protected.write_text(json.dumps(template_candidate()))
        args.output = protected
    elif target == "snapshot-wal":
        args.output = Path(str(args.snapshot) + "-wal")
    else:
        args.output = old.parent / "alias.json"
        args.output.symlink_to(old)
    before = protected.read_bytes()
    monkeypatch.setattr(
        replay, "Sandbox", lambda: pytest.fail("Output checks must precede execution")
    )
    with pytest.raises(ValueError, match="Output"):
        asyncio.run(replay.replay(args))
    assert protected.read_bytes() == before


def test_atomic_report_publication_never_clobbers_a_racing_output(tmp_path, monkeypatch):
    output = tmp_path / "racing.json"
    link = replay.os.link

    def raced(source, destination):
        output.write_text("other owner's report")
        return link(source, destination)

    monkeypatch.setattr(replay.os, "link", raced)
    with pytest.raises(FileExistsError):
        replay.write_report(output, {"safe": "new report"})
    assert output.read_text() == "other owner's report"
    assert not list(tmp_path.glob(".source-replay-*"))


@pytest.mark.parametrize("change", ["main_bytes", "new_wal"])
def test_replay_detects_snapshot_mutation_before_report_publication(tmp_path, monkeypatch, change):
    path, blobs = snapshot(tmp_path, 1)

    class MutatingSandbox(ReplaySandbox):
        async def run_script(self, *args):
            result = await super().run_script(*args)
            if change == "main_bytes":
                path.write_bytes(path.read_bytes() + b"test mutation")
            else:
                Path(str(path) + "-wal").write_bytes(b"new uncheckpointed data")
            return result

    monkeypatch.setattr(replay, "Sandbox", MutatingSandbox)
    monkeypatch.setattr(replay, "normalized_funnel", lambda *args: {"tested_separately": True})
    args = SimpleNamespace(
        snapshot=path,
        blobs=blobs,
        source_id="source-1",
        now=NOW,
        candidate=None,
        concurrency=1,
        local_classifier=None,
        output=tmp_path / "new-report.json",
    )
    with pytest.raises(ValueError, match="changed|WAL"):
        asyncio.run(replay.replay(args))
    assert not args.output.exists()


def test_blob_size_and_regular_file_checks_are_enforced(tmp_path, monkeypatch):
    reference = blob(tmp_path, b"bounded bytes")
    monkeypatch.setattr(replay, "MAX_BLOB_BYTES", 4)
    with pytest.raises(ValueError, match="byte limit"):
        replay.read_blob(tmp_path, reference)
    (tmp_path / reference).unlink()
    (tmp_path / reference).mkdir()
    with pytest.raises(ValueError, match="regular file"):
        replay.read_blob(tmp_path, reference)


def test_four_worker_limit_is_enforced_during_actual_flow(tmp_path, monkeypatch):
    path, blobs = snapshot(tmp_path, 6)
    active = maximum = 0

    class ConcurrentSandbox(ReplaySandbox):
        async def run_script(self, *args):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            try:
                await asyncio.sleep(0)
                return await super().run_script(*args)
            finally:
                active -= 1

    monkeypatch.setattr(replay, "Sandbox", ConcurrentSandbox)
    monkeypatch.setattr(replay, "normalized_funnel", lambda *args: {"tested_separately": True})
    args = SimpleNamespace(
        snapshot=path,
        blobs=blobs,
        source_id="source-1",
        now=NOW,
        candidate=None,
        concurrency=4,
        local_classifier=None,
        output=tmp_path / "new-report.json",
    )
    report = asyncio.run(replay.replay(args))
    assert maximum == 4 and active == 0
    assert report["actual_recipe_execution_count"] == 6
    assert report["counts"]["execution"] == {"completed": 6}
    args.concurrency = 5
    with pytest.raises(ValueError, match="Concurrency"):
        asyncio.run(replay.replay(args))


@pytest.mark.parametrize("issue_index", [0, 1, 2])
def test_position_calendar_scopes_one_based_field_issues_to_zero_based_rows(issue_index):
    result = extract_position_facts(material("2026-10-22"))
    result["positions"] = [deepcopy(result["positions"][0]) for _ in range(3)]
    result["errors"].append(
        {
            "code": "APPLICATION_DATE_CONFLICT",
            "message": "Only this row needs date review",
            "path": f"positions[{issue_index}].dates.end",
        }
    )
    calendar = replay.position_calendar(result, NOW)
    assert [row["position_index"] for row in calendar] == [0, 1, 2]
    assert [row["status"] for row in calendar] == [
        "unknown" if index == issue_index else "expired" for index in range(3)
    ]


def test_cached_old_runtime_identity_does_not_bind_current_execution(cache_fixture):
    args, old, prior, _ = cache_fixture
    actual = replay.runtime_fingerprint()
    historical = "0" * 64 if actual != "0" * 64 else "1" * 64
    prior["runtime_fingerprint"] = historical
    for row in prior["records"]:
        row["executed_runtime_fingerprint"] = historical
        row["executed_candidate_digest"] = "f" * 64
    old.write_text(json.dumps(prior))
    args.reuse_classification_from = old
    current = asyncio.run(replay.replay(args))
    assert current["cache"]["prior_runtime_fingerprint"] == historical
    assert current["runtime_fingerprint"] == current["cache"]["runtime_fingerprint"] == actual
    for row in current["records"]:
        if row["recipe_execution_attempted"]:
            assert row["executed_runtime_fingerprint"] == actual
            assert row["executed_candidate_digest"] == current["candidate"]["digest"]
        else:
            assert row["executed_runtime_fingerprint"] is row["executed_candidate_digest"] is None


@pytest.mark.parametrize("change_phase", ["execution", "final_check"])
def test_runtime_identity_drift_refuses_report_publication(
    cache_fixture, monkeypatch, change_phase
):
    args, _, _, _ = cache_fixture
    calls = 0

    def changed_fingerprint():
        nonlocal calls
        calls += 1
        # Two selected records: start, each actual execution, final check.
        threshold = 1 if change_phase == "execution" else 3
        return "a" * 64 if calls <= threshold else "b" * 64

    monkeypatch.setattr(replay, "runtime_fingerprint", changed_fingerprint)
    with pytest.raises(ValueError, match="Runtime.*changed"):
        asyncio.run(replay.replay(args))
    assert not args.output.exists()


@pytest.mark.parametrize(
    "written, expected",
    [
        ("2026年10月24日至10月31日", {"value": "2026-10-31", "tag": "source"}),
        ("2026年12月24日至1月5日", {"value": None, "tag": "unknown"}),
    ],
)
def test_replay_job_dates_follow_shared_same_year_and_cross_year_rules(
    tmp_path, monkeypatch, written, expected
):
    from datetime import datetime

    from youju.notices.contracts import RoutingCategory, RoutingDecision

    value = material()
    value["page"]["blocks"][-1]["text"] = "报名时间：" + written
    # No unsupported date scope can buy a model call on a deliberately quick route.
    monkeypatch.setattr(
        replay,
        "decide_route",
        lambda *args, **kwargs: RoutingDecision(
            RoutingCategory.FIXED_EXPIRED,
            "quick",
            "Synthetic cost hint",
            "test-v1",
            datetime.fromisoformat(NOW),
        ),
    )
    row = run_record(tmp_path, ReplaySandbox(), value)
    assert row["execution_status"] == "completed"
    position = row["result"]["positions"][0]
    assert position["deadline_on"] == expected
    assert position["position_code"] == {"value": None, "tag": "unknown"}
    assert row["position_calendar"][0]["status"] == (
        "not_expired" if expected["value"] else "unknown"
    )
    assert row["execution"]["model_used"] is False
