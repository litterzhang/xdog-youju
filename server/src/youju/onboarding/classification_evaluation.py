"""Combined rules/shared-model evaluation on saved material; never call an LLM."""

import asyncio
from collections import Counter

from .classification import Classifier
from .classification_corpus import _identity, normalized_text
from .classification_features import feature_text
from .classification_policy import notice_candidate
from .shared_classifier import _unique_records, evaluate_model


def evaluate_pipeline(model, records, load_bundle, *, allow_synthetic=False):
    observed = {}

    async def classify(row):
        if row["id"] in observed:
            result = observed[row["id"]][1]
        else:
            bundle = load_bundle(row["id"], row["source_id"])
            if normalized_text(feature_text(bundle)) != normalized_text(row["text"]):
                raise ValueError(
                    "Saved features changed; re-export and annotate the frozen material"
                )
            result = await Classifier(local_model=model).classify(bundle)
            observed[row["id"]] = (row, result)
        if (
            result["requires_review"]
            or result["business_type"] == "unknown"
            or "unknown" in result["page_roles"]
        ):
            return None
        return {"page_role": result["page_roles"][0], "business_type": result["business_type"]}

    report = evaluate_model(
        model,
        records,
        allow_synthetic=allow_synthetic,
        decision_fn=lambda row: asyncio.run(classify(row)),
    )
    methods = Counter()
    announcements = exclusions = 0
    selected_ids = {row["id"] for row in _unique_records(records)}
    for row, result in observed.values():
        if row["id"] not in selected_ids:
            continue
        methods[result["method"]] += 1
        if row["business_type"] == "recruitment" and row["page_role"] in {"article", "attachment"}:
            announcements += 1
            if not result["requires_review"] and not notice_candidate(result):
                exclusions += 1

    # These describe the complete rules/local/host path. Unknown/review is not
    # a cheap-path coverage success or a confident negative classification.
    def safety_metrics(rows):
        counts = Counter()
        for row in rows:
            result = observed[row["id"]][1]
            resolved = (
                not result["requires_review"]
                and result["business_type"] != "unknown"
                and "unknown" not in result["page_roles"]
            )
            counts["total"] += 1
            counts["resolved"] += int(resolved)
            counts["cheap_resolved"] += int(resolved and result["method"] in {"rules", "local"})
            counts["review_or_unknown"] += int(not resolved)
            counts["expected_role_present"] += int(
                resolved and row["page_role"] in result["page_roles"]
            )
            if row["business_type"] == "recruitment" and row["page_role"] in {
                "article",
                "attachment",
            }:
                counts["announcement_support"] += 1
                counts["announcement_deferred"] += int(not resolved)
                counts["announcement_confidently_excluded"] += int(
                    resolved and not notice_candidate(result)
                )
                counts["announcement_recognized"] += int(resolved and notice_candidate(result))
            if row["page_role"] == "notice_list":
                counts["list_support"] += 1
                counts["list_role_missed"] += int(
                    resolved and "notice_list" not in result["page_roles"]
                )
                counts["list_deferred"] += int(not resolved)
            if row["business_type"] in {"guide", "report", "unrelated", "result"}:
                counts["nonvacancy_support"] += 1
                counts["nonvacancy_extraction_candidates"] += int(notice_candidate(result))
        return {
            **counts,
            "cheap_resolved_coverage": counts["cheap_resolved"] / counts["total"]
            if counts["total"]
            else None,
            "role_membership_accuracy": counts["expected_role_present"] / counts["total"]
            if counts["total"]
            else None,
            "note": "Coverage is not accuracy; unknown/review is neither "
            "a resolved hit nor an exclusion.",
        }

    selected = _unique_records(records)
    report["whole_chain_safety"] = safety_metrics(selected)
    report["per_source_safety"] = {
        source: safety_metrics(
            _unique_records([row for row in records if _identity(row["source_id"]) == source])
        )
        for source in report["per_source"]
    }
    report.update(
        evaluation_path="rules-plus-shared-with-host-guards-no-llm",
        methods=dict(methods),
        announcement_support=announcements,
        announcement_erroneous_exclusions=exclusions,
        announcement_erroneous_exclusion_rate=exclusions / announcements if announcements else None,
        external_model_calls=0,
        evaluation_note="Model-only and combined scores are different measurements. "
        "Primary-role metrics use the first role; role-membership metrics retain compound roles. "
        "Per-source measurements run the same combined decision path, "
        "never model-only predictions.",
    )
    return report
