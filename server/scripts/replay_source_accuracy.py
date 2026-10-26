"""Full-source saved-material observations, NOT labelled accuracy or source adoption.

Run from server with frozen uv and an isolated UV_PROJECT_ENVIRONMENT. All paths
are explicit. --snapshot must be a settled COPY (SQLite backup), not a live WAL
DB: immutable read-only SQLite deliberately ignores WAL. No PipelineStore, model
client, fetcher, candidate imports, DB migrations or writes are used. Generated
scripts run only through the real network-isolated Sandbox and official Flow.

One JSON report (plus one temporary file during atomic publication) contains all
latest saved bundles, not the pinned 40-case corpus. Raw script outputs are data,
not accepted jobs. Routing is cost-only; job dates come from the position domain.
No whole-notice temporal oracle or old Recipe output is reused.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import stat
import tempfile
from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from youju.extraction_service import ConfiguredScriptExecutor
from youju.jobs import job_validity
from youju.notices.contracts import RoutingPolicy
from youju.notices.routing import decide_route
from youju.onboarding.auditing import AUDIT_POLICY, position_field_issues
from youju.onboarding.classification import (
    Classifier,
    classifier_implementation_key,
    validate_classification,
)
from youju.onboarding.regions import REGION_VERSION
from youju.onboarding.sandbox import Sandbox
from youju.positions.contracts import CONTRACT as POSITION_CONTRACT
from youju.positions.drafts import DRAFT_CONTRACT
from youju.positions.facts import PositionFacts
from youju.positions.normalization import normalize_extraction
from youju.recipe_runtime.contracts import RUNTIME_CONTRACT, candidate_digest, template_candidate
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_runtime.runner import RecipeRunner
from youju.recipe_runtime.versioning import runtime_fingerprint

LOG = logging.getLogger("source_accuracy_replay")
MAX_BLOB_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 512 * 1024 * 1024
REPORT_SCHEMA = "source-runtime-replay-v1"
REVIEW_TABLES = {"source_calendar_reviews", "announcement_change_reviews"}


def aware_iso(value):
    try:
        now = datetime.fromisoformat(value)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError
        return now.isoformat()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("--now requires a timezone-aware ISO datetime") from None


def concurrency(value):
    try:
        number = int(value)
        if not 1 <= number <= 4:
            raise ValueError
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("--concurrency must be an integer from 1 to 4") from None


def read_blob(root, reference):
    if (
        not isinstance(reference, str)
        or len(reference) != 64
        or any(char not in "0123456789abcdef" for char in reference)
    ):
        raise ValueError("Invalid saved blob reference")
    # O_NOFOLLOW also closes the final-component symlink race. No writable opens.
    fd = os.open(root / reference, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Saved blob must be a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(MAX_BLOB_BYTES + 1)
    finally:
        os.close(fd)
    if len(body) > MAX_BLOB_BYTES:
        raise ValueError("Saved blob exceeds byte limit")
    if hashlib.sha256(body).hexdigest() != reference:
        raise ValueError("Saved blob digest mismatch")
    return body


def connect_snapshot(path):
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Snapshot must be a regular SQLite backup file")
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Snapshot has a nonempty WAL; supply a settled SQLite backup copy")
    database = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA query_only=ON")
    return database


def saved_inventory(database, source_id):
    tables = {
        row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {"sources", "resources", "resource_bundles", "resource_versions"}
    if not required <= tables:
        raise ValueError(f"Missing saved schema tables: {sorted(required - tables)}")
    if database.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone() is None:
        raise ValueError("Source ID not present in snapshot")
    scans = {}
    if "resource_scans" in tables:
        scans = {
            row["resource_id"]: dict(row)
            for row in database.execute(
                "SELECT s.resource_id,s.bundle_id,s.state,s.last_version_id "
                "FROM resource_scans s JOIN resources r ON r.id=s.resource_id "
                "WHERE r.source_id=?",
                (source_id,),
            )
        }
    rows = database.execute(
        "SELECT b.id,b.resource_id,b.page_version_id,b.body_ref,b.status,b.created_at,"
        "r.normalized_url AS url FROM resource_bundles b "
        "JOIN resources r ON r.id=b.resource_id WHERE r.source_id=? "
        "ORDER BY b.resource_id,b.created_at DESC,b.id DESC",
        (source_id,),
    ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["resource_id"], []).append(dict(row))
    selected = []
    for resource_id, versions in grouped.items():
        scan = scans.get(resource_id, {})
        pointer = scan.get("bundle_id")
        current = next((row for row in versions if row["id"] == pointer), None)
        record = dict(current or versions[0])
        record["selection"] = "scan_bundle_pointer" if current else "latest_created_at_id_fallback"
        record["scan_state"] = scan.get("state")
        record["scan_last_version_id"] = scan.get("last_version_id")
        record["warnings"] = []
        if not current:
            record["warnings"].append("No valid current scan pointer; latest saved bundle used")
        if scan.get("last_version_id") not in (None, record["page_version_id"]):
            record["warnings"].append("Saved bundle does not represent latest acquired version")
        if scan.get("state") not in (None, "done", "review"):
            record["warnings"].append("Acquisition is not settled; replay uses last saved bundle")
        selected.append(record)
    originals = {
        row["id"]: dict(row)
        for row in database.execute(
            "SELECT v.id,v.resource_id,v.body_ref,v.media_type FROM resource_versions v "
            "JOIN resources r ON r.id=v.resource_id WHERE r.source_id=?",
            (source_id,),
        )
    }
    resource_count = database.execute(
        "SELECT count(*) FROM resources WHERE source_id=?", (source_id,)
    ).fetchone()[0]
    return (
        selected,
        originals,
        {
            "source_resource_count": resource_count,
            "saved_bundle_version_count": len(rows),
            "latest_saved_bundle_count": len(selected),
            "resources_without_saved_bundle": resource_count - len(selected),
            "selection_counts": dict(Counter(row["selection"] for row in selected)),
            "saved_original_version_count": len(originals),
            "resources_without_saved_version": resource_count
            - len({row["resource_id"] for row in originals.values()}),
            "resource_scan_count": len(scans),
            "resources_without_scan": resource_count - len(scans),
            "scan_state_counts": dict(Counter(row["state"] for row in scans.values())),
            "saved_bundle_status_counts": dict(Counter(row["status"] for row in rows)),
            "selected_bundle_status_counts": dict(Counter(row["status"] for row in selected)),
            "review_tables_present": sorted(REVIEW_TABLES & tables),
            "review_tables_missing": sorted(REVIEW_TABLES - tables),
        },
    )


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def classification_key(local_key):
    return hashlib.sha256(
        json.dumps(
            {
                "implementation": classifier_implementation_key(),
                "local": local_key,
                "llm": False,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def recruitment_article(assessment):
    """Cost routing can observe recruitment articles even when classification needs review."""
    return bool(
        assessment
        and assessment.get("business_type") == "recruitment"
        and "article" in assessment.get("page_roles", [])
    )


def resolved_article(assessment):
    return recruitment_article(assessment) and assessment.get("requires_review") is False


def upstream_cache(path, *, snapshot, blobs, source_id, clock, local_key, saved, inventory):
    """Trust an explicitly supplied report only after checking every saved binding.

    Classification ONLY: current routing and every Recipe are always re-executed.
    Old report schemas/contracts fail explicitly: omit the cache flag to recompute.
    No route, date, draft, candidate, error or parser observations are returned.
    This provenance check is not authentication or independent classification gold.
    """
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("Upstream cache exceeds report byte limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Upstream cache exceeds report byte limit")
    prior = json.loads(raw)

    def require(condition, message):
        if not condition:
            raise ValueError("Upstream cache rejected: " + message)

    require(isinstance(prior, dict), "report object")
    require(prior.get("schema_version") == REPORT_SCHEMA, "report schema; recompute without cache")
    require(
        prior.get("runtime_contract") == RUNTIME_CONTRACT,
        "runtime contract; recompute without cache",
    )
    require(
        prior.get("position_contract") == POSITION_CONTRACT,
        "position contract; recompute without cache",
    )
    prior_fingerprint = prior.get("runtime_fingerprint")
    require(
        isinstance(prior_fingerprint, str)
        and len(prior_fingerprint) == 64
        and all(c in "0123456789abcdef" for c in prior_fingerprint),
        "runtime fingerprint missing/invalid; recompute without cache",
    )
    require(prior.get("source_id") == source_id, "source")
    require(prior.get("snapshot_sha256") == file_digest(snapshot), "snapshot hash")
    require(
        isinstance(prior.get("blobs"), str) and Path(prior["blobs"]).resolve() == blobs, "blob root"
    )
    require(aware_iso(prior.get("clock")) == clock, "clock")
    require(
        prior.get("model_calls") == 0
        and prior.get("network_requests") == 0
        and prior.get("models_enabled") is False,
        "no-LLM/no-network identity",
    )
    classification = prior.get("classification", {})
    require(isinstance(classification, dict), "classification metadata object")
    require(
        classification.get("pipeline") == "current_shared_rules_local_host_no_llm"
        and classification.get("local_model_key") == local_key
        and classification.get("implementation_key") == classifier_implementation_key(),
        "classifier/local identity",
    )
    require(isinstance(prior.get("funnel"), dict), "funnel object")
    require(isinstance(prior["funnel"].get("context"), dict), "funnel context")
    require(
        prior["funnel"]["context"].get("classifier_key") == classification_key(local_key),
        "classifier implementation identity",
    )
    require(prior.get("inventory") == inventory, "inventory")
    observations = prior["funnel"].get("observations")
    require(
        isinstance(observations, list)
        and all(
            isinstance(row, dict) and isinstance(row.get("resource_id"), str)
            for row in observations
        ),
        "resource observations array",
    )
    indexed = {row["resource_id"]: row for row in observations}
    require(len(indexed) == len(observations), "duplicate resource observations")
    with closing(connect_snapshot(snapshot)) as database:
        scans = database.execute(
            "SELECT r.id,s.last_version_id,s.last_document_id,s.bundle_id "
            "FROM resources r LEFT JOIN resource_scans s ON s.resource_id=r.id "
            "WHERE r.source_id=?",
            (source_id,),
        ).fetchall()
    require(set(indexed) == {row[0] for row in scans}, "complete resource coverage")
    for resource_id, version_id, document_id, bundle_id in scans:
        observed = indexed[resource_id]
        require(
            (observed.get("version_id"), observed.get("document_id"), observed.get("bundle_id"))
            == (version_id, document_id, bundle_id),
            "scan version/document/bundle binding",
        )
    records = prior.get("records")
    require(
        isinstance(records, list)
        and all(
            isinstance(row, dict) and isinstance(row.get("resource_id"), str) for row in records
        ),
        "latest-bundle records array",
    )
    provenance = prior.get("candidate")
    require(
        isinstance(provenance, dict)
        and isinstance(provenance.get("digest"), str)
        and len(provenance["digest"]) == 64
        and all(c in "0123456789abcdef" for c in provenance["digest"]),
        "prior candidate provenance",
    )
    indexed_records = {row["resource_id"]: row for row in records}
    require(
        len(records) == len(indexed_records)
        and set(indexed_records) == {row["resource_id"] for row in saved},
        "complete latest-bundle coverage",
    )
    for record in saved:
        old = indexed_records[record["resource_id"]]
        require(
            all(
                old.get(key) == record[key]
                for key in ("resource_id", "url", "page_version_id", "scan_state", "selection")
            )
            and old.get("bundle_id") == record["id"]
            and old.get("bundle_body_ref") == record["body_ref"]
            and old.get("source_id") == source_id,
            "bundle material binding",
        )
        require(
            old.get("scan_last_version_id") == record.get("scan_last_version_id"),
            "scan last-version binding",
        )
        bundle = json.loads(read_blob(blobs, record["body_ref"]))
        require(
            bundle.get("id") == record["id"]
            and bundle.get("resource_id") == record["resource_id"]
            and bundle.get("source_id") == source_id,
            "saved bundle ownership",
        )
        # Validate every classification, including pages which will not run Flow.
        # A matching cache key alone cannot legitimize malformed/evidence-free labels.
        try:
            errors = validate_classification(bundle, old.get("classification"))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Upstream cache rejected: malformed classification binding") from error
        require(not errors, "classification/evidence binding")
        require(
            old["classification"]["method"] in {"rules", "local", "unknown"},
            "no-LLM classification method",
        )
    classifications = {
        resource_id: {"classification": deepcopy(row["classification"])}
        for resource_id, row in indexed_records.items()
    }
    return classifications, {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "prior_candidate_digest": prior["candidate"]["digest"],
        "prior_runtime_fingerprint": prior_fingerprint,
        "runtime_fingerprint": runtime_fingerprint(),
        "snapshot_sha256": prior["snapshot_sha256"],
        "clock": clock,
        "classifier_key": classification_key(local_key),
        "reuse_mode": "classification_only",
        "prior_routing_policy": prior.get("routing_policy"),
        "routing_policy": RoutingPolicy().version,
        "routing_results_reused": 0,
        "position_results_reused": 0,
        "diagnostic_results_reused": 0,
        "classification_reused_count": len(records),
        "upstream_only_count": sum(
            not resolved_article(row.get("classification")) for row in records
        ),
        "candidate_results_reused": 0,
        "note": "Only bound classification reused; recruitment gets fresh cost routing; "
        "resolved articles execute current Flow, with no old dates/positions/drafts/results; "
        "other pages have no routing or candidate outputs",
    }


class CachedClassification:
    def __init__(self, assessment):
        self.assessment = assessment

    async def classify(self, bundle):
        errors = validate_classification(bundle, self.assessment)
        if errors:
            raise ValueError("Cached classification is invalid for reparsed evidence")
        return deepcopy(self.assessment)


def evidence_view(document):
    # Compare tables/cell IDs as well as blocks: never rebind an old locator to a
    # different row or silently let a parser upgrade erase an old evidence ID.
    return {
        "blocks": [
            (block["id"], block["text"], block.get("locator"))
            for block in document.get("blocks", [])
        ],
        "tables": document.get("tables", []),
    }


async def reparse_materials(bundle, originals, blobs, sandbox):
    observations = []
    for document in [bundle["page"], *bundle.get("attachments", [])]:
        if document.get("format") != "html":
            continue
        record = {"version_id": document.get("version_id"), "status": "current_metadata"}
        observations.append(record)
        if document.get("structure", {}).get("version") == REGION_VERSION:
            continue
        original = originals.get(document.get("version_id"))
        if original is None:
            record.update(status="saved_view_retained", warning="Saved HTML original not found")
            continue
        try:
            body = read_blob(blobs, original["body_ref"])
            parsed = await sandbox.parse(
                body,
                media_type=original["media_type"] or "text/html",
                url=document.get("url", bundle["url"]),
                material_key=f"{original['resource_id']}:{original['body_ref']}",
            )
            if evidence_view(document) != evidence_view(parsed):
                record.update(
                    status="saved_view_retained",
                    warning="Parser evidence namespace/content changed; no ID rebinding attempted",
                )
                continue
            # Parser metadata can improve; an acquisition/partial-material failure
            # cannot turn into success merely because the saved body was readable.
            old_status = document.get("status", "ok")
            old_errors = document.get("errors", [])
            old_truncated = document.get("truncated", False)
            document.update(parsed)
            if old_status != "ok":
                document["status"] = old_status
            document["errors"] = old_errors + [
                error for error in parsed.get("errors", []) if error not in old_errors
            ]
            document["truncated"] = old_truncated or parsed.get("truncated", False)
            record["status"] = "same_evidence_current_metadata_added"
        except Exception as error:
            record.update(
                status="saved_view_retained",
                warning="Reparse failed; old evidence retained",
                error={"type": type(error).__name__, "message": str(error)[:1000]},
            )
    return observations


class RecordingSandbox:
    """Retain script observations without trusting or executing them on the host."""

    def __init__(self, sandbox):
        self.sandbox = ConfiguredScriptExecutor(sandbox)
        self.outputs = []

    async def run_script(self, files, module, function, inputs):
        record = {"module": module, "function": function}
        self.outputs.append(record)
        try:
            result = await self.sandbox.run_script(files, module, function, inputs)
            record.update(status="returned", output=result)
            return result
        except Exception as error:
            record.update(
                status="failed", error={"type": type(error).__name__, "message": str(error)[:1000]}
            )
            raise


def load_candidate(path):
    if path is None:
        candidate = template_candidate()
        origin = "host_template_not_agent_authored_source_recipe"
    else:
        with path.open("rb") as stream:
            raw = stream.read(300_001)
        if len(raw) > 300_000:
            raise ValueError("Canonical saved candidate exceeds byte limit")
        candidate = json.loads(raw)
        if not isinstance(candidate, dict) or set(candidate) != {
            "workflow",
            "parser_py",
            "undated_parser_py",
            "config",
        }:
            raise ValueError("--candidate must be canonical saved candidate JSON, not an envelope")
        origin = "supplied_saved_candidate_generation_provenance_not_independently_verified"
    errors = RecipeRunner.validate(candidate)
    if errors:
        raise ValueError(
            "Invalid recipe-runtime-v3 / position-facts-v3 candidate: " + json.dumps(errors)
        )
    return candidate, {
        "origin": origin,
        "path": str(path.resolve()) if path else None,
        "digest": candidate_digest(candidate),
        "runtime_contract": RUNTIME_CONTRACT,
        "position_contract": POSITION_CONTRACT,
        "generated_for_source_verified": False,
        "adopted": False,
    }


def position_calendar(result, now_iso):
    """Exact per-job comparison, never supplied with a routing hypothesis."""
    day = datetime.fromisoformat(now_iso).astimezone(ZoneInfo("Asia/Shanghai")).date()
    return [
        {
            "position_index": index,
            **job_validity(
                PositionFacts.from_dict(position),
                day,
                now_iso=now_iso,
                field_issues=position_field_issues(result, index + 1),
            ),
        }
        for index, position in enumerate(result["positions"])
    ]


def check_output_path(path, *, snapshot=None, blobs=None, inputs=()):
    """Every entry point requires a NEW explicit destination, never an input/blob."""
    if path is None:
        raise ValueError("A new explicit --output path is required")
    if path.exists() or path.is_symlink():
        raise ValueError("Output already exists; never overwrite a baseline or prior report")
    output = path.resolve()
    protected = [p.resolve() for p in inputs if p is not None]
    if snapshot is not None:
        protected.extend(
            Path(str(snapshot.resolve()) + suffix) for suffix in ("", "-wal", "-shm", "-journal")
        )
    if output in protected:
        raise ValueError("Output must not overwrite an input artifact")
    if blobs is not None and output.is_relative_to(blobs.resolve()):
        raise ValueError("Output must not be inside immutable blob storage")
    return output


async def replay_record(
    saved,
    *,
    source_id,
    originals,
    blobs,
    candidate,
    now_iso,
    classifier,
    sandbox,
):
    record = {
        "resource_id": saved["resource_id"],
        "bundle_id": saved["id"],
        "source_id": source_id,
        "url": saved["url"],
        "bundle_body_ref": saved["body_ref"],
        "page_version_id": saved["page_version_id"],
        "selection": saved["selection"],
        "scan_state": saved["scan_state"],
        "scan_last_version_id": saved.get("scan_last_version_id"),
        "material_status": saved["status"],
        "warnings": list(saved["warnings"]),
        "classification": None,
        "reparse": [],
        "routing": None,
        "routing_evaluation": "not_applicable",
        "execution_status": "not_started",
        "execution": None,
        "result": None,
        "accepted_position_count": 0,
        "extracted_position_count": 0,
        "diagnostic_position_count": 0,
        "position_calendar": [],
        "diagnostic_position_calendar": [],
        "raw_outputs": [],
        "rejected_result": None,
        "rejected_origin": None,
        "diagnostic_normalization": None,
        "errors": [],
        "error_code": None,
        "recipe_execution_attempted": False,
        "executed_candidate_digest": None,
        "executed_runtime_fingerprint": None,
    }
    recorder = RecordingSandbox(sandbox)
    record["raw_outputs"] = recorder.outputs
    phase = "load_bundle"
    try:
        bundle = json.loads(read_blob(blobs, saved["body_ref"]))
        if not isinstance(bundle, dict) or not isinstance(bundle.get("page"), dict):
            raise ValueError("Saved bundle must contain a page object")
        for key, expected in (
            ("id", saved["id"]),
            ("source_id", source_id),
            ("resource_id", saved["resource_id"]),
        ):
            if bundle.get(key) not in (None, expected):
                raise ValueError("Saved bundle ownership conflicts with snapshot selection")
        # Material only: remove historical classification, routes, drafts, facts and dates.
        bundle = {
            key: bundle[key]
            for key in ("page", "attachments", "captured_at", "status", "errors")
            if key in bundle
        }
        bundle.update(
            id=saved["id"], source_id=source_id, resource_id=saved["resource_id"], url=saved["url"]
        )
        page = bundle["page"]
        if page.get("version_id") not in (None, saved["page_version_id"]):
            raise ValueError("Saved page version conflicts with authoritative bundle row")
        page.update(
            version_id=saved["page_version_id"], resource_id=saved["resource_id"], url=saved["url"]
        )
        if isinstance(classifier, CachedClassification):
            phase = "classification"
            record["classification"] = await classifier.classify(bundle)
            if not recruitment_article(record["classification"]):
                record["execution_status"] = "not_a_resolved_recruitment_article"
                return record
        phase = "reparse"
        record["reparse"] = await reparse_materials(bundle, originals, blobs, sandbox)
        record["warnings"].extend(
            item["warning"] for item in record["reparse"] if "warning" in item
        )
        phase = "classification"
        assessment = await classifier.classify(bundle)
        record["classification"] = assessment
        if not recruitment_article(assessment):
            record["execution_status"] = "not_a_resolved_recruitment_article"
            return record
        phase = "cost_routing"
        record["routing_evaluation"] = "failed"
        routing = decide_route(bundle, assessment, now=datetime.fromisoformat(now_iso)).as_dict()
        record.update(routing=routing, routing_evaluation="evaluated")
        if not resolved_article(assessment):
            record["execution_status"] = "not_a_resolved_recruitment_article"
            return record
        phase = "recipe_flow"
        record.update(
            recipe_execution_attempted=True,
            executed_candidate_digest=candidate_digest(candidate),
            executed_runtime_fingerprint=runtime_fingerprint(),
        )
        try:
            result = await RecipeRunner(recorder).run(candidate, bundle, routing=routing)
            record.update(
                execution_status="completed",
                result=result,
                routing=deepcopy(result["routing"]),
                execution=deepcopy(result["execution"]),
                errors=result["errors"],
                extracted_position_count=len(result["positions"]),
            )
            if result["status"] in {"success", "partial"}:
                record["accepted_position_count"] = len(result["positions"])
            record["position_calendar"] = position_calendar(result, now_iso)
        except RecipeError as error:
            record.update(
                execution_status="unfinished",
                error_code=error.code,
                errors=error.errors,
                runtime_error=error.as_dict(),
                execution=deepcopy(error.execution),
                rejected_result=error.rejected_result,
                rejected_origin=error.rejected_origin,
            )
            # Retain actual rejected artifacts; normalization is a separate UNACCEPTED
            # diagnostic view, never a manufactured successful runtime result.
            draft = error.rejected_result
            if not isinstance(draft, dict) or draft.get("schema_version") != DRAFT_CONTRACT:
                draft = next(
                    (
                        item.get("output")
                        for item in reversed(recorder.outputs)
                        if isinstance(item.get("output"), dict)
                        and item["output"].get("schema_version") == DRAFT_CONTRACT
                    ),
                    None,
                )
            if draft is not None:
                record["rejected_draft"] = deepcopy(draft)
                try:
                    preview = normalize_extraction(draft)
                    record["diagnostic_normalization"] = preview
                    record["diagnostic_position_count"] = len(preview["positions"])
                    record["extracted_position_count"] = len(preview["positions"])
                    record["diagnostic_position_calendar"] = position_calendar(preview, now_iso)
                except (KeyError, TypeError, ValueError) as diagnostic_error:
                    record["diagnostic_error"] = {
                        "type": type(diagnostic_error).__name__,
                        "message": str(diagnostic_error)[:1000],
                    }
    except Exception as error:
        record.update(
            execution_status="error",
            error_code="OFFLINE_REPLAY_ERROR",
            errors=[
                {
                    "code": "OFFLINE_REPLAY_ERROR",
                    "phase": phase,
                    "type": type(error).__name__,
                    "message": str(error)[:1000],
                }
            ],
        )
        # A reporting/clock error cannot leave rows marked accepted.
        record["accepted_position_count"] = 0
    return record


async def replay(args):
    if type(args.concurrency) is not int or not 1 <= args.concurrency <= 4:
        raise ValueError("Concurrency must be from 1 to 4")
    cache_path = getattr(args, "reuse_classification_from", None)
    snapshot, blobs = args.snapshot.resolve(strict=True), args.blobs.resolve(strict=True)
    check_output_path(
        getattr(args, "output", None),
        snapshot=snapshot,
        blobs=blobs,
        inputs=(args.candidate, getattr(args, "local_classifier", None), cache_path),
    )
    now_iso = aware_iso(args.now)
    snapshot_sha256 = file_digest(snapshot)
    fingerprint = runtime_fingerprint()
    candidate, provenance = load_candidate(args.candidate)
    with closing(connect_snapshot(snapshot)) as database:
        saved, originals, inventory = saved_inventory(database, args.source_id)
    local = None
    if getattr(args, "local_classifier", None):
        from youju.onboarding.shared_classifier import load_runtime_model

        local = load_runtime_model(args.local_classifier)
    classifier = Classifier(model=None, local_model=local)
    cached, cache_provenance = {}, None
    if cache_path is not None:
        cached, cache_provenance = upstream_cache(
            cache_path,
            snapshot=snapshot,
            blobs=blobs,
            source_id=args.source_id,
            clock=now_iso,
            local_key=local.key if local else None,
            saved=saved,
            inventory=inventory,
        )
    sandbox = Sandbox()
    # Probe failures are per-resource reparse/script observations, not a reason
    # to discard the full-source inventory or emit a false empty success report.
    probe_error = None
    try:
        await sandbox.probe()
    except Exception as error:
        probe_error = {"type": type(error).__name__, "message": str(error)[:1000]}
    LOG.info(
        "source=%s latest_saved_bundles=%s resources_without_bundle=%s concurrency=%s",
        args.source_id,
        len(saved),
        inventory["resources_without_saved_bundle"],
        args.concurrency,
    )
    results = [None] * len(saved)
    # Every row is rebuilt from saved material; only classification may be reused.
    targets = list(enumerate(saved))
    LOG.info("saved_material_targets=%s classification_only_cache=%s", len(targets), bool(cached))
    work = iter(targets)
    completed = 0

    async def worker():
        nonlocal completed
        for index, record in work:
            results[index] = await replay_record(
                record,
                source_id=args.source_id,
                originals=originals,
                blobs=blobs,
                candidate=candidate,
                now_iso=now_iso,
                classifier=CachedClassification(cached[record["resource_id"]]["classification"])
                if cached
                else classifier,
                sandbox=sandbox,
            )
            results[index].update(
                observation_origin="classification_only_revalidation" if cached else "fresh_replay",
                classification_origin="bound_upstream_cache" if cached else "current_classifier",
            )
            completed += 1
            if completed % 50 == 0 or completed == len(targets):
                LOG.info("processed=%s/%s saved material targets", completed, len(targets))

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    if any(
        row["recipe_execution_attempted"]
        and (
            row["executed_candidate_digest"] != provenance["digest"]
            or row["executed_runtime_fingerprint"] != fingerprint
        )
        for row in results
    ):
        raise ValueError("Runtime/candidate execution binding changed; no report may be published")
    counts = {
        "denominators": {
            "source_resources": inventory["source_resource_count"],
            "resources_without_saved_bundle": inventory["resources_without_saved_bundle"],
            "latest_saved_bundles": len(results),
            "classified_saved_bundles": sum(row["classification"] is not None for row in results),
            "unclassified_saved_bundles": sum(row["classification"] is None for row in results),
            "recruitment_article_candidates": sum(
                recruitment_article(row["classification"]) for row in results
            ),
            "resolved_recruitment_articles": sum(
                resolved_article(row["classification"]) for row in results
            ),
            "classification_review": sum(
                bool(row["classification"] and row["classification"].get("requires_review"))
                for row in results
            ),
        },
        "routing_evaluation": dict(Counter(row["routing_evaluation"] for row in results)),
        "cost_routing": dict(
            Counter(row["routing"]["category"] for row in results if row["routing"])
        ),
        "execution": dict(Counter(row["execution_status"] for row in results)),
        "classification_method": dict(
            Counter(row["classification"]["method"] for row in results if row["classification"])
        ),
        "business_type": dict(
            Counter(
                row["classification"]["business_type"] for row in results if row["classification"]
            )
        ),
        "page_roles": dict(
            Counter(
                role
                for row in results
                if row["classification"]
                for role in set(row["classification"]["page_roles"])
            )
        ),
        "errors": dict(Counter(row["error_code"] for row in results if row["error_code"])),
        "reparse": dict(Counter(item["status"] for row in results for item in row["reparse"])),
        "accepted_positions": sum(row["accepted_position_count"] for row in results),
        "completed_positions": sum(
            row["extracted_position_count"]
            for row in results
            if row["execution_status"] == "completed"
        ),
        "diagnostic_positions_not_accepted": sum(
            row["diagnostic_position_count"] for row in results
        ),
        "extracted_positions_including_diagnostics": sum(
            row["extracted_position_count"] for row in results
        ),
        "position_calendar_status": dict(
            Counter(item["status"] for row in results for item in row["position_calendar"])
        ),
        "diagnostic_position_calendar_status": dict(
            Counter(
                item["status"] for row in results for item in row["diagnostic_position_calendar"]
            )
        ),
    }
    LOG.info("counts=%s", json.dumps(counts, ensure_ascii=False))
    report = {
        "schema_version": REPORT_SCHEMA,
        "source_id": args.source_id,
        "snapshot": str(snapshot),
        "snapshot_sha256": snapshot_sha256,
        "blobs": str(blobs),
        "clock": now_iso,
        "snapshot_mode": "read_only_immutable_settled_backup_no_wal",
        "network_requests": 0,
        "model_calls": 0,
        "models_enabled": False,
        "classification": {
            "pipeline": "current_shared_rules_local_host_no_llm",
            "local_model_key": local.key if local else None,
            "local_model_note": "Host default has no implicit local artifact; none auto-discovered",
            "saved_assessments_reused": False,
            "upstream_classifications_reused": bool(cached),
            "implementation_key": classifier_implementation_key(),
        },
        "candidate": provenance,
        "runtime_contract": RUNTIME_CONTRACT,
        "runtime_fingerprint": fingerprint,
        "position_contract": POSITION_CONTRACT,
        "source_audit_policy": AUDIT_POLICY,
        "routing_policy": RoutingPolicy().version,
        "reviewed_context_applied": False,
        "inventory": inventory,
        "sandbox_probe_error": probe_error,
        "counts": counts,
        "gold_status": "unlabelled_observations_not_accuracy",
        "acceptance_scope": "runtime_validated_only_not_admission",
        "limitations": [
            "No independent labels: counts/coverage are NOT accuracy or admission evidence",
            "Only saved bundles are replayable; resources without one remain acquisition gaps",
            "Rejected/unfinished normalization rows are diagnostics, never accepted positions",
            "Cost routing is approximate; only position-domain dates feed job calendar status",
            "Accepted means runtime rows, not source adoption/publication or confirmed hiring",
            "Calendar/change tables are inventoried only; no old notice-period context applied",
            "No model fallback: MODEL_DISABLED remains an unfinished execution",
            "Old parser evidence mismatches retain the old view with a warning",
            "Supplied candidate provenance is not authenticated; template is not a source recipe",
        ],
        "records": results,
        "cache": cache_provenance,
        "actual_recipe_execution_count": sum(
            bool(row.get("recipe_execution_attempted")) for row in results
        ),
        "current_reparse_counts": dict(
            Counter(item["status"] for row in results for item in row["reparse"])
        ),
    }
    report["funnel"] = normalized_funnel(report, snapshot, blobs)
    # A newly appearing WAL is a change too, even if the main SQLite bytes match.
    with closing(connect_snapshot(snapshot)):
        pass
    if file_digest(snapshot) != snapshot_sha256:
        raise ValueError("Snapshot changed during read-only replay; no report may be published")
    if runtime_fingerprint() != fingerprint:
        raise ValueError("Runtime fingerprint changed during replay; no report may be published")
    return report


def normalized_funnel(report, snapshot, blobs):
    """Official pure funnel API; all resources and current saved material bindings.

    Raw replay results are diagnostics even if a fallback/historical bundle was
    runnable. Only independently current material can contribute accepted counts.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from youju.onboarding.funnel import (
        FunnelContext,
        aggregate_funnel,
        snapshot_observations,
    )

    classifier_key = classification_key(report["classification"]["local_model_key"])
    context = FunnelContext(
        source_id=report["source_id"],
        clock=report["clock"],
        classifier_key=classifier_key,
        recipe_id=report["candidate"]["digest"],
        generation=0,
        mode="new_contract_replay",
        contract=RUNTIME_CONTRACT,
    )
    engine = create_engine("sqlite://", creator=lambda: connect_snapshot(snapshot))
    records = {row["resource_id"]: row for row in report["records"]}
    observations = []
    try:
        with Session(engine, autoflush=False) as session:
            _, baseline = snapshot_observations(
                session,
                blobs=blobs,
                source_id=report["source_id"],
                clock=report["clock"],
                material_only=True,
            )
            for saved in baseline:
                current = saved.material_current
                reasons = list(saved.reasons)
                row = records.get(saved.resource_id)
                label = row["classification"] if row and row["classification"] else {}
                if row and row["bundle_id"] != saved.bundle_id:
                    current = False
                    reasons.append("replayed_bundle_not_current_scan_bundle")
                state = "missing"
                if row:
                    if not current:
                        state = "stale"
                    elif row["execution_status"] in {"unfinished", "error"}:
                        state = "failed"
                    elif label.get("requires_review"):
                        state = "review"
                    elif not resolved_article(label):
                        state = "missing"
                    elif row["execution_status"] == "completed":
                        status = row["result"]["status"]
                        state = (
                            "accepted"
                            if status in {"success", "partial"}
                            else ("review" if status == "review" else "rejected")
                        )
                if row and row["error_code"]:
                    reasons.append(row["error_code"])
                elif row is None:
                    reasons.append("no_saved_bundle")
                observations.append(
                    replace(
                        saved,
                        classifier_key=context.classifier_key,
                        recipe_id=context.recipe_id,
                        generation=context.generation,
                        material_current=current,
                        classified=bool(label) and current,
                        page_roles=tuple(label.get("page_roles", [])),
                        business_type=label.get("business_type", "unknown"),
                        classification_review=label.get("requires_review", False),
                        state=state,
                        # No notice-level expiry is derived from routing or mixed jobs.
                        validity="unknown",
                        cancelled=False,
                        # The funnel's existing aggregation label is internal, not a Recipe tier.
                        quality="full"
                        if row and row["execution_status"] == "completed"
                        else "unversioned",
                        routing_category=row["routing"]["category"]
                        if row and row.get("routing") and current
                        else "unknown",
                        position_calendar_statuses=tuple(
                            item["status"] for item in row.get("position_calendar", [])
                        )
                        if row and current
                        else (),
                        reported_positions=row["extracted_position_count"] if row else 0,
                        accepted_positions=row["accepted_position_count"]
                        if row and current and state == "accepted"
                        else 0,
                        reasons=tuple(reasons),
                    )
                )
    finally:
        engine.dispose()
    return aggregate_funnel(context, observations)


def write_report(path, report):
    path = check_output_path(
        path,
        snapshot=Path(report["snapshot"]) if report.get("snapshot") else None,
        blobs=Path(report["blobs"]) if report.get("blobs") else None,
        inputs=[Path(report["cache"]["path"])] if report.get("cache") else (),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".source-replay-", delete=False
        ) as f:
            temporary = Path(f.name)
            total = 0
            for part in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(report):
                encoded = part.encode("utf-8")
                total += len(encoded)
                if total > MAX_REPORT_BYTES:
                    raise ValueError("Report exceeds 512 MiB; no partial report published")
                f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        # Publish atomically without clobbering a file created after the preflight.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--blobs", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", type=aware_iso, required=True)
    parser.add_argument(
        "--candidate",
        type=Path,
        help="Canonical recipe-runtime-v3 JSON; default is the host template, not Agent-authored",
    )
    parser.add_argument("--concurrency", type=concurrency, default=2)
    parser.add_argument(
        "--reuse-classification-from",
        type=Path,
        help="Bound current classification ONLY; recompute cost routing and execute current Flow; "
        "old report schemas/contracts require recomputation without this flag",
    )
    parser.add_argument(
        "--local-classifier",
        type=Path,
        help="Optional host-approved shared JSON artifact; host default is none",
    )
    args = parser.parse_args()
    try:
        check_output_path(
            args.output,
            snapshot=args.snapshot,
            blobs=args.blobs,
            inputs=(args.candidate, args.local_classifier, args.reuse_classification_from),
        )
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = asyncio.run(replay(args))
    write_report(args.output, report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"records", "funnel"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
