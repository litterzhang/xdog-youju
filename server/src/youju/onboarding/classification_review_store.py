"""Runtime-owned review reservations, provenance and compare-and-swap adoption.

Use the existing source metadata ledger, like classification_budget. No Agent
opens this DB, authoring service or human-gold/publication tables.
"""

import copy
from collections import Counter
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import PageAssessment, Resource, ResourceScan, ScanPolicy, SourcePipeline
from youju.models.base import utc_now

from .classification_budget import covered
from .classification_policy import scheduled
from .store import digest, json_bytes

LEDGER = "classification_agent_review"


class ReviewLedger:
    def __init__(self, store, classifier_key, policy):
        self.store, self.key, self.policy = store, classifier_key, policy

    @staticmethod
    def _state(pipeline):
        return copy.deepcopy(pipeline.summary.get(LEDGER, {"batches": []}))

    def snapshots(self, source_id):
        with self.store.lock, Session(self.store.engine) as session:
            pipeline = session.get(SourcePipeline, source_id)
            attempted = {
                page["bundle_id"]
                for batch in self._state(pipeline)["batches"]
                for page in batch["pages"]
            }
            rows = session.execute(
                select(PageAssessment, ResourceScan)
                .join(ResourceScan, ResourceScan.bundle_id == PageAssessment.bundle_id)
                .join(Resource, Resource.id == ResourceScan.resource_id)
                .where(Resource.source_id == source_id, PageAssessment.classifier_key == self.key)
            ).all()
            return [
                {
                    "assessment_id": row.id,
                    "bundle_id": row.bundle_id,
                    "resource_id": scan.resource_id,
                    "result": copy.deepcopy(row.result),
                    "result_digest": digest(row.result),
                }
                for row, scan in rows
                if not covered(row.result) and row.bundle_id not in attempted
            ]

    def reserve(self, source_id, selected, payload):
        if not selected:
            return None
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            self.store.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            # Do not change the classification pins of an in-flight author/audit.
            if pipeline.stage not in {"classification", "active"}:
                raise ValueError("Classification review requires classification/incremental phase")
            state = self._state(pipeline)
            attempted = {p["bundle_id"] for b in state["batches"] for p in b["pages"]}
            if len(state["batches"]) >= self.policy.max_batches or any(
                p["bundle_id"] in attempted for p in selected
            ):
                return None
            for item in selected:
                row = session.get(PageAssessment, item["assessment_id"])
                scan = session.get(ResourceScan, item["resource_id"])
                if (
                    scan is None
                    or row is None
                    or scan.bundle_id != item["bundle_id"]
                    or digest(row.result) != item["result_digest"]
                ):
                    return None
            key = uuid4().hex
            from .classification_review import REVIEW_VERSION

            state["batches"].append(
                {
                    "id": key,
                    "status": "reserved",
                    "classifier_key": self.key,
                    "model": self.policy.model,
                    "prompt_version": REVIEW_VERSION,
                    "reserved_at": utc_now().isoformat(),
                    "input_ref": self.store.blobs.put(json_bytes(payload)),
                    "pages": [
                        {k: item[k] for k in ("assessment_id", "bundle_id", "result_digest")}
                        | {
                            "status": "reserved",
                            "original_ref": self.store.blobs.put(json_bytes(item["result"])),
                        }
                        for item in selected
                    ],
                }
            )
            pipeline.summary = dict(pipeline.summary, **{LEDGER: state})
            return key

    def _update(self, source_id, batch_id, callback):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            state = self._state(pipeline)
            batch = next(b for b in state["batches"] if b["id"] == batch_id)
            callback(session, batch)
            pipeline.summary = dict(pipeline.summary, **{LEDGER: state})

    def usage(self, source_id, batch_id, usage):
        self._update(source_id, batch_id, lambda session, batch: batch.update(usage=usage))

    def fail(self, source_id, batch_id, code):
        def save(session, batch):
            batch.update(status="failed", error_code=code)
            for page in batch["pages"]:
                page["status"] = "failed"

        self._update(source_id, batch_id, save)

    def finish_page(self, source_id, batch_id, item, outcome, result, errors):
        def save(session, batch):
            page = next(p for p in batch["pages"] if p["bundle_id"] == item["bundle_id"])
            row = session.get(PageAssessment, item["assessment_id"])
            scan = session.get(ResourceScan, item["resource_id"])
            pipeline = session.get(SourcePipeline, source_id)
            current = (
                pipeline.stage in {"classification", "active"}
                and row is not None
                and scan is not None
                and scan.bundle_id == item["bundle_id"]
                and digest(row.result) == item["result_digest"]
            )
            page.update(
                status=outcome if current else "stale",
                errors=errors,
                result_ref=self.store.blobs.put(json_bytes(result)),
            )
            if outcome != "resolved" or not current:
                return
            row.result = scheduled(result)
            policy = session.get(ScanPolicy, item["resource_id"])
            cadence = row.result["revisit"]
            if policy is None:
                policy = ScanPolicy(resource_id=item["resource_id"])
                session.add(policy)
            policy.assessment_id, policy.cadence = row.id, cadence
            policy.reason = row.result["reason"]
            policy.next_scan_at = self.store.next_scan_at(
                scan.last_success_at or utc_now(), cadence
            )

        self._update(source_id, batch_id, save)

    def complete(self, source_id, batch_id):
        self._update(source_id, batch_id, lambda session, batch: batch.update(status="complete"))

    def summary(self, source_id, skipped):
        candidates = self.snapshots(source_id)
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            state = self._state(pipeline)
            counts = Counter(p["status"] for b in state["batches"] for p in b["pages"])
            pending = sum(p["bundle_id"] not in skipped for p in candidates)
            remaining = max(0, self.policy.max_batches - len(state["batches"]))
            result = {
                "model": self.policy.model,
                "reserved_batches": len(state["batches"]),
                "max_batches": self.policy.max_batches,
                "remaining_batches": remaining,
                "page_outcomes": dict(counts),
                "pending_ai": pending,
                "deferred_budget": pending if not remaining else 0,
                "capture_or_input_unresolved": len(skipped),
                "human_review_required": False,
                "can_continue": bool(pending and remaining),
            }
            state["summary"] = result
            pipeline.summary = dict(pipeline.summary, **{LEDGER: state})
            return result
