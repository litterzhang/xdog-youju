"""Three focused, network/model-free groups for classification and business safety."""

import asyncio
import copy
import json

from youju.onboarding.classification import (
    CLASSIFICATION_SCHEMA,
    MAX_DIGEST_CHARS,
    Classifier,
    build_digest,
    validate_classification,
)
from youju.onboarding.sampling import select_samples
from youju.onboarding.toolkit import (
    check_validity,
    extract_positions,
    material_issues,
    validate_result,
)


def document(key, *, title="招聘公告", texts=(), rows=None, format="html"):
    blocks = [{"id": key + ":title", "text": title, "locator": "title"}]
    blocks.extend(
        {"id": f"{key}:b{number}", "text": text, "locator": f"block {number}"}
        for number, text in enumerate(texts)
    )
    tables = []
    if rows:
        cell_ids = []
        for row_number, row in enumerate(rows):
            ids = []
            for col, text in enumerate(row):
                block_id = f"{key}:r{row_number}:c{col}"
                ids.append(block_id)
                blocks.append(
                    {"id": block_id, "text": text, "locator": f"table 0 row {row_number} col {col}"}
                )
            cell_ids.append(ids)
        tables.append(
            {"id": key + ":table", "locator": "table 0", "rows": rows, "cell_evidence": cell_ids}
        )
    return {
        "format": format,
        "status": "ok",
        "title": title,
        "blocks": blocks,
        "tables": tables,
        "links": [],
        "errors": [],
        "truncated": False,
        "url": f"https://example.org/{key}",
    }


def bundle(key="page", **kwargs):
    page = document(key, **kwargs)
    return {
        "id": key,
        "source_id": "source",
        "resource_id": key,
        "url": page["url"],
        "page": page,
        "attachments": [],
        "status": "complete",
        "errors": [],
        "captured_at": "2026-10-11T00:00:00+00:00",
    }


def unknown():
    return {
        "page_roles": ["unknown"],
        "business_type": "unknown",
        "revisit": "weekly",
        "template_key": None,
        "reason": "Ambiguous material",
        "evidence_ids": [],
        "requires_review": True,
        "method": "small",
    }


class FakeModel:
    metadata = {
        "small_model": "synthetic-small",
        "large_model": "synthetic-large",
        "api_key": "never-copy",
    }

    def __init__(self, *responses):
        self.responses = list(responses)
        self.tiers = []

    async def complete(self, task, payload, schema, *, tier):
        assert task == "classify_page" and schema is CLASSIFICATION_SCHEMA
        assert "page_digest" in payload
        self.tiers.append(tier)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_classification_rules_escalation_uncertainty_and_bounded_digest():
    notice = bundle(
        texts=["报名提交材料，截止日期见通知。"], rows=[["岗位名称", "人数"], ["管理助理", "2"]]
    )
    classified = asyncio.run(Classifier().classify(notice))
    assert classified["business_type"] == "recruitment"
    assert classified["page_roles"] == ["article"] and classified["template_key"]
    assert not validate_classification(notice, classified)
    listing = bundle("list", title="人才招聘", texts=["教师招聘公告", "助理招聘公告"])
    listing["page"]["links"] = [
        {"url": f"https://example.org/n{number}", "label": text, "is_attachment": False}
        for number, text in enumerate(["教师招聘公告", "助理招聘公告"])
    ]
    assert asyncio.run(Classifier().classify(listing))["page_roles"] == ["notice_list"]
    for title, expected in [
        ("招聘岗位取消公告", "cancellation"),
        ("招聘报名延期公告", "correction"),
        ("招聘拟聘用人员公示", "result"),
        ("招聘拟录用人员公示更正公告", "correction"),
        ("招聘拟录用人员公示取消公告", "cancellation"),
    ]:
        event_bundle = bundle(title=title)
        classified = asyncio.run(Classifier().classify(event_bundle))
        assert classified["business_type"] == expected
        assert classified["requires_review"] is (expected != "result")
        assert classified["revisit"] == ("weekly" if expected == "result" else "daily")
        assert validate_classification(event_bundle, classified) == []
    historical_result = bundle(
        title="2024年招聘拟录用人员公示",
        rows=[["岗位名称", "人数"], ["管理助理", "1"]],
    )
    model = FakeModel()
    classified = asyncio.run(Classifier(model).classify(historical_result))
    assert model.tiers == [] and classified["method"] == "rules"
    assert classified["business_type"] == "result" and not classified["requires_review"]
    assert classified["evidence_ids"] and classified["template_key"]
    assert extract_positions(historical_result)["positions"] == []
    truncated_result = copy.deepcopy(historical_result)
    truncated_result["page"]["truncated"] = True
    failed_attachment_result = copy.deepcopy(historical_result)
    failed_attachment_result["attachments"] = [document("result-pdf", format="pdf")]
    failed_attachment_result["attachments"][0]["status"] = "failed"
    failed_attachment_result["status"] = "partial"
    for incomplete in (truncated_result, failed_attachment_result):
        classified = asyncio.run(Classifier().classify(incomplete))
        assert classified["business_type"] == "result"
        assert classified["requires_review"] is (incomplete is truncated_result)
        assert material_issues(incomplete)  # Type confidence does not repair missing materials.
        assert classified["revisit"] == "weekly"
        assert validate_classification(incomplete, classified) == []
    no_title_evidence = copy.deepcopy(historical_result)
    no_title_evidence["page"]["blocks"] = [
        block for block in no_title_evidence["page"]["blocks"] if block["id"] != "page:title"
    ]
    assert asyncio.run(Classifier().classify(no_title_evidence))["requires_review"]
    # Real sites often retain one browser title across distinct body headlines.
    for heading, expected in [
        ("实验室劳务派遣岗位招聘启事", "recruitment"),
        ("招聘拟录用人员公示", "result"),
        ("招聘拟录用人员公示更正公告", "correction"),
        ("招聘岗位取消公告", "cancellation"),
    ]:
        body_headline = bundle(
            title="示例大学人才招聘网",
            texts=[heading, "报名提交材料。"],
            rows=[["岗位名称", "人数"], ["管理助理", "1"]],
        )
        classified = asyncio.run(Classifier().classify(body_headline))
        assert classified["business_type"] == expected
        assert "page:b0" in classified["evidence_ids"]
        if expected != "recruitment":
            assert extract_positions(body_headline)["positions"] == []
    guide = bundle(
        title="示例大学人才招聘网", texts=["教授岗位招聘指南", "招聘条件", "报名方式另行说明。"]
    )
    assert asyncio.run(Classifier().classify(guide))["business_type"] == "guide"
    assert asyncio.run(Classifier().classify(guide))["page_roles"] == ["article"]
    for heading in ("讲席教授岗位引进指南", "示例大学诚聘科研人员"):
        explicit_hiring = bundle(
            title="示例大学人才招聘网", texts=[heading, "引进条件", "报名方式另行说明。"]
        )
        expected = "guide" if "指南" in heading else "recruitment"
        assert asyncio.run(Classifier().classify(explicit_hiring))["business_type"] == expected
    navigation_news = bundle(title="示例大学人才招聘网", texts=["人才招聘", "学校历史沿革介绍。"])
    navigation_news["page"]["blocks"][1]["locator"] = (
        "body:nth-of-type(1) > nav:nth-of-type(1) > a:nth-of-type(1)"
    )
    navigation_news["page"]["links"] = [
        {"url": "https://example.org/jobs", "label": "人才招聘", "is_attachment": False}
    ]
    negative_content = {
        **unknown(),
        "page_roles": ["other"],
        "business_type": "unrelated",
        "revisit": "none",
        "requires_review": False,
        "evidence_ids": ["page:b1"],
        "reason": "Institution history, not recruitment content.",
    }
    model = FakeModel(negative_content)
    classified = asyncio.run(Classifier(model).classify(navigation_news))
    assert classified["business_type"] == "unrelated" and model.tiers == ["small"]
    assert validate_classification(
        navigation_news, {**negative_content, "evidence_ids": ["page:b0"]}
    )
    assert build_digest(navigation_news)["documents"][0]["blocks"][1]["navigation"]
    navigation_news["page"]["blocks"][2]["text"] = "本校公开招聘管理助理。"
    assert validate_classification(navigation_news, negative_content)
    sitemap = bundle("sitemap", title="", format="xml")
    sitemap["page"]["blocks"] = []
    sitemap["page"]["links"] = [
        {"url": "https://example.org/notice", "label": "", "is_attachment": False}
    ]
    model = FakeModel()
    navigation = asyncio.run(Classifier(model).classify(sitemap))
    assert model.tiers == [] and navigation["method"] == "rules"
    assert navigation["page_roles"] == ["navigation"] and navigation["revisit"] == "weekly"
    assert navigation["requires_review"] is False
    assert validate_classification(sitemap, navigation) == []
    sitemap["page"]["truncated"] = True
    assert asyncio.run(Classifier().classify(sitemap))["requires_review"]
    empty = bundle(title="", texts=[])
    empty["url"] = "https://example.org/recruitment/notice"
    assert asyncio.run(Classifier().classify(empty))["business_type"] == "unknown"
    notice["attachments"] = [document("missing", title="", format="pdf")]
    notice["attachments"][0]["status"] = "failed"
    notice["status"] = "partial"
    classified = asyncio.run(Classifier().classify(notice))
    assert classified["business_type"] == "recruitment"
    assert classified["page_roles"] == ["article"] and classified["template_key"]
    assert classified["requires_review"] is False
    assert classified["revisit"] != "none"
    assert material_issues(notice)  # Clear page classification does not repair a failed attachment.
    assert validate_classification(notice, classified) == []

    ambiguous = bundle(title="信息发布", texts=["本校招聘管理助理，详情见岗位信息。"])
    resolved = {
        **unknown(),
        "page_roles": ["article"],
        "business_type": "recruitment",
        "revisit": "daily",
        "evidence_ids": ["page:b0"],
        "requires_review": False,
        "reason": "Literal recruitment content",
    }
    model = FakeModel(unknown(), resolved)
    classified = asyncio.run(Classifier(model).classify(ambiguous))
    assert model.tiers == ["small", "large"] and classified["method"] == "large"
    assert classified["template_key"] and "api_key" not in classified["model_metadata"]
    assert not validate_classification(ambiguous, classified)
    model = FakeModel(resolved)
    assert asyncio.run(Classifier(model).classify(ambiguous))["method"] == "small"
    assert model.tiers == ["small"]
    model = FakeModel({**resolved, "confidence": 0.99}, RuntimeError("private provider body"))
    failed = asyncio.run(Classifier(model).classify(ambiguous))
    assert failed["requires_review"] and failed["revisit"] == "weekly"
    assert "MODEL_ERROR" in failed["reason"] and "private provider body" not in failed["reason"]
    for malformed in [None, {**resolved, "business_type": []}, {**resolved, "evidence_ids": [{}]}]:
        assert validate_classification(ambiguous, malformed)
    assert validate_classification(ambiguous, {**resolved, "evidence_ids": ["invented"]})
    large = bundle(title="资料", texts=["内容" * 5000 for _ in range(100)])
    digest = build_digest(large)
    assert digest["truncated"] and len(json.dumps(digest, ensure_ascii=False)) <= MAX_DIGEST_CHARS
    negative = {
        **resolved,
        "business_type": "unrelated",
        "page_roles": ["other"],
        "revisit": "none",
        "evidence_ids": ["page:b0"],
    }
    model = FakeModel(negative, negative)
    assert asyncio.run(Classifier(model).classify(large))["revisit"] == "weekly"


def test_validity_explicit_clock_conservative_periods_and_literal_row_validation():
    rows = [
        ["岗位名称", "招聘人数", "学历", "招聘单位"],
        ["管理助理", "2人", "硕士", "示例大学财务处"],
    ]
    notice = bundle(
        texts=[
            "报名截止时间为2026年10月31日。",
            "到岗日期2026年12月31日。",
            "年龄截至2026年1月1日不超过35周岁。",
        ],
        rows=rows,
    )
    validity = check_validity(notice, "2026-10-31T15:59:59+00:00")
    assert validity["status"] == "not_expired" and validity["deadline"] == "2026-10-31"
    assert check_validity(notice, "2026-10-31T16:00:00+00:00")["status"] == "expired"
    assert check_validity(notice, "invalid")["status"] == "unknown"
    result = extract_positions(notice)
    assert result["status"] == "success" and len(result["positions"]) == 1
    position = result["positions"][0]
    assert position["headcount"] == 2 and position["employer_text"] is None
    assert position["hiring_unit_text"] == "示例大学财务处"
    assert position["deadline_on"] == "2026-10-31"
    result["validity"] = validity
    original = copy.deepcopy(result)
    assert validate_result(notice, result) == [] and result == original
    forged = copy.deepcopy(result)
    forged["positions"][0]["title"] = "教授"
    assert any(error["code"] == "UNSUPPORTED_FIELD" for error in validate_result(notice, forged))
    forged_employer = copy.deepcopy(result)
    forged_employer["positions"][0]["employer_text"] = position["hiring_unit_text"]
    forged_employer["positions"][0]["evidence"]["employer_text"] = position["evidence"][
        "hiring_unit_text"
    ]
    assert any(
        error["code"] == "UNSUPPORTED_FIELD" for error in validate_result(notice, forged_employer)
    )
    forged = copy.deepcopy(result)
    forged["positions"][0]["evidence"]["title"] = ["fake"]
    assert any(error["code"] == "UNKNOWN_EVIDENCE" for error in validate_result(notice, forged))
    empty = {"status": "success", "positions": [], "errors": []}
    assert {error["code"] for error in validate_result(notice, empty)} >= {
        "FALSE_COMPLETE",
        "SUSPICIOUS_ZERO_POSITIONS",
        "OMITTED_POSITION_ROW",
    }
    filtered = extract_positions(notice, {"table_ids": []})
    assert filtered["status"] == "review" and any(
        error["code"] == "FILTERED_POSITION_TABLE" for error in filtered["errors"]
    )

    # A nested HTML container is not another position table. The leading
    # serial-count display is excluded only when every following row proves it.
    counted = bundle(
        rows=[
            ["序号", "招聘单位", "岗位", "专业", "招聘条件"],
            ["2", "", "", "", ""],
            ["1", "示例学院", "教授", "物理", "博士且具有教学经历"],
            ["2", "示例学院", "副教授", "物理", "博士且具有科研经历"],
        ]
    )
    page = counted["page"]
    child = page["tables"][0]
    outer = "html:nth-of-type(1) > body:nth-of-type(1) > table:nth-of-type(1)"
    child["locator"] = outer + " > tr:nth-of-type(2) > td:nth-of-type(1) > table:nth-of-type(1)"
    block_index = {block["id"]: block for block in page["blocks"]}
    for ri, cells in enumerate(child["cell_evidence"]):
        for ci, key in enumerate(cells):
            block_index[key]["locator"] = (
                child["locator"] + f" > tr:nth-of-type({ri + 1}) > td:nth-of-type({ci + 1})"
            )
    parent_texts = [
        "示例学院高级专业技术岗位招聘",
        " ".join(cell for row in child["rows"] for cell in row if cell),
        "所有应聘者需提交个人简历及申请材料。",
    ]
    parent = {
        "id": "layout",
        "locator": outer,
        "rows": [[text] for text in parent_texts],
        "cell_evidence": [[f"layout:{number}"] for number in range(3)],
    }
    page["tables"].insert(0, parent)
    page["blocks"].extend(
        {
            "id": f"layout:{number}",
            "text": text,
            "locator": outer + f" > tr:nth-of-type({number + 1}) > td:nth-of-type(1)",
        }
        for number, text in enumerate(parent_texts)
    )
    before = copy.deepcopy(counted)
    complete = extract_positions(counted)
    assert complete["status"] == "success" and len(complete["positions"]) == 2
    assert all(position["headcount"] is None for position in complete["positions"])
    assert all(position["employer_text"] is None for position in complete["positions"])
    assert validate_result(counted, complete) == [] and counted == before
    omitted = {**complete, "positions": complete["positions"][:1]}
    assert any(
        error["code"] == "OMITTED_POSITION_ROW" for error in validate_result(counted, omitted)
    )
    extra_parent = copy.deepcopy(counted)
    extra_parent["page"]["tables"][0]["rows"].append(["新增研究助理"])
    extra_parent["page"]["tables"][0]["cell_evidence"].append(["unaccounted"])
    extra_parent["page"]["blocks"].append(
        {
            "id": "unaccounted",
            "text": "新增研究助理",
            "locator": outer + " > tr:nth-of-type(4) > td:nth-of-type(1)",
        }
    )
    assert any(
        error["code"] == "UNCHECKED_ROWS" for error in validate_result(extra_parent, complete)
    )
    extra_inline = copy.deepcopy(counted)
    extra_inline["page"]["tables"][0]["rows"][1][0] += " 新增研究助理"
    next(block for block in extra_inline["page"]["blocks"] if block["id"] == "layout:1")[
        "text"
    ] += " 新增研究助理"
    assert any(
        error["code"] == "UNCHECKED_ROWS" for error in validate_result(extra_inline, complete)
    )
    bad_count = copy.deepcopy(counted)
    bad_count["page"]["tables"] = bad_count["page"]["tables"][1:]
    bad_count["page"]["tables"][0]["rows"][1][0] = "3"
    next(block for block in bad_count["page"]["blocks"] if block["id"] == "page:r1:c0")["text"] = (
        "3"
    )
    assert any(error["code"] == "UNCHECKED_ROWS" for error in validate_result(bad_count, complete))
    missing_count_evidence = copy.deepcopy(counted)
    missing_count_evidence["page"]["tables"][1]["cell_evidence"][1][0] = "unknown-counter"
    assert any(
        error["code"] == "UNCHECKED_ROWS"
        for error in validate_result(missing_count_evidence, complete)
    )

    split_deadline = bundle(
        texts=["1. 提交报名材料，截止期为 2026 年 10 月 31 日；", "最晚到岗时间为2026年12月31日。"],
        rows=[
            ["岗位名称", "专业", "学历"],
            ["管理助理 （ 1 人）", "财经类专业优先", "硕士研究生及以上"],
        ],
    )
    explicit = check_validity(split_deadline, "2026-10-11T18:00:00+08:00")
    assert explicit["status"] == "not_expired" and explicit["deadline"] == "2026-10-31"
    assert explicit["evidence_ids"] == ["page:b0"]
    extracted = extract_positions(split_deadline)
    assert extracted["positions"][0]["title"] == "管理助理 （ 1 人）"
    assert extracted["positions"][0]["headcount"] == 1
    assert extracted["positions"][0]["deadline_on"] == "2026-10-31"
    assert validate_result(split_deadline, {**extracted, "validity": explicit}) == []
    conflicting_count = bundle(rows=[["岗位名称", "人数"], ["管理助理（1人）", "2"]])
    conflicted = extract_positions(conflicting_count)
    assert conflicted["status"] == "partial" and conflicted["positions"][0]["headcount"] is None
    assert validate_result(conflicting_count, {**conflicted, "status": "success", "errors": []})
    missing_material = copy.deepcopy(split_deadline)
    missing_material["page"]["links"] = [
        {"url": "https://example.org/form.doc", "label": "报名表", "is_attachment": True}
    ]
    missing_validity = check_validity(missing_material, "2026-11-01T00:00:00+08:00")
    assert missing_validity["status"] == "expired" and missing_validity["deadline"] == "2026-10-31"
    assert missing_validity["scope"] == "available_materials"
    assert missing_validity["material_issues"] == ["missing_attachment"]
    assert "page:b0" in missing_validity["evidence_ids"]
    assert extract_positions(missing_material)["status"] == "partial"
    matrix = bundle(rows=[["专业方向", "需求人数", "最晚到岗时间"], ["物理", "1", "2026-10-31"]])
    assert extract_positions(matrix)["positions"] == []
    assert extract_positions(matrix)["status"] == "review"
    assert check_validity(matrix, "2027-01-01T00:00:00+08:00")["status"] == "unknown"
    unrelated_clock = bundle(texts=["报名后安排面试，截止时间为2026年10月31日。"])
    assert check_validity(unrelated_clock, "2027-01-01T00:00:00+08:00")["status"] == "unknown"
    for extra in [
        "长期招聘，招满为止。",
        "报名截止2026年11月1日。",
        "各岗位报名截止时间不同。",
        "报名截止日期另行通知。",
        "报名延期至2026年12月1日。",
    ]:
        changed = copy.deepcopy(notice)
        changed["page"]["blocks"].append({"id": "extra", "text": extra, "locator": "extra"})
        assert check_validity(changed, "2027-01-01T00:00:00+08:00")["status"] == "unknown"
        fake_expired = {**result, "validity": {**validity, "status": "expired"}}
        assert any(
            error["code"] == "UNSUPPORTED_VALIDITY"
            for error in validate_result(changed, fake_expired)
        )
    for text in [
        "到岗截止2026年10月31日。",
        "年龄截至2026年10月31日。",
        "报名截止10月31日。",
        "报名截止2026年2月30日。",
    ]:
        assert (
            check_validity(bundle(texts=[text]), "2027-01-01T00:00:00+08:00")["status"] == "unknown"
        )
    period = bundle(texts=["报名时间：2026年10月1日至2026年10月31日。"])
    assert check_validity(period, "2026-11-01T00:00:00+08:00")["status"] == "expired"
    clock = bundle(texts=["报名截止2026年10月31日17:00。"])
    assert check_validity(clock, "2026-10-31T17:01:00+08:00")["status"] == "unknown"
    per_job = bundle(
        rows=[["岗位名称", "报名截止日期"], ["助理", "2026-10-31"], ["研究员", "2026-11-30"]]
    )
    assert check_validity(per_job, "2027-01-01T00:00:00+08:00")["status"] == "unknown"
    assert not validate_result(per_job, extract_positions(per_job))
    broken = copy.deepcopy(notice)
    broken["page"]["tables"][0]["rows"].append(["未完整解析的岗位"])
    assert extract_positions(broken)["status"] == "partial"
    assert any(error["code"] == "UNCHECKED_ROWS" for error in validate_result(broken, result))
    broken = copy.deepcopy(notice)
    broken["page"]["truncated"] = True
    assert extract_positions(broken)["status"] == "partial"
    assert check_validity(broken, "2027-01-01T00:00:00+08:00")["status"] == "expired"
    assert extract_positions(broken)["positions"] == extract_positions(notice)["positions"]
    row_scope = bundle(
        texts=["本次不采用劳务派遣。"],
        rows=[["岗位名称", "人数", "学历"], ["助理", "2", "非本科"], ["研究员", "5", "博士"]],
    )
    valid_rows = extract_positions(row_scope)
    swapped = copy.deepcopy(valid_rows)
    swapped["positions"][0]["headcount"] = 5
    swapped["positions"][0]["evidence"]["headcount"] = ["page:r2:c1"]
    assert any(
        error["code"] == "CROSS_POSITION_EVIDENCE" for error in validate_result(row_scope, swapped)
    )
    shortened = copy.deepcopy(valid_rows)
    shortened["positions"][0]["education_text"] = "本科"
    assert any(
        error["code"] == "TRUNCATED_LITERAL_FIELD"
        for error in validate_result(row_scope, shortened)
    )
    negated = copy.deepcopy(valid_rows)
    negated["positions"][0]["employment_arrangement"] = "劳务派遣"
    negated["positions"][0]["evidence"]["employment_arrangement"] = ["page:b0"]
    assert any(
        error["code"] == "UNSUPPORTED_FIELD" for error in validate_result(row_scope, negated)
    )
    assert validate_result(row_scope, valid_rows) == []
    for value in [
        None,
        [],
        {"status": [], "positions": [{}], "errors": None},
        {"status": "success", "positions": [{"title": "助理", "evidence": []}], "errors": []},
    ]:
        assert validate_result(notice, value)


def test_sample_selection_hard_cap_template_and_attachment_coverage():
    plain = bundle("plain")
    plain["assessment"] = {"template_key": "inline"}
    duplicate = bundle("second-inline")
    duplicate["assessment"] = {"template_key": "inline"}
    multi = bundle("multi")
    multi["assessment"] = {"template_key": "attachments"}
    multi["attachments"] = [document("pdf", format="pdf"), document("sheet", format="xlsx")]
    word = bundle("word")
    word["assessment"] = {"template_key": "word-template"}
    word["attachments"] = [document("word-doc", format="docx")]
    unsupported = bundle("unsupported")
    unsupported["assessment"] = {"template_key": "inline"}
    unsupported["attachments"] = [document("old-word", format="doc")]
    unsupported["attachments"][0]["status"] = "unsupported"
    unsupported["status"] = "partial"
    inputs = [plain, duplicate, multi, word, unsupported, plain]
    before = copy.deepcopy(inputs)
    selected = select_samples(inputs, limit=10)
    assert len(selected["samples"]) == 3
    assert {item["id"] for item in selected["samples"]} == {"multi", "word", "plain"}
    assert selected["covered_formats"] == ["docx", "html", "pdf", "xlsx"]
    assert selected["uncovered_formats"] == ["doc"]
    assert selected["covered_templates"] == ["attachments", "inline", "word-template"]
    assert selected["uncovered_templates"] == []
    assert inputs == before
    assert select_samples(inputs, limit=0)["samples"] == []
    assert select_samples([], limit=3)["covered_formats"] == []
    one = select_samples(inputs, limit=1)
    assert len(one["samples"]) == 1 and one["uncovered_templates"]


def test_validity_keeps_available_deadlines_context_and_explicit_unknown_basis():
    stamp = "2026-10-12T12:00:00+08:00"
    split = bundle(texts=["六、报名时间", "即日起至2026年10月31日。"])
    v = check_validity(split, stamp)
    assert (v["status"], v["deadline"]) == ("not_expired", "2026-10-31")
    assert set(v["evidence_ids"]) == {"page:b0", "page:b1"}
    assert v["basis"] == "explicit_deadline" and v["scope"] == "complete_materials"
    assert not validate_result(
        split,
        {
            "status": "review",
            "positions": [],
            "errors": [],
            "validity": v,
        },
    )
    spaced = bundle(texts=["提交报名材料，截止期为202 6年1 0月3 1日。"])
    assert check_validity(spaced, stamp)["deadline"] == "2026-10-31"
    after = bundle(
        texts=[
            "报名截止2026年10月10日。",
            "报名结束后进行资格审查，并于报名截止后一周内通知本人。",
        ]
    )
    assert check_validity(after, stamp)["status"] == "expired"
    midnight = bundle(texts=["应聘材料投递截止2026年10月12日24:00。"])
    assert check_validity(midnight, "2026-10-12T23:59:59+08:00")["status"] == "not_expired"
    assert check_validity(midnight, "2026-10-13T00:00:00+08:00")["status"] == "expired"
    rolling = bundle(texts=["本招聘启事长期有效，应聘者可随时提交报名材料。"])
    assert check_validity(rolling, stamp)["basis"] == "rolling"
    assert check_validity(rolling, stamp)["status"] == "unknown"
    missing = copy.deepcopy(spaced)
    missing["status"] = "partial"
    missing["attachments"] = [document("missing-form", title="", format="docx")]
    missing["attachments"][0].update(status="failed", blocks=[], tables=[])
    observed = check_validity(missing, stamp)
    assert observed["status"] == "not_expired" and observed["material_issues"]
    result = {"status": "partial", "positions": [], "errors": [], "validity": observed}
    assert not validate_result(missing, result)
    forged = copy.deepcopy(result)
    forged["validity"]["scope"] = "complete_materials"
    assert any(e["code"] == "VALIDITY_CONTEXT" for e in validate_result(missing, forged))
    unrelated = bundle(texts=["报名时间", "四、年龄要求", "出生日期截至2026年10月31日。"])
    assert check_validity(unrelated, stamp)["status"] == "unknown"
