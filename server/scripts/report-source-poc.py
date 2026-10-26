"""Small source POC diagnostic: SQLite mode=ro online backup, then local snapshot queries.

No initialization/worker lock/model calls/script execution; normal read locks/WAL effects possible.
Run final evidence after the worker stops. Old committed state does NOT mean a stalled worker.
Export explicit metadata/counts only, never text, scripts, credentials or model payloads.
"""

import argparse
import json
import math
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from youju import models as m
from youju.crawlers.policy import origin
from youju.database import create_sqlite_engine
from youju.models.base import utc_now
from youju.onboarding.auditing import audit_dimensions, current_source_positions, evaluation_day
from youju.onboarding.classification_budget import covered
from youju.onboarding.store import PipelineStore, approved_origins, toolkit_key
from youju.poc.scope import DiscoveryScope


def pick(value, names):
    return {key: value[key] for key in names.split() if key in value}


def counts(values):
    return dict(Counter(str(value) for value in values))


def audit_info(row):
    if row is None:
        return None
    fields = "total_announcements processed remaining statuses validity position_count passed"
    return {
        "id": row.id,
        "recipe_id": row.recipe_id,
        "status": row.status,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "summary": pick(row.summary, fields + " evaluated_on generation errors_by_code"),
        "admission": pick(row.summary.get("admission", {}), "policy blocking_reasons"),
    }


def recipe_info(store, row):
    if row is None:
        return None
    info = {
        "id": row.id,
        "status": row.status,
        "body_ref": row.body_ref,
        "toolkit_matches": row.validation.get("toolkit_key") == toolkit_key(),
        "recorded_validation_errors": len(row.validation.get("errors", [])),
    }
    try:
        candidate = json.loads(store.blobs.read(row.body_ref))
        from xdog.flow.loader import parse_workflow
        from xdog.flow.models import entry_frontier

        from youju.recipe_runtime.contracts import validate_candidate
        from youju.recipe_runtime.workflow import default_workflow

        workflow = candidate["workflow"]
        errors = validate_candidate(candidate)
        nodes = workflow["nodes"]
        info.update(
            main_stage_count=len(nodes),
            native_subflow_count=sum(node.get("type") == "subflow" for node in nodes),
            entry_is_parse_subflow=not errors
            and entry_frontier(parse_workflow(workflow)) == ("parse",),
            parser_scripts_present={
                name: isinstance(candidate.get(name), str) and bool(candidate[name].strip())
                for name in ("parser_py", "undated_parser_py")
            },
            uses_default_graph=workflow == default_workflow(),
            current_contract_valid=not errors,
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        info["artifact_error"] = "Unreadable or invalid recipe artifact"
    return info  # Inspection only, not a fresh validation or execution claim.


def fetch_stats(attempts, modes):
    return {
        "success": sum(a.outcome in {"downloaded", "not_modified"} for a in attempts),
        "failure": sum(a.outcome == "failed" for a in attempts),
        "mode_outcomes": counts(f"{modes.get(a.id, 'unrecorded')}:{a.outcome}" for a in attempts),
    }


def boundaries(session, source, pipeline, resources, attempts):
    root = session.get(m.OrganizationScope, source.publisher_organization_id)
    scope = DiscoveryScope(root.root_domain) if root else None
    base = origin(httpx.URL(source.base_url))
    pages = approved_origins(base, pipeline.config)
    attachments = approved_origins(base, pipeline.config, attachments=True)
    edges = session.scalars(
        select(m.Discovery.target_resource_id)
        .join(m.ResourceVersion, m.Discovery.parent_version_id == m.ResourceVersion.id)
        .join(m.Resource, m.Resource.id == m.ResourceVersion.resource_id)
        .where(m.Resource.source_id == source.id)
    ).all()
    targets = [session.get(m.Resource, key) for key in set(edges)]
    registered = [source.base_url] + [r.normalized_url for r in resources]
    registered += [seed.resource.normalized_url for seed in source.seeds]
    checks = {}
    for label, urls in (
        ("registered", registered),
        ("discovered_targets", [r.normalized_url for r in targets]),
    ):
        checks[label] = {
            "checked": len(urls),
            "outside_root_or_invalid": sum(not scope or not scope.contains(url) for url in urls),
        }
    resource_map = {r.id: r for r in resources}
    for label in ("requested", "final"):
        bad, missing = [], 0
        for attempt in attempts:
            resource = resource_map[attempt.resource_id]
            url = resource.normalized_url if label == "requested" else attempt.final_url
            if not url:
                missing += 1
                continue
            try:
                allowed = attachments if resource.kind == "attachment" else pages
                valid = scope and scope.contains(url) and origin(httpx.URL(url)) in allowed
            except (ValueError, httpx.InvalidURL):
                valid = False
            if not valid:
                bad.append(attempt)
        checks[label + "_fetch"] = {
            "checked": len(attempts) - missing,
            "missing": missing,
            "violations": len(bad),
            "outcomes": counts(a.outcome for a in bad),
        }
    return {
        "root": root.root_domain if root else None,
        "page_origins": sorted(pages),
        "attachment_origins": sorted(attachments),
        "checks": checks,
    }


def build_report(store, source_id, phase):
    day = evaluation_day()
    with Session(store.engine) as session:
        source = session.get(m.Source, source_id)
        pipeline = session.get(m.SourcePipeline, source_id)
        if source is None or pipeline is None:
            raise ValueError("Source/onboarding pipeline not found")
        saved = pipeline.summary.get("classification", {})
        key = saved.get("classifier_key")
        ledgers = pipeline.summary.get("classification_budgets", {})
        bundles = store.bundles(source_id, classifier_key=key, summaries=True)
        assessed = [b["assessment"] for b in bundles if b.get("assessment")]
        confident = [a for a in assessed if covered(a)]
        cheap = sum(a.get("method") in {"rules", "local"} for a in confident)

        def records(table, *joins):
            statement = select(table)
            for parent in joins:
                statement = statement.join(parent)
            return session.scalars(statement.where(m.Resource.source_id == source_id)).all()

        observed = records(m.PageAssessment.bundle_id, m.ResourceBundle, m.Resource)
        scans = records(m.ResourceScan, m.Resource)
        current_ids = {scan.bundle_id for scan in scans if scan.bundle_id}
        denominator = len(current_ids | set(observed))
        reserved = sum(len(v.get("reservations", [])) for v in ledgers.values())
        fraction = saved.get("llm_fraction", ledgers.get(key, {}).get("fraction"))
        limit = math.floor(denominator * fraction) if isinstance(fraction, int | float) else None
        notices = {
            b["id"]
            for b in bundles
            if "article" in b.get("assessment", {}).get("page_roles", [])
            and b["assessment"].get("business_type")
            in {"recruitment", "correction", "cancellation"}
        }
        history = records(m.AuditResult, m.ResourceBundle, m.Resource)
        binding = session.get(m.SourceRecipeBinding, source_id)
        active_id = binding.active_recipe_id if binding else None
        candidate_id = binding.candidate_recipe_id if binding else None
        recipe_id = active_id if pipeline.stage == "active" else candidate_id
        generation = (
            (binding.generation if binding else 0)
            if pipeline.stage == "active"
            else pipeline.summary.get("candidate_generation")
        )
        outcomes = [
            a
            for a in history
            if a.bundle_id in notices
            and a.recipe_id == recipe_id
            and a.evaluated_on == day
            and a.generation == generation
        ]
        accepted = [a for a in outcomes if a.status == "success" and not a.errors]
        partial = [a for a in outcomes if a.status == "partial" and not a.errors]
        review = [
            a for a in outcomes if a.status not in {"success", "partial", "cancelled"} or a.errors
        ]
        bundle_status = {b["id"]: b["status"] for b in bundles}
        parse_ids = records(m.Position.parse_id, m.ParseResult, m.ResourceVersion, m.Resource)
        by_parse = Counter(parse_ids)
        current = current_source_positions(store, source_id)
        with_expired = current_source_positions(store, source_id, include_expired=True)
        latest = session.scalar(
            select(m.AuditRun)
            .where(m.AuditRun.source_id == source_id, m.AuditRun.kind == "onboarding")
            .order_by(m.AuditRun.created_at.desc(), m.AuditRun.id.desc())
            .limit(1)
        )
        active = None
        if active_id:
            active = session.get(m.SourceRecipe, active_id)
        recipe = recipe_info(store, active)
        resources = records(m.Resource)
        attempts = records(m.FetchAttempt, m.Resource)
        captures = records(m.Capture, m.FetchAttempt, m.Resource)
        versions = records(m.ResourceVersion, m.Resource)
        modes, queue = {c.attempt_id: c.mode for c in captures}, store.counts(source_id)
        missing = len(notices - {a.bundle_id for a in outcomes})
        activated = pipeline.activated_at
        since = [a for a in attempts if activated and a.started_at >= activated]
        report = {
            "phase": phase,
            "generated_at": utc_now().isoformat(),
            "source_id": source_id,
            "registry_status": str(source.status),
            "pipeline": {
                "stage": pipeline.stage,
                "state": pipeline.state,
                "active_recipe_id": active_id,
                "candidate_recipe_id": candidate_id,
                "generation": binding.generation if binding else 0,
                "candidate_generation": pipeline.summary.get("candidate_generation"),
                "updated_at": pipeline.updated_at.isoformat(),
                "activated_at": activated.isoformat() if activated else None,
                "inventory_incomplete": pipeline.summary.get("inventory_incomplete", False),
                "config": pick(
                    pipeline.config,
                    "max_resources max_depth max_bytes retry_delay allow_http",
                ),
            },
            "classification": {
                "classifier_key": key,
                "key_basis": "last persisted summary; else latest per bundle",
                "pages": len(bundles),
                "processed": len(assessed),
                "unassessed": len(bundles) - len(assessed),
                "pending_acquisitions": queue.get("ready", 0) + queue.get("running", 0),
                "review": len(assessed) - len(confident),
                "methods": counts(a.get("method") for a in assessed),
                "confident_by_method": counts(a.get("method") for a in confident),
                "cheap_coverage": cheap / len(bundles) if bundles else 0,
                "target_met": bool(bundles)
                and len(assessed) == len(bundles)
                and not (queue.get("ready", 0) + queue.get("running", 0))
                and cheap / len(bundles) >= 0.95,
                "llm_reserved_calls": reserved,
                "llm_profile_reserved_calls": len(ledgers.get(key, {}).get("reservations", [])),
                "denominator": denominator,
                "fraction": fraction,
                "limit": limit,
                "within_limit": reserved <= limit if limit is not None else None,
                "profile_ledgers": {
                    k: pick(v, "reserved limit denominator fraction") for k, v in ledgers.items()
                },
            },
            "audits": {
                "latest_onboarding": audit_info(latest),
                "scope": {"recipe_id": recipe_id, "evaluated_on": day, "generation": generation},
                "identified_announcements": len(notices),
                "missing": missing,
                "statuses": counts(a.status for a in outcomes),
                "extraction_statuses": counts(
                    audit_dimensions(a, bundle_status[a.bundle_id])["extraction_status"]
                    for a in outcomes
                ),
                "material_statuses": counts(bundle_status[a.bundle_id] for a in outcomes),
                "warnings": counts(
                    e["code"]
                    for a in outcomes
                    for e in audit_dimensions(a, bundle_status[a.bundle_id])["warnings"]
                ),
                "review_required": sum(a.status == "review" for a in outcomes),
                "repair_required": sum(a.status == "failed" for a in outcomes),
                "validity": counts(
                    a.result.get("validity", {}).get("status", "unknown") for a in outcomes
                ),
                "errors": counts(e.get("code", "unknown") for a in outcomes for e in a.errors),
            },
            "positions": {
                "total_stored_history": len(parse_ids),
                "accepted_stored_rows_in_audit_scope": sum(by_parse[a.parse_id] for a in accepted),
                "partial_stored_rows_in_audit_scope": sum(by_parse[a.parse_id] for a in partial),
                "review_candidate_rows_in_audit_scope": sum(
                    len(a.result.get("positions", [])) for a in review
                ),
                "current": len(current),
                "current_with_partial": len(
                    current_source_positions(store, source_id, include_partial=True)
                ),
                "current_with_partial_and_expired": len(
                    current_source_positions(
                        store, source_id, include_partial=True, include_expired=True
                    )
                ),
                "current_including_expired": len(with_expired),
                "expired_current": len(with_expired) - len(current),
                "current_validity": counts(p["validity"].get("status") for p in current),
            },
            "acquisition": {
                "queue": queue,
                "original_versions": len(versions),
                "captures_by_mode": counts(c.mode for c in captures),
                "fetch_history": fetch_stats(attempts, modes),
                "fetch_since_activation": fetch_stats(since, modes),
            },
            "boundaries": boundaries(session, source, pipeline, resources, attempts),
            "active_recipe": recipe,
            "notes": [
                "Labels/committed state do not prove success, activation, or worker liveness.",
                "LLM reservations sum ALL profiles; confidence coverage is not accuracy/cost.",
                "Audit scope: current identified terminal bundles + recipe/day/generation.",
                "Accepted/history rows are not current/eligible; review may be unpersisted.",
                "Current is strict; partial requires opt-in and retains warnings. "
                "Unknown validity is not proof of open hiring.",
                "Fetch modes require Capture; missing stays unrecorded. Not audit success.",
                "Stored URLs/approvals only; no DNS/redirect proof. Failures may be blocks.",
                "Recipe inspection only; no runtime/evidence revalidation or full blob scan.",
            ],
        }
    if day != evaluation_day():
        raise ValueError("Evaluation day changed; rerun report")
    return report


def snapshot_report(database, storage, source_id, phase="ongoing"):
    database, storage = Path(database).resolve(), Path(storage).resolve()
    if not database.is_file() or not storage.is_dir():
        raise ValueError("Existing SQLite database and blob directory required")
    with tempfile.TemporaryDirectory(prefix="youju-report-") as directory:
        target = Path(directory) / "snapshot.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination, pages=256)
        engine = create_sqlite_engine(target)  # Private snapshot; never initialize the live DB.
        try:
            report = build_report(PipelineStore(engine, storage), source_id, phase)
            report["snapshot"] = "SQLite online backup; source URI mode=ro"
            return report
        finally:
            engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--storage", required=True, type=Path)
    parser.add_argument("--phase", choices=("ongoing", "final"), default="ongoing")
    parser.add_argument(
        "--output", type=Path, help="Create DIR/evidence.json exclusively; default stdout"
    )
    args = parser.parse_args(argv)
    try:
        report = snapshot_report(args.database, args.storage, args.source, args.phase)
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            with (args.output / "evidence.json").open("x", encoding="utf-8") as stream:
                stream.write(text)
        else:
            print(text, end="")
    except (OSError, ValueError, sqlite3.Error, SQLAlchemyError) as error:
        parser.exit(
            1,
            f"Report failed ({type(error).__name__}); check inputs and output location.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
