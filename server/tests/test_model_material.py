"""Pure model-view checks: no model, network, filesystem assets or token estimates."""

import copy
import json

import pytest

from youju.onboarding.model_material import (
    ModelEvidenceError,
    compact_bundle,
    restore_extraction,
)


def material(key, rows, *, format="html"):
    blocks, cell_refs = [], []
    for row_number, row in enumerate(rows):
        refs = []
        for column, text in enumerate(row):
            reference = f"{key}:{'a' * 64}:row-{row_number}:column-{column}"
            refs.append(reference)
            blocks.append(
                {
                    "id": reference,
                    "text": text,
                    "locator": "/html/body/main/section/div/table/tbody" * 20,
                    "navigation": False,
                }
            )
        cell_refs.append(refs)
    blocks.append({"id": key + ":prose", "text": "  正文全文\\n" * 1500, "locator": "main/p"})
    return {
        "format": format,
        "url": f"https://example.org/{key}",
        "resource_id": key,
        "version_id": key + "-version",
        "sha256": "a" * 64,
        "status": "ok",
        "errors": [],
        "truncated": False,
        "title": "示例招聘公告",
        "blocks": blocks,
        "tables": [
            {
                "id": key + ":original-table-id",
                "locator": "section/div/" * 100,
                "rows": rows,
                "cell_evidence": cell_refs,
            }
        ],
        "links": [],
    }


def fixture_bundle():
    return {
        "id": "bundle-content-digest",
        "source_id": "source",
        "resource_id": "notice",
        "url": "https://example.org/notice",
        "status": "partial",
        "errors": [{"code": "MATERIAL_PARTIAL", "message": "保留原始错误及状态"}],
        "captured_at": "2026-10-11T00:00:00+00:00",
        "assessment": {"reason": "Classification metadata " * 200, "evidence_ids": []},
        "page": material("page", [["岗位名称", "人数"], ["  管理助理\n（派遣）　", "２"]]),
        "attachments": [
            material("excel", [["专业条件", "备注"], ["计算机\t科学", ""]], format="xlsx"),
            {
                "format": "pdf",
                "status": "failed",
                "errors": [{"code": "PDF_LOCKED", "message": "加密"}],
                "truncated": True,
                "url": "https://example.org/locked.pdf",
                "resource_id": "pdf",
                "blocks": [],
                "tables": [],
            },
        ],
    }


def validity(bundle):
    return {
        "status": "unknown",
        "deadline": None,
        "evidence_ids": [bundle["attachments"][0]["blocks"][0]["id"]],
        "reason": "保留字面值 e1；有效期未知",
    }


def test_compaction_preserves_every_literal_material_and_roundtrips_evidence():
    original = fixture_bundle()
    period = validity(original)
    before, period_before = copy.deepcopy(original), copy.deepcopy(period)
    compact, compact_period, reverse = compact_bundle(original, period)
    assert original == before and period == period_before
    assert "assessment" not in compact
    original_docs = [original["page"], *original["attachments"]]
    compact_docs = [compact["page"], *compact["attachments"]]
    expected = copy.deepcopy(original)
    expected.pop("assessment")
    # Restoring only IDs makes the model view identical to the host bundle except
    # for the two explicitly removable metadata locations, not just similar text.
    roundtrip = copy.deepcopy(compact)
    for expected_doc, restored_doc in zip(
        [expected["page"], *expected["attachments"]],
        [roundtrip["page"], *roundtrip["attachments"]],
        strict=True,
    ):
        for block in expected_doc["blocks"]:
            block.pop("locator")
        for table in expected_doc["tables"]:
            table.pop("locator")
        for block in restored_doc["blocks"]:
            block["id"] = reverse[block["id"]]
        for table in restored_doc["tables"]:
            table["cell_evidence"] = [
                [reverse[key] for key in row] for row in table["cell_evidence"]
            ]
    assert roundtrip == expected
    assert len(reverse) == sum(len(doc["blocks"]) for doc in original_docs)
    assert len(set(reverse.values())) == len(reverse)
    for source, view in zip(original_docs, compact_docs, strict=True):
        assert [block["text"] for block in view["blocks"]] == [
            block["text"] for block in source["blocks"]
        ]
        assert [table["id"] for table in view["tables"]] == [
            table["id"] for table in source["tables"]
        ]
        assert [table["rows"] for table in view["tables"]] == [
            table["rows"] for table in source["tables"]
        ]
    assert {
        **compact_period,
        "evidence_ids": [reverse[key] for key in compact_period["evidence_ids"]],
    } == period

    model_result = {
        "status": "partial",
        "positions": [
            {
                "title": "e1",
                "requirements_text": period["evidence_ids"][0],
                "evidence": {"title": list(reverse), "requirements_text": []},
            }
        ],
        "errors": [{"code": "REVIEW", "message": "Literal e1 must stay e1."}],
    }
    model_before = copy.deepcopy(model_result)
    restored = restore_extraction(model_result, reverse)
    assert restored["positions"][0]["evidence"]["title"] == list(reverse.values())
    assert restored["positions"][0]["title"] == "e1"
    assert restored["positions"][0]["requirements_text"] == period["evidence_ids"][0]
    assert restored["errors"] == model_result["errors"]
    assert model_result == model_before
    restored["errors"][0]["message"] = "changed only in returned copy"
    assert model_result == model_before
    compact["page"]["tables"][0]["rows"][1][0] = "changed only in model copy"
    assert original == before


def test_unknown_dangling_and_ambiguous_references_fail_closed():
    original = fixture_bundle()
    period = validity(original)
    _, _, reverse = compact_bundle(original, period)
    for reference in ("fabricated", "e999999", next(iter(reverse.values())), "", None):
        result = {
            "status": "success",
            "positions": [{"title": "岗位", "evidence": {"title": [reference]}}],
            "errors": [],
        }
        before = copy.deepcopy(result)
        with pytest.raises(ModelEvidenceError):
            restore_extraction(result, reverse)
        assert result == before
    for defect in ("validity", "cell", "duplicate"):
        source, value = copy.deepcopy(original), copy.deepcopy(period)
        if defect == "validity":
            value["evidence_ids"] = ["unavailable"]
        elif defect == "cell":
            source["page"]["tables"][0]["cell_evidence"][0][0] = "unavailable"
        else:
            source["attachments"][0]["blocks"][0]["id"] = source["page"]["blocks"][0]["id"]
        before = copy.deepcopy((source, value))
        with pytest.raises(ModelEvidenceError):
            compact_bundle(source, value)
        assert (source, value) == before


def test_original_short_ids_do_not_collide_and_bytes_shrink_without_truncation():
    source = fixture_bundle()
    # The first original ID receives e1, while an ORIGINAL e1 must get a distinct
    # short ID, including in another material's cells and in validity evidence.
    attachment = source["attachments"][0]
    attachment["blocks"][0]["id"] = "e1"
    attachment["tables"][0]["cell_evidence"][0][0] = "e1"
    source["page"]["tables"][0]["cell_evidence"][0][0] = "e1"  # Forward cross-material ref.
    attachment["tables"][0]["id"] = "e1"  # Config-facing table ID must never be remapped.
    attachment["blocks"][0]["locator"] = {"sheet": "岗位", "cell": "A1"}
    period = validity(source)
    compact, compact_period, reverse = compact_bundle(source, period)
    forward = {original: short for short, original in reverse.items()}
    assert reverse["e1"] == source["page"]["blocks"][0]["id"]
    assert forward["e1"] != "e1"
    assert compact_period["evidence_ids"] == [forward["e1"]]
    assert compact["page"]["tables"][0]["cell_evidence"][0][0] == forward["e1"]
    assert compact["attachments"][0]["tables"][0]["id"] == "e1"
    assert compact["attachments"][0]["blocks"][0]["locator"] == {"sheet": "岗位", "cell": "A1"}
    result = {
        "status": "success",
        "positions": [{"title": "岗位", "evidence": {"title": ["e1", forward["e1"]]}}],
        "errors": [],
    }
    restored = restore_extraction(result, reverse)
    assert restored["positions"][0]["evidence"]["title"] == [
        source["page"]["blocks"][0]["id"],
        "e1",
    ]
    before_bytes = len(json.dumps(source, ensure_ascii=False).encode("utf-8"))
    after_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    assert after_bytes < before_bytes  # Serialized bytes only; not a token estimate.
    assert compact["page"]["blocks"][-1]["text"] == source["page"]["blocks"][-1]["text"]
