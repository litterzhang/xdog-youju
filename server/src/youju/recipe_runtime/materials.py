"""Split already-frozen adapter context from untrusted parser/model material.

Association construction/authority belongs to the source application. This helper
never imports a source DAO, infers context from legacy metadata or fetches data.
"""

from copy import deepcopy


def split_material(bundle: dict) -> tuple[dict, dict | None]:
    material = deepcopy(bundle)
    context = material.pop("fact_context", None)
    # Approximate page metadata is cost-only; do not expose it as job-date evidence.
    for doc in [material.get("page"), *(material.get("attachments") or [])]:
        if isinstance(doc, dict):
            doc.pop("date_hints", None)
    material["attachments"] = [
        doc for doc in material.get("attachments", []) if not doc.get("change_review_id")
    ]
    material.pop("period_context", None)
    return material, context


def validate_empty_capture(bundle, draft):
    """Check a source-declared table observation, never infer absence of live hiring.

    Currently supports header-only tables or a serial placeholder with all other
    cells empty on a complete primary page. Complex/missing materials stay unknown.
    """
    reference = draft.get("coverage", {}).get("empty_capture")
    if reference is None:
        return
    from youju.material_contracts import capture_diagnostics

    from .errors import RecipeError

    if capture_diagnostics(bundle) or bundle.get("attachments"):
        raise RecipeError(
            "EMPTY_CAPTURE_UNVERIFIED",
            "Empty observation needs complete acquired material",
            rejected_origin="material",
        )
    index = reference["table_index"]
    tables = bundle.get("page", {}).get("tables", [])
    valid = 0 <= index < len(tables)
    rows = tables[index].get("rows", []) if valid else []
    valid = bool(rows) and len(rows[0]) >= 3
    if valid:
        width = len(rows[0])
        valid = all(
            len(row) <= width
            and str(row[0]).strip() in {"", "0"}
            and all(not str(cell).strip() for cell in row[1:])
            for row in rows[1:]
            if row
        )
    if not valid:
        raise RecipeError(
            "DRAFT_INVALID",
            "Declared empty table contains data or is not a supported empty shape",
            rejected_result=draft,
            rejected_origin="script_draft",
        )
