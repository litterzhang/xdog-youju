"""Deterministic, capped source-authoring sample selection; no resource fetching."""

import re

from .auditing import material_warnings
from .classification_policy import notice_candidate
from .toolkit import ALIASES, FORMATS, documents


def _readable_document(document):
    return any(
        isinstance(block.get("text"), str) and any(char.isalnum() for char in block["text"])
        for block in document.get("blocks", [])
    ) or any(
        isinstance(cell, (str, int, float)) and str(cell).strip()
        for table in document.get("tables", [])
        for row in table.get("rows", [])
        if isinstance(row, list)
        for cell in row
    )


def partial_sample_warnings(bundle):
    """Readable incomplete examples remain useful; warnings never erase facts.

    This is not a semantic/capture acceptance gate or an accuracy claim. Missing,
    failed and truncated metadata are retained independently of readable content.
    """
    if not any(_readable_document(doc) for doc in documents(bundle)):
        return None
    warnings = material_warnings(bundle)
    return warnings if warnings else None


def _structures(bundle):
    """Distinguish literal position tables from prose; layout tables are not job tables."""
    has_positions = any(
        any(ALIASES.get(re.sub(r"\s+", "", str(cell))) == "title" for cell in rows[0])
        for doc in documents(bundle)
        for table in doc.get("tables", [])
        if (rows := table.get("rows"))
    )
    return {"position_table" if has_positions else "prose"}


def _features(bundle, *, readable_only=False):
    docs = documents(bundle)
    formats = {
        doc.get("format") if doc.get("format") in FORMATS else "unknown"
        for doc in docs
        if not readable_only or _readable_document(doc)
    }
    assessment = bundle.get("assessment") or {}
    template = assessment.get("template_key") or "unknown"
    templates = {template} if isinstance(template, str) else {"unknown"}
    if readable_only and not any(_readable_document(doc) for doc in docs):
        templates = set()
    return formats, templates


def select_samples(bundles, limit=3, *, now=None):
    """Cover prose/table structure and material formats with at most three saved bundles.

    A confident readable page may teach parsing even when attachments are missing.
    Selecting that sample never claims its missing materials or coverage are complete.
    Bundle IDs identify snapshots, not titles; inputs are not mutated.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    limit = min(limit, 3)
    candidates, seen = [], set()
    all_formats, all_templates, all_structures = set(), set(), set()
    for bundle in bundles:
        formats, templates = _features(bundle)
        all_formats.update(formats)
        all_templates.update(templates)
        all_structures.update(_structures(bundle))
        identity = bundle.get("id") or bundle.get("resource_id") or bundle.get("url")
        if identity is not None and identity in seen:
            continue
        if identity is not None:
            seen.add(identity)
        if bundle.get("assessment", {}).get("requires_review"):
            continue  # Unresolved intent is not a bootstrap example; no samples are gold.
        assessment = bundle.get("assessment", {})
        complete = bundle.get("status") == "complete" and all(
            doc.get("status") == "ok" and not doc.get("errors") and not doc.get("truncated")
            for doc in documents(bundle)
        )
        partial = (
            assessment.get("requires_review") is False
            and assessment.get("business_type") == "recruitment"
            and notice_candidate(assessment)
            and partial_sample_warnings(bundle) is not None
        )
        if (complete or partial) and any(_readable_document(doc) for doc in documents(bundle)):
            candidates.append(bundle)
    covered_formats, covered_templates, covered_structures, samples = set(), set(), set(), []
    partial_prose_selected = False
    covered_routes = set()

    def parser_kind(bundle):
        if now is None:
            return "unassessed"
        from youju.notices import decide_route

        category = decide_route(bundle, bundle.get("assessment"), now=now).category
        return "undated" if category.startswith("undated_") else "general"

    while candidates and len(samples) < limit:

        def score(bundle, partial_prose_selected=partial_prose_selected):
            formats, templates = _features(bundle, readable_only=True)
            structures = _structures(bundle)
            return (
                not partial_prose_selected
                and bundle.get("status") == "partial"
                and "prose" in structures,
                parser_kind(bundle) not in covered_routes,
                len(structures - covered_structures),
                len(formats - covered_formats) + len(templates - covered_templates),
                bundle.get("status") == "complete",
            )

        selected = max(range(len(candidates)), key=lambda number: score(candidates[number]))
        bundle = candidates.pop(selected)
        formats, templates = _features(bundle, readable_only=True)
        covered_formats.update(formats)
        covered_templates.update(templates)
        structures = _structures(bundle)
        covered_structures.update(structures)
        partial_prose_selected |= bundle.get("status") == "partial" and "prose" in structures
        samples.append(bundle)
        covered_routes.add(parser_kind(bundle))
    return {
        "samples": samples,
        "covered_parser_kinds": sorted(covered_routes),
        "covered_formats": sorted(covered_formats),
        "covered_templates": sorted(covered_templates),
        "uncovered_formats": sorted(all_formats - covered_formats),
        "uncovered_templates": sorted(all_templates - covered_templates),
        "covered_structures": sorted(covered_structures),
        "uncovered_structures": sorted(all_structures - covered_structures),
        "incomplete_samples": [
            {"bundle_id": sample.get("id"), "warnings": material_warnings(sample)}
            for sample in samples
            if sample.get("status") != "complete"
        ],
    }
