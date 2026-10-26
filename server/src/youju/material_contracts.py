"""Shared parsed-material data only; no acquisition, database or execution authority."""

from typing import Any, NotRequired, TypedDict


class MaterialBlock(TypedDict):
    id: str
    text: str
    locator: NotRequired[str]


class MaterialDocument(TypedDict):
    blocks: list[MaterialBlock]
    date_hints: NotRequired[list[dict[str, str]]]
    tables: NotRequired[list[dict[str, Any]]]
    version_id: NotRequired[str]
    url: NotRequired[str]
    title: NotRequired[str]
    status: NotRequired[str]
    errors: NotRequired[list[dict]]


class MaterialBundle(TypedDict):
    page: MaterialDocument
    attachments: NotRequired[list[MaterialDocument]]
    source_id: NotRequired[str]
    resource_id: NotRequired[str]
    id: NotRequired[str]
    status: NotRequired[str]
    errors: NotRequired[list[dict]]


def capture_diagnostics(bundle: dict) -> list[dict]:
    """Project capture metadata only; these warnings cannot alter readable facts."""
    docs = ([bundle["page"]] if isinstance(bundle.get("page"), dict) else []) + [
        doc for doc in bundle.get("attachments", []) or [] if isinstance(doc, dict)
    ]
    result = []
    if bundle.get("status") != "complete":
        result.append(
            {"code": "INCOMPLETE_CAPTURE", "message": "Saved bundle is not declared complete"}
        )
    if bundle.get("errors"):
        result.append(
            {"code": "CAPTURE_DIAGNOSTICS", "message": "Bundle contains acquisition diagnostics"}
        )
    for index, document in enumerate(docs):
        if (
            document.get("status") not in (None, "ok")
            or document.get("truncated")
            or document.get("errors")
        ):
            result.append(
                {
                    "code": "INCOMPLETE_CAPTURE",
                    "message": "Document capture is incomplete",
                    "document": index,
                }
            )
    present = {document.get("url") for document in docs}
    if any(
        link.get("is_attachment") and link.get("url") not in present
        for link in (bundle.get("page") or {}).get("links", [])
        if isinstance(link, dict)
    ):
        result.append({"code": "MISSING_ATTACHMENT", "message": "Linked attachment not captured"})
    return result
