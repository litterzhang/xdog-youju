"""Three focused groups; synthetic local training, no provider/network or real corpus."""

import copy
import hashlib
import json

import pytest

from youju.onboarding.local_classifier import LocalPageModel

BASES = {
    "article:recruitment": "公开招聘管理助理报名材料学历本科招聘岗位人数资格条件报名截止。",
    "article:unrelated": "学校历史校园建筑图书馆文化校史展览参观交通路线校园风景介绍。",
    "notice_list:recruitment": "招聘公告列表下一页上一页发布日期通知索引招聘启事分页栏目。",
}


def records(classes=2):
    training, calibration = [], []
    for label, text in list(BASES.items())[:classes]:
        for split, count, target in (("train", 12, training), ("cal", 6, calibration)):
            for number in range(count):
                body = f"{text} {'训练编号' if split == 'train' else '校准独有🛰🛸'}{number}"
                target.append(
                    {
                        "id": f"{split}:{label}:{number}",
                        "text": body,
                        "label": label,
                        "group": hashlib.sha256(body.encode()).hexdigest(),
                        "origin": "synthetic-fixtures-only",
                    }
                )
    return training, calibration


def fingerprint(payload):
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_local_fit_json_roundtrip_binary_multiclass_and_unseen_abstention(tmp_path):
    for classes in (2, 3):
        training, calibration = records(classes)
        model = LocalPageModel.fit(training, calibration)
        path = tmp_path / f"model-{classes}.json"
        model.save(path)
        saved = json.loads(path.read_text())
        assert "🛰🛸" not in saved["model"]["vocabulary"]  # Calibration cannot fit features.
        assert len(saved["model"]["vocabulary"]) <= 20_000
        assert saved["model"]["vectorizer"]["ngram_range"] == [2, 4]
        assert len(saved["model"]["coef"]) == (1 if classes == 2 else classes)
        loaded = LocalPageModel.load(path)
        assert loaded.key == model.key == saved["sha256"]
        assert loaded.metadata == model.metadata
        for record in calibration:
            predicted = model.predict(record["text"])
            assert predicted is not None and predicted["label"] == record["label"]
            assert predicted == loaded.predict(record["text"])
            assert set(predicted) == {"label", "confidence", "margin"}
            assert 0 < predicted["margin"] <= predicted["confidence"] <= 1
        for text in ("", " ", "🧿🪬🛸", "a" * 32_001, None):
            assert model.predict(text) is None and loaded.predict(text) is None
        metadata = model.metadata
        assert metadata["training"]["records"] == len(training)
        assert metadata["calibration"]["effective_records"] == len(calibration)
        assert metadata["score_kind"] == "uncalibrated_logistic_predict_proba"
        assert "synthetic-fixtures-only" not in json.dumps(metadata)
        assert training[0]["text"] not in json.dumps(metadata, ensure_ascii=False)
        assert all(
            report["accepted_groups"] >= 5 and report["observed_precision"] == 1
            for report in metadata["class_gates"].values()
        )
        metadata["min_precision"] = 0
        assert model.metadata["min_precision"] == 0.98


def test_disjoint_groups_content_ids_and_insufficient_or_wrong_calibration():
    training, calibration = records()
    for field in ("group", "text", "id"):
        leaking = copy.deepcopy(calibration)
        leaking[0][field] = training[0][field]
        with pytest.raises(ValueError, match="leakage"):
            LocalPageModel.fit(training, leaking)
    wrong = copy.deepcopy(calibration)
    labels = list(BASES)[:2]
    for record in wrong:
        record["label"] = labels[1] if record["label"] == labels[0] else labels[0]
    for held_out in ([], calibration[:2], wrong):
        model = LocalPageModel.fit(training, held_out)
        assert all(
            report["accepted_groups"] == 0 for report in model.metadata["class_gates"].values()
        )
        assert model.predict(training[0]["text"]) is None
    # Duplicating one held-out page must not manufacture five independent observations.
    copies = [{**calibration[0], "id": f"copy-{index}"} for index in range(8)]
    deduplicated = LocalPageModel.fit(training, copies)
    assert deduplicated.metadata["calibration"]["records"] == 8
    assert deduplicated.metadata["calibration"]["effective_records"] == 1
    assert deduplicated.predict(calibration[0]["text"]) is None
    with pytest.raises(ValueError, match="requirements"):
        LocalPageModel.fit(training, calibration, min_support=True)
    with pytest.raises(ValueError, match="classes"):
        LocalPageModel.fit(training[:3], [])


def test_corrupt_json_artifacts_fail_closed_even_with_recomputed_digest(tmp_path):
    training, calibration = records()
    model = LocalPageModel.fit(training, calibration)
    path = tmp_path / "local.json"
    model.save(path)
    original = json.loads(path.read_text())
    mutations = []
    wrong_dimensions = copy.deepcopy(original["model"])
    wrong_dimensions["coef"][0].pop()
    mutations.append(wrong_dimensions)
    wrong_vocabulary = copy.deepcopy(original["model"])
    wrong_vocabulary["vocabulary"][next(iter(wrong_vocabulary["vocabulary"]))] = -1
    mutations.append(wrong_vocabulary)
    unsupported_gate = copy.deepcopy(original["model"])
    unsupported_gate["metadata"]["class_gates"][unsupported_gate["classes"][0]][
        "accepted_groups"
    ] = 0
    mutations.append(unsupported_gate)
    nonnumeric = copy.deepcopy(original["model"])
    nonnumeric["idf"][0] = "NaN"
    mutations.append(nonnumeric)
    unknown_capability = copy.deepcopy(original["model"])
    unknown_capability["execute"] = "never execute artifact strings"
    mutations.append(unknown_capability)
    for payload in mutations:
        path.write_text(json.dumps({"sha256": fingerprint(payload), "model": payload}))
        with pytest.raises(ValueError):
            LocalPageModel.load(path)
    nonfinite = copy.deepcopy(original)
    nonfinite["model"]["coef"][0][0] = float("nan")
    path.write_text(json.dumps(nonfinite))
    with pytest.raises(ValueError):
        LocalPageModel.load(path)
    path.write_text('{"sha256":"first","sha256":"second","model":{}}')
    with pytest.raises(ValueError, match="Duplicate"):
        LocalPageModel.load(path)
    path.write_text('{"sha256":"' + "0" * 64 + '","model":{}}')
    with pytest.raises(ValueError, match="digest"):
        LocalPageModel.load(path)
