"""Host-selected, frozen submission regressions, separate from author startup samples.

Reuse the existing case blobs and assertion checker. Private expected values never
enter the author input mount or case tools. These are regression checks, not an
unseen accuracy estimate; source-owned holdouts remain with the source audit.
"""

import hashlib
import json
from pathlib import Path

from youju.positions.expectations import MAX_EXPECTATION_BYTES, validate_expected
from youju.serialization import json_bytes

from .cases import freeze_cases, load_catalog, read_case

CONTRACT = "recipe-submission-regressions-v1"
MAX_CASES = 8


def checker_key():
    root = Path(__file__).parents[1]
    paths = [
        Path(__file__),
        Path(__file__).with_name("authoring.py"),
        root / "positions/expectations.py",
        root / "positions/semantics.py",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def select_cases(cases, startup_ids, labels):
    """Fixed host order: known regressions first, then format/template diversity."""
    eligible = []
    for bundle in cases:
        assessment = bundle.get("assessment") or {}
        if bundle["id"] in startup_ids or (
            assessment
            and (
                assessment.get("business_type") != "recruitment"
                or not {"article", "attachment"}.intersection(assessment.get("page_roles", []))
            )
        ):
            continue
        eligible.append(bundle)
    eligible.sort(key=lambda b: (b["id"] not in labels, b["id"]))

    def families(bundle):
        formats = {
            d.get("format", "unknown") for d in [bundle["page"], *bundle.get("attachments", [])]
        }
        return {"format:" + f for f in formats} | {
            "template:" + str((bundle.get("assessment") or {}).get("template_key") or "unknown")
        }

    # Reserve room for format/template counterexamples even with many known failures.
    selected = [b for b in eligible if b["id"] in labels][: MAX_CASES // 2]
    selected_ids = {b["id"] for b in selected}
    covered = set().union(*(families(b) for b in selected))
    for bundle in eligible:
        observed = families(bundle)
        if bundle["id"] not in selected_ids and not observed <= covered:
            selected.append(bundle)
            selected_ids.add(bundle["id"])
            covered.update(observed)
        if len(selected) == MAX_CASES:
            return selected
    return (selected + [b for b in eligible if b["id"] not in selected_ids])[:MAX_CASES]


def freeze_acceptance(blobs, source_id, cases, startup_ids, labels=None):
    labels = labels or {}
    selected = select_cases(cases, set(startup_ids), labels)
    private_labels = {}
    for bundle in selected:
        label = labels.get(bundle["id"])
        if label is not None:
            if set(label) != {"label_id", "expected"} or not label["label_id"]:
                raise ValueError("Invalid host regression label")
            expected = validate_expected(label["expected"])
            private_labels[bundle["id"]] = {
                "label_id": label["label_id"],
                "expected_ref": blobs.put(json_bytes(expected)),
            }
    return blobs.put(
        json_bytes(
            {
                "schema_version": CONTRACT,
                "source_id": source_id,
                "catalog_ref": freeze_cases(blobs, source_id, selected),
                "labels": private_labels,
                "startup_ids": sorted(startup_ids),
            }
        )
    )


def load_acceptance(blobs, reference, source_id):
    body = blobs.read(reference)
    if len(body) > 64 * 1024:
        raise ValueError("Regression manifest exceeds byte limit")
    plan = json.loads(body)
    if (
        set(plan) != {"schema_version", "source_id", "catalog_ref", "labels", "startup_ids"}
        or plan["schema_version"] != CONTRACT
        or plan["source_id"] != source_id
        or not isinstance(plan["labels"], dict)
        or not isinstance(plan["startup_ids"], list)
    ):
        raise ValueError("Invalid private regression manifest")
    catalog = load_catalog(blobs, plan["catalog_ref"], source_id)
    ids = {c["case_id"] for c in catalog["cases"]}
    if (
        len(ids) > MAX_CASES
        or ids.intersection(plan["startup_ids"])
        or not plan["labels"].keys() <= ids
    ):
        raise ValueError("Submission regressions must be bounded and separate from startup samples")
    samples = [read_case(blobs, case, source_id) for case in catalog["cases"]]
    expected = {}
    for case_id, label in plan["labels"].items():
        if not isinstance(label, dict) or set(label) != {"label_id", "expected_ref"}:
            raise ValueError("Invalid host regression label reference")
        body = blobs.read(label["expected_ref"])
        if len(body) > MAX_EXPECTATION_BYTES:
            raise ValueError("Semantic assertion exceeds byte limit")
        expected[case_id] = validate_expected(json.loads(body))
    return plan, samples, expected


async def validate_submission(blobs, reference, source_id, author, candidate, now_iso, bindings):
    plan, samples, expected = load_acceptance(blobs, reference, source_id)
    model_calls = getattr(author.models, "calls", [])
    start = len(model_calls)
    attempt = await author.validate_candidate(
        candidate,
        samples,
        now_iso,
        1,
        validation_bindings=bindings,
        expectations=expected,
    )
    checks = [
        {
            **s,
            "split": "regression",
            "label_id": plan["labels"].get(s["case_id"], {}).get("label_id"),
        }
        for s in attempt["samples"]
    ]
    formats = sorted(
        {
            d.get("format", "unknown")
            for b in samples
            for d in [b["page"], *b.get("attachments", [])]
        }
    )
    usage = [c.get("usage", {}).get("total_tokens") for c in model_calls[start:]]
    return {
        "manifest_ref": reference,
        "checker_key": checker_key(),
        "status": "failed"
        if attempt["errors"]
        else "passed"
        if samples
        else "no_independent_cases",
        "errors": attempt["errors"],
        "checks": checks,
        "failures": attempt["failures"],
        "selected_cases": len(samples),
        "execution_cases": len(attempt["samples"]) + len(attempt["failures"]),
        "labeled_cases": len(expected),
        "semantic_cases": sum(
            s.get("semantic", {}).get("status") in {"passed", "failed"} for s in checks
        ),
        "semantic_passed": sum(s.get("semantic", {}).get("status") == "passed" for s in checks),
        "model_calls": len(model_calls) - start,
        "reported_tokens": sum(usage) if all(type(n) is int for n in usage) else None,
        "monetary_cost": None,  # No pricing estimate is fabricated from partial usage.
        "formats": formats,
        "templates": sorted(
            {str((b.get("assessment") or {}).get("template_key") or "unknown") for b in samples}
        ),
        "interpretation": "Regression assertions only; unlabeled replay is not semantic accuracy. "
        "Source holdouts are not exported.",
    }
