"""Lossless source text with reversible, model-only evidence references.

Unlike a classification digest, this view never selects, summarizes or truncates
materials. Original snapshots and their locators remain in the host's bundle.
"""

from __future__ import annotations

import copy

from .classification import compact_evidence


class ModelEvidenceError(ValueError):
    """A material or model reference cannot be mapped without ambiguity."""


def _list(value: object, where: str) -> list:
    if not isinstance(value, list):
        raise ModelEvidenceError(f"Expected an array at {where}")
    return value


def _object(value: object, where: str) -> dict:
    if not isinstance(value, dict):
        raise ModelEvidenceError(f"Expected an object at {where}")
    return value


def _reference(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelEvidenceError(f"Expected a nonempty evidence ID at {where}")
    return value


def compact_bundle(bundle: dict, validity: dict) -> tuple[dict, dict, dict[str, str]]:
    """Copy all material, shortening only evidence IDs in the model namespace.

    Table IDs are deliberately unchanged so source config.table_ids still works.
    Only block/table locator *strings* and the bundle's assessment are omitted.
    Invalid or dangling references are errors, never newly invented evidence.
    """
    compact = copy.deepcopy(_object(bundle, "bundle"))
    compact_validity = copy.deepcopy(_object(validity, "validity"))
    page = _object(compact.get("page"), "bundle.page")
    attachments = _list(compact.get("attachments", []), "bundle.attachments")
    documents = [page, *attachments]
    references = set()
    descriptor = {"documents": []}
    for index, document in enumerate(documents):
        path = f"materials[{index}]"
        document = _object(document, path)
        blocks = _list(document.get("blocks", []), path + ".blocks")
        tables = _list(document.get("tables", []), path + ".tables")
        ids = []
        for number, block in enumerate(blocks):
            block_path = f"{path}.blocks[{number}]"
            block = _object(block, block_path)
            key = _reference(block.get("id"), block_path + ".id")
            if key in references:
                raise ModelEvidenceError("Duplicate material evidence ID")
            references.add(key)
            ids.append({"id": key})
        cells = []
        for number, table in enumerate(tables):
            table_path = f"{path}.tables[{number}]"
            table = _object(table, table_path)
            rows = _list(table.get("cell_evidence", []), table_path + ".cell_evidence")
            for row in rows:
                _list(row, table_path + ".cell_evidence[]")
            cells.append({"cell_evidence": rows})
        # Give the existing helper only references; do not run build_digest, which
        # clips content. The full copied documents above remain otherwise intact.
        descriptor["documents"].append({"blocks": ids, "tables": cells})

    # Validate after collecting every material's IDs: cross-material references
    # may legitimately point forward to a later attachment.
    for document in descriptor["documents"]:
        for table in document["tables"]:
            for row in table["cell_evidence"]:
                for key in row:
                    key = _reference(key, "table.cell_evidence")
                    if key not in references:
                        raise ModelEvidenceError("Table cell references unavailable evidence")
    validity_refs = _list(compact_validity.get("evidence_ids"), "validity.evidence_ids")
    for key in validity_refs:
        key = _reference(key, "validity.evidence_ids")
        if key not in references:
            raise ModelEvidenceError("Validity references unavailable evidence")

    mapped, reverse_refs = compact_evidence(descriptor)
    forward_refs = {original: short for short, original in reverse_refs.items()}
    for document, mapped_document in zip(documents, mapped["documents"], strict=True):
        for block, mapped_block in zip(
            document.get("blocks", []), mapped_document["blocks"], strict=True
        ):
            block["id"] = mapped_block["id"]
            if isinstance(block.get("locator"), str):
                del block["locator"]
        for table, mapped_table in zip(
            document.get("tables", []), mapped_document["tables"], strict=True
        ):
            if "cell_evidence" in table:
                table["cell_evidence"] = mapped_table["cell_evidence"]
            if isinstance(table.get("locator"), str):
                del table["locator"]
    compact.pop("assessment", None)
    compact_validity["evidence_ids"] = [forward_refs[key] for key in validity_refs]

    def remap_ids(ids):
        if not isinstance(ids, list) or any(key not in forward_refs for key in ids):
            raise ModelEvidenceError("Nested rule/region references unavailable evidence")
        return [forward_refs[key] for key in ids]

    for document in documents:
        for region in document.get("structure", {}).get("regions", []):
            region["evidence_ids"] = remap_ids(region.get("evidence_ids", []))
    period = compact_validity.get("application_period")
    if isinstance(period, dict):
        period["evidence_ids"] = remap_ids(period.get("evidence_ids", []))
        for reference in period.get("material_refs", []):
            reference["evidence_id"] = remap_ids([reference["evidence_id"]])[0]
        for derived in period.get("derivation", []):
            derived["anchor"]["evidence_id"] = remap_ids([derived["anchor"]["evidence_id"]])[0]
            derived["rule_evidence_id"] = remap_ids([derived["rule_evidence_id"]])[0]
        for window in period.get("windows", []):
            window["evidence_ids"] = remap_ids(window["evidence_ids"])
            window["target_ids"] = remap_ids(window["target_ids"])
    return compact, compact_validity, reverse_refs


def restore_extraction(result: dict, reverse_refs: dict[str, str]) -> dict:
    """Restore only positions[*].evidence arrays after the caller's schema check.

    No passthrough for long original IDs: a model must cite its supplied short
    namespace. Text, errors and any other fields are never searched or replaced.
    """
    reverse_refs = _object(reverse_refs, "reverse_refs")
    for short, original in reverse_refs.items():
        _reference(short, "reverse_refs key")
        _reference(original, "reverse_refs value")
    if len(set(reverse_refs.values())) != len(reverse_refs):
        raise ModelEvidenceError("Evidence mapping is not bijective")
    restored = copy.deepcopy(_object(result, "result"))
    for number, position in enumerate(_list(restored.get("positions"), "positions")):
        path = f"positions[{number}]"
        position = _object(position, path)
        evidence = _object(position.get("evidence"), path + ".evidence")
        for field, ids in evidence.items():
            field_path = f"{path}.evidence.{field}"
            originals = []
            for key in _list(ids, field_path):
                key = _reference(key, field_path)
                if key not in reverse_refs:
                    raise ModelEvidenceError(f"Unknown model evidence reference at {field_path}")
                originals.append(reverse_refs[key])
            evidence[field] = originals
    return restored
