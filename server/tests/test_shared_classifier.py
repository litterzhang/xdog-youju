"""Five offline synthetic groups; no real labels, network, paid calls or production writes."""

import copy
import hashlib
import json

import pytest

from youju.onboarding.classification_corpus import (
    BUSINESS_TYPES,
    PAGE_ROLES,
    assert_disjoint,
    split_corpus,
    validate_records,
)
from youju.onboarding.local_classifier import LocalPageModel
from youju.onboarding.shared_classifier import (
    SCORE_KIND,
    SharedPageModel,
    evaluate_model,
    load_runtime_model,
)

BASES = (
    ("article", "recruitment", "公开招聘管理助理报名材料学历本科招聘岗位人数资格条件报名截止。"),
    ("navigation", "unrelated", "学校历史校园建筑图书馆文化校史展览参观交通路线校园风景介绍。"),
    ("notice_list", "guide", "报考指南问答列表下一页上一页发布日期索引分页栏目。"),
)


def corpus(classes=3):
    result = {}
    for split, count in (("training", 12), ("calibration", 6), ("test", 3)):
        rows = []
        for role, business, base in BASES[:classes]:
            for number in range(count):
                text = f"{base} {split}独立编号{number}"
                rows.append(
                    {
                        "id": f"{split}:{role}:{number}",
                        "source_id": f"{split}-source",
                        "template_family": f"{split}-template",
                        "group": hashlib.sha256(text.encode()).hexdigest(),
                        "text": text,
                        "page_role": role,
                        "business_type": business,
                        "label_quality": "synthetic",
                    }
                )
        result[split] = rows
    return result


def fitted(parts, **kwargs):
    return SharedPageModel.fit(
        parts["training"], parts["calibration"], allow_synthetic=True, **kwargs
    )


def digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_two_heads_roundtrip_and_support_metadata(tmp_path):
    for classes in (2, 3):
        parts = corpus(classes)
        model = fitted(parts)
        path = tmp_path / "shared.json"
        model.save(path)
        loaded = SharedPageModel.load(path)
        with pytest.raises(ValueError, match="Runtime shared classifier"):
            load_runtime_model(path)
        assert model.key == loaded.key
        assert model.metadata == loaded.metadata
        for row in parts["test"]:
            decision = model.predict(row["text"])
            assert decision == loaded.predict(row["text"])
            assert decision is not None
            assert (decision["page_role"], decision["business_type"]) == (
                row["page_role"],
                row["business_type"],
            )
            assert decision["score_kind"] == SCORE_KIND
            assert 0 < decision["margin"] <= decision["confidence"] <= 1
        for text in ("", None, "🧿🪬🛸", "a" * 32_001):
            assert loaded.predict(text) is None
        meta = loaded.metadata
        assert set(meta["class_supports"]["page_role"]) == set(PAGE_ROLES)
        assert set(meta["class_supports"]["business_type"]) == set(BUSINESS_TYPES)
        assert meta["class_supports"]["business_type"]["cancellation"]["training"] == 0
        assert meta["training"]["identities"]["source_id"] == ["training-source"]
        assert meta["calibration"]["identities"]["template_family"] == ["calibration-template"]
        assert "not proof" in meta["provenance_note"]
        saved = json.loads(path.read_text())
        assert saved["model"]["heads"]["business_type"]["metadata"]["min_precision"] == 0.995
        assert set(saved["model"]["heads"]["page_role"]["classes"]) <= set(PAGE_ROLES) - {"unknown"}
        head = LocalPageModel.from_payload(saved["model"]["heads"]["page_role"])
        exported = head.export_payload()
        exported["classes"][0] = "mutated"
        assert head.export_payload()["classes"][0] != "mutated"
        meta["training"]["records"] = 0
        assert loaded.metadata["training"]["records"] == 12 * classes


def test_corpus_contract_splits_and_all_leakage_dimensions():
    parts = corpus()
    all_rows = sum(parts.values(), [])
    with pytest.raises(ValueError, match="Synthetic"):
        validate_records(all_rows)
    split = split_corpus(
        all_rows,
        training_sources={"training-source"},
        calibration_sources={"calibration-source"},
        test_sources={"test-source"},
        allow_synthetic=True,
    )
    assert split == parts
    assert_disjoint(parts)
    for field in ("source_id", "template_family", "group", "text", "id"):
        leaking = copy.deepcopy(parts)
        leaking["calibration"][0][field] = leaking["training"][0][field]
        with pytest.raises(ValueError, match="leakage"):
            assert_disjoint(leaking)
        with pytest.raises(ValueError, match="leakage"):
            fitted(leaking)
    copied = {
        **parts["training"][0],
        "id": "copied",
        "group": "new-group",
        "text": "  " + parts["training"][0]["text"].upper().replace(" ", "\n") + "  ",
        "business_type": "correction",
    }
    with pytest.raises(ValueError, match="Inconsistent"):
        validate_records([parts["training"][0], copied], allow_synthetic=True)
    unlabeled = {
        **parts["test"][0],
        "page_role": None,
        "business_type": None,
        "label_quality": "unlabeled",
        "template_family": None,
    }
    assert validate_records([unlabeled], require_labels=False) == [unlabeled]
    assert_disjoint({"export": [unlabeled]})
    with pytest.raises(ValueError, match="template_family"):
        validate_records([{**parts["test"][0], "template_family": None}], allow_synthetic=True)
    with pytest.raises(ValueError, match="Unlabeled"):
        validate_records([unlabeled])
    for field, value in (
        ("source_id", ""),
        ("page_role", "binary:ustc"),
        ("text", ""),
        ("label_quality", "gold"),
        ("business_type", None),
    ):
        invalid = {**parts["test"][0], field: value}
        with pytest.raises(ValueError):
            validate_records([invalid], allow_synthetic=True)
    with pytest.raises(ValueError, match="assigned"):
        split_corpus(
            all_rows,
            training_sources={"training-source"},
            calibration_sources=set(),
            test_sources={"test-source"},
            allow_synthetic=True,
        )
    with pytest.raises(ValueError, match="Source overlap"):
        split_corpus(
            all_rows,
            training_sources={"training-source"},
            calibration_sources={"training-source"},
            test_sources=set(),
            allow_synthetic=True,
        )


def test_provenance_unknown_and_missing_support_abstain():
    parts = corpus()
    with pytest.raises(ValueError, match="Synthetic"):
        SharedPageModel.fit(parts["training"], parts["calibration"])
    silver = [{**row, "label_quality": "silver"} for row in parts["training"]]
    # Silver is accepted only as declared training data, never independent gold.
    model = SharedPageModel.fit(silver, [], min_support=5)
    assert model.metadata["training"]["label_quality_counts"] == {"silver": len(silver)}
    assert model.predict(parts["test"][0]["text"]) is None
    with pytest.raises(ValueError, match="Calibration requires human"):
        SharedPageModel.fit(silver, [{**parts["calibration"][0], "label_quality": "silver"}])
    for calibration in ([], parts["calibration"][:2]):
        no_support = SharedPageModel.fit(parts["training"], calibration, allow_synthetic=True)
        assert no_support.predict(parts["test"][0]["text"]) is None
    copies = [{**parts["calibration"][0], "id": f"copy-{i}"} for i in range(8)]
    repeated = SharedPageModel.fit(parts["training"], copies, allow_synthetic=True)
    for head in ("page_role", "business_type"):
        label = parts["calibration"][0][head]
        assert repeated.metadata["class_supports"][head][label]["calibration"] == 1
    assert repeated.predict(parts["test"][0]["text"]) is None
    unknown = {
        **parts["training"][0],
        "id": "unknown-train",
        "group": "unknown-group",
        "text": "待复核内容另存测试材料未知属性",
        "page_role": "unknown",
        "business_type": "unknown",
    }
    with_unknown = SharedPageModel.fit(
        parts["training"] + [unknown], parts["calibration"], allow_synthetic=True
    )
    assert with_unknown.metadata["class_supports"]["page_role"]["unknown"]["training"] == 0
    assert with_unknown.metadata["training"]["label_counts"]["page_role"]["unknown"] == 1
    for kwargs in (
        {"min_support": True},
        {"min_precision": 0},
        {"negative_precision": float("nan")},
    ):
        with pytest.raises(ValueError):
            fitted(parts, **kwargs)


def test_corrupt_shared_artifacts_and_binary_rejection(tmp_path):
    parts = corpus()
    model = fitted(parts)
    path = tmp_path / "shared.json"
    model.save(path)
    original = json.loads(path.read_text())
    mutations = []
    for mutate in (
        lambda p: p["heads"].pop("business_type"),
        lambda p: p["heads"]["page_role"]["classes"].__setitem__(0, "unknown"),
        lambda p: p["heads"]["business_type"]["coef"][0].pop(),
        lambda p: p["metadata"].__setitem__("score_kind", "calibrated_probability"),
        lambda p: p["metadata"]["calibration"]["label_quality_counts"].__setitem__("silver", 18),
        lambda p: p["metadata"]["training"]["identities"]["id"].pop(),
        lambda p: p["metadata"]["calibration"]["identities"].__setitem__(
            "template_family", ["training-template"]
        ),
        lambda p: p["metadata"].__setitem__("class_supports", {}),
        lambda p: p["metadata"].__setitem__("calibration_mode", "declared_human"),
        lambda p: p.__setitem__("execute", "never execute artifact strings"),
    ):
        payload = copy.deepcopy(original["model"])
        mutate(payload)
        mutations.append(payload)
    for payload in mutations:
        path.write_text(json.dumps({"sha256": digest(payload), "model": payload}))
        with pytest.raises(ValueError):
            SharedPageModel.load(path)
    local = original["model"]["heads"]["page_role"]
    path.write_text(json.dumps({"sha256": digest(local), "model": local}))
    with pytest.raises(ValueError, match="not a shared model"):
        SharedPageModel.load(path)
    for malformed in (
        '{"sha256":"one","sha256":"two","model":{}}',
        '{"sha256":"' + "0" * 64 + '","model":{}}',
        '{"model":NaN}',
    ):
        path.write_text(malformed)
        with pytest.raises(ValueError):
            SharedPageModel.load(path)
    with pytest.raises(ValueError):
        SharedPageModel.load(tmp_path)


def test_heldout_evaluation_metrics_unknown_and_erroneous_exclusion(monkeypatch):
    parts = corpus()
    model = fitted(parts)
    report = evaluate_model(model, parts["test"], allow_synthetic=True)
    copies = parts["test"] + [{**parts["test"][0], "id": "extra-capture"}]
    deduped = evaluate_model(model, copies, allow_synthetic=True)
    assert deduped["total"] == len(parts["test"])
    assert deduped["raw_test_records"] == len(copies)

    assert report["evaluation_kind"] == "synthetic_test"
    assert report["calibration_mode"] == "synthetic_test"
    assert report["promotion_qualified"] is False
    assert (
        report["coverage"] == report["accuracy_over_total"] == report["accuracy_over_accepted"] == 1
    )
    assert report["accepted"] == report["total"] == 9
    assert report["recruitment_erroneous_exclusion_rate"] == 0
    assert (
        report["per_source"]["test-source"]["heads"]["business_type"]["per_label"]["recruitment"][
            "support"
        ]
        == 3
    )
    for split in ("training", "calibration"):
        for field in ("source_id", "template_family", "group", "text", "id"):
            leaking = copy.deepcopy(parts["test"])
            leaking[0][field] = parts[split][0][field]
            with pytest.raises(ValueError, match="leakage"):
                evaluate_model(model, leaking, allow_synthetic=True)
    with pytest.raises(ValueError, match="Synthetic"):
        evaluate_model(model, parts["test"])
    with pytest.raises(ValueError, match="Test requires human"):
        evaluate_model(model, [{**row, "label_quality": "silver"} for row in parts["test"]])
    # Explicitly simulated decisions verify denominators, not classifier accuracy.
    test = [dict(row) for row in parts["test"][:3]]
    test[2].update(page_role="unknown", business_type="unknown")
    monkeypatch.setattr(
        model,
        "predict",
        lambda text: (
            {"page_role": "article", "business_type": "unrelated"}
            if text == test[0]["text"]
            else None
        ),
    )
    measured = evaluate_model(model, test, allow_synthetic=True)
    assert measured["total"] == 3 and measured["accepted"] == 1 and measured["abstentions"] == 2
    assert measured["accuracy_over_total"] == measured["accuracy_over_accepted"] == 0
    assert measured["recruitment_support"] == 2
    assert measured["recruitment_erroneous_exclusions"] == 1
    assert measured["recruitment_erroneous_exclusion_rate"] == 0.5
    business = measured["heads"]["business_type"]
    assert business["confusion"]["unknown"]["unknown"] == 1
    assert business["confusion"]["recruitment"]["unknown"] == 1
    assert business["per_label"]["unknown"]["true_positive"] == 0
    assert business["per_label"]["unknown"]["precision"] is None
    empty = evaluate_model(model, [], allow_synthetic=True)
    assert empty["coverage"] is None and empty["accuracy_over_accepted"] is None
