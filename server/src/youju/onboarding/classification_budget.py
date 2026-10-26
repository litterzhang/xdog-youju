"""Durable per-source LLM reservations and honest rules/local coverage accounting."""

import asyncio
import math
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.models import PageAssessment, Resource, ResourceBundle, ResourceScan, SourcePipeline
from youju.models.base import utc_now


def covered(result):
    return (
        bool(result)
        and not result.get("requires_review", True)
        and "unknown" not in result.get("page_roles", ["unknown"])
        and (result.get("business_type") != "unknown" or result.get("page_roles") == ["navigation"])
    )


class ClassificationBudget:
    def __init__(self, store, classifier_key, fraction=0.05):
        if (
            not isinstance(fraction, int | float)
            or isinstance(fraction, bool)
            or not 0 <= fraction <= 1
        ):
            raise ValueError("Classification LLM fraction must be between zero and one")
        self.store, self.key, self.fraction = store, classifier_key, fraction

    async def reserve(self, bundle, tier):
        return await asyncio.to_thread(self._reserve, bundle["source_id"], bundle["id"], tier)

    def _reserve(self, source_id, bundle_id, tier):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            current = (
                select(ResourceScan.bundle_id)
                .join(Resource)
                .where(Resource.source_id == source_id, ResourceScan.bundle_id.is_not(None))
            )
            observed = (
                select(PageAssessment.bundle_id)
                .join(ResourceBundle)
                .join(Resource)
                .where(Resource.source_id == source_id)
            )
            denominator = session.scalar(
                select(func.count()).select_from(current.union(observed).subquery())
            )
            limit = math.floor(denominator * self.fraction)
            budgets = dict(pipeline.summary.get("classification_budgets", {}))
            state = dict(budgets.get(self.key, {"reservations": []}))
            reservations = list(state["reservations"])
            # A rule/model profile change must not replenish a source's budget.
            # Retain each profile ledger for provenance, but debit their union.
            already_reserved = sum(len(item.get("reservations", [])) for item in budgets.values())
            allowed = already_reserved < limit
            if allowed:
                reservations.append(
                    {"bundle_id": bundle_id, "tier": tier, "reserved_at": utc_now().isoformat()}
                )
            state.update(
                reservations=reservations,
                denominator=denominator,
                limit=limit,
                fraction=self.fraction,
                reserved=len(reservations),
            )
            budgets[self.key] = state
            pipeline.summary = dict(pipeline.summary, classification_budgets=budgets)
            return allowed

    def summarize(self, source_id):
        bundles = self.store.bundles(source_id, classifier_key=self.key, summaries=True)
        methods, accepted = Counter(), Counter()
        review = unassessed = 0
        for bundle in bundles:
            result = bundle.get("assessment")
            if not result:
                unassessed += 1
                continue
            methods[result["method"]] += 1
            if covered(result):
                accepted[result["method"]] += 1
            else:
                review += 1
        cheap = accepted["rules"] + accepted["local"]
        acquisition = self.store.counts(source_id)
        pending_acquisitions = acquisition.get("ready", 0) + acquisition.get("running", 0)
        summary = {
            "classifier_key": self.key,
            "total_current_pages": len(bundles),
            "processed": len(bundles) - unassessed,
            "unassessed": unassessed,
            "pending_acquisitions": pending_acquisitions,
            "requires_review": review,
            "methods": dict(methods),
            "confident_by_method": dict(accepted),
            "cheap_covered": cheap,
            "cheap_coverage": cheap / len(bundles) if bundles else 0.0,
            "target": 0.95,
            "target_met": bool(bundles)
            and not unassessed
            and not pending_acquisitions
            and cheap / len(bundles) >= 0.95,
        }
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            budgets = pipeline.summary.get("classification_budgets", {})
            state = budgets.get(self.key, {})
            summary["llm_profile_reserved_calls"] = state.get("reserved", 0)
            summary["llm_reserved_calls"] = sum(
                len(item.get("reservations", [])) for item in budgets.values()
            )
            summary["llm_call_limit"] = state.get("limit", math.floor(len(bundles) * self.fraction))
            summary["llm_fraction"] = self.fraction
            pipeline.summary = dict(pipeline.summary, classification=summary)
        return summary
