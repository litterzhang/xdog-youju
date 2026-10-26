"""Train and evaluate a local classifier from explicitly selected saved silver labels.

Historical model decisions are not human ground truth. Training/calibration/test
content groups are disjoint; the independent test agreement is reported separately
from rule/local coverage over the whole inventory. No model service is contacted.
"""

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from youju.onboarding.classification import Classifier, validate_classification
from youju.onboarding.classification_budget import covered
from youju.onboarding.classification_features import content_group, feature_text
from youju.onboarding.cli import _store
from youju.onboarding.local_classifier import LocalPageModel


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def train(args, store):
    summaries = store.bundles(args.source, summaries=True)
    groups, conflicts, all_bundles = {}, set(), {}
    for item in summaries:
        bundle = store.load_bundle(item["id"])
        all_bundles[bundle["id"]] = bundle
        label = bundle.get("assessment", {})
        if (
            label.get("method") not in {"small", "large"}
            or not covered(label)
            or label.get("business_type") not in {"recruitment", "unrelated"}
            or validate_classification(bundle, label)
        ):
            continue
        group = content_group(bundle)
        record = {
            "id": bundle["id"],
            "group": group,
            "label": label["business_type"],
            "text": feature_text(bundle),
            "origin": "historical-xdog-silver",
            "url": bundle["url"],
            "evidence_ids": label["evidence_ids"],
        }
        if group in groups and groups[group]["label"] != record["label"]:
            conflicts.add(group)
        else:
            groups.setdefault(group, record)
    splits = {"training": [], "calibration": [], "test": []}
    for group, record in sorted(groups.items()):
        if group in conflicts:
            continue
        bucket = int(group[:8], 16) % 10
        split = "training" if bucket < 6 else "calibration" if bucket < 8 else "test"
        splits[split].append(record)
    for name, records in splits.items():
        save(args.output / f"{name}.json", records)
    model = LocalPageModel.fit(
        splits["training"], splits["calibration"], min_precision=0.98, min_support=5
    )
    model.save(args.output / "model.json")
    reloaded = LocalPageModel.load(args.output / "model.json")
    assert reloaded.key == model.key
    classifier = Classifier(local_model=reloaded)
    outcomes, counts = [], Counter()
    for bundle in all_bundles.values():
        result = await classifier.classify(bundle)
        counts[result["method"]] += 1
        if covered(result):
            counts["covered"] += 1
        else:
            counts["review"] += 1
        outcomes.append(
            {
                "bundle_id": bundle["id"],
                "url": bundle["url"],
                "title": bundle["page"].get("title"),
                "result": result,
            }
        )
    by_id = {row["bundle_id"]: row["result"] for row in outcomes}
    heldout, confusion = Counter(), Counter()
    disagreements = []
    for record in splits["test"]:
        result = by_id[record["id"]]
        heldout["total"] += 1
        if not covered(result):
            heldout["abstained"] += 1
            continue
        prediction = result["business_type"]
        heldout["accepted"] += 1
        heldout["agreed"] += prediction == record["label"]
        confusion[record["label"] + "->" + prediction] += 1
        if prediction != record["label"]:
            disagreements.append(
                {"url": record["url"], "silver_label": record["label"], "result": result}
            )
    report = {
        "source_id": args.source,
        "training_algorithm": {"features": "char-tfidf-2-4", "C": 4.0, "class_weight": "balanced"},
        "model_key": model.key,
        "model_metadata": model.metadata,
        "labels": "historical xdog decisions with evidence; silver labels, not human truth",
        "splits": {name: dict(Counter(r["label"] for r in rows)) for name, rows in splits.items()},
        "conflicting_groups_excluded": len(conflicts),
        "overlap": 0,
        "inventory_pages": len(outcomes),
        "methods": dict(counts),
        "cheap_coverage": counts["covered"] / len(outcomes) if outcomes else 0,
        "target_met": bool(outcomes) and counts["covered"] / len(outcomes) >= 0.95,
        "independent_test": dict(heldout),
        "test_confusion": dict(confusion),
        "test_disagreements": disagreements,
        "external_model_calls": 0,
    }
    save(args.output / "predictions.json", outcomes)
    save(args.output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with _store(args.database, args.storage) as store:
        asyncio.run(train(args, store))


if __name__ == "__main__":
    main()
