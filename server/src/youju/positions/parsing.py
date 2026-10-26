"""Saved-material bootstrap: recognizable tables and paired application headings.

No source routing, notice evaluation, fetching, model client or onboarding import.
Unrecognized layouts remain explicit gaps; available rows never require quotes/IDs.
"""

import re

from youju.material_contracts import capture_diagnostics

from .completeness import residual_structure_gaps, structured_signals, title_headcount
from .conditions import conditions_from_texts
from .deadlines import dates_from_text, merge_date_observations
from .drafts import ExtractionDraft
from .employment import employment_from_text
from .facts import POSITION_FIELDS, PositionFacts, TaggedField
from .normalization import normalize_extraction

_ALIASES = {
    "岗位职责": "responsibilities",
    "专业方向": "major",
    "单位": "hiring_unit",
    "岗位数": "headcount",
    "拟招聘岗位": "requirements_text",
    "岗位代码": "position_code",
    "岗位编号": "position_code",
    "职位代码": "position_code",
    "岗位名称": "title",
    "招聘岗位": "title",
    "招聘职位": "title",
    "职位名称": "title",
    "岗位": "title",
    "职位": "title",
    "岗位类别": "title",
    "人数": "headcount",
    "招聘人数": "headcount",
    "需求人数": "headcount",
    "计划人数": "headcount",
    "招聘数量": "headcount",
    "招聘单位": "hiring_unit",
    "用人单位": "hiring_unit",
    "部门": "hiring_unit",
    "单位名称": "hiring_unit",
    "招聘部门": "hiring_unit",
    "合同单位": "employer",
    "签约单位": "employer",
    "劳动合同签订单位": "employer",
    "学历": "education",
    "学历要求": "education",
    "学位": "degree",
    "学位要求": "degree",
    "学历学位": "education",
    "学历学位要求": "education",
    "专业": "major",
    "专业要求": "major",
    "招聘专业": "major",
    "所需专业": "major",
    "工作经验": "work_experience",
    "工作经历": "work_experience",
    "年龄": "age",
    "年龄要求": "age",
    "任职条件": "requirements_text",
    "岗位要求": "requirements_text",
    "招聘条件": "requirements_text",
    "应聘条件": "requirements_text",
    "任职要求": "requirements_text",
    "其他条件": "requirements_text",
    "其他要求": "requirements_text",
    "备注": "requirements_text",
    "用工方式": "employment_text",
    "聘用方式": "employment_text",
    "用工性质": "employment_text",
    "岗位性质": "employment_text",
    "编制情况": "employment_text",
    "人员性质": "employment_text",
    "工作地点": "work_location",
    "工作地址": "work_location",
    "报名截止时间": "end",
    "报名截止日期": "end",
    "截止日期": "end",
    "截止时间": "end",
    "报名时限": "dates",
    "报名期限": "dates",
    "报名时间": "dates",
    "应聘时间": "dates",
    "报名开始时间": "start",
    "报名起始时间": "start",
}
_IGNORED = {
    "序号",
    "岗位代码",
    "岗位编号",
    "职位代码",
    "联系电话",
    "联系人",
    "邮箱",
    "电子邮箱",
    "联系方式",
    "报名方式",
}


def _text(value):
    return str(value).strip() if value is not None else ""


def _header(value):
    return re.sub(r"\s+", "", _text(value))


def _documents(bundle):
    if not isinstance(bundle, dict):
        return []
    return ([bundle["page"]] if isinstance(bundle.get("page"), dict) else []) + [
        doc for doc in bundle.get("attachments", []) or [] if isinstance(doc, dict)
    ]


def _body_blocks(document):
    cells = {
        cell
        for table in document.get("tables", [])
        for row in table.get("cell_evidence", [])
        if isinstance(row, list)
        for cell in row
        if isinstance(cell, str)
    }
    return [
        block
        for block in document.get("blocks", [])
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
        and block.get("id") not in cells
        and block.get("region_kind") not in {"navigation", "recommendation"}
        and not re.search(
            r"(?:^|>\s*)(?:nav|header|footer|aside|head|title)(?=:|\s|$)", block.get("locator", "")
        )
    ]


def _count(text):
    match = re.fullmatch(r"(\d+)\s*(?:人|名)?", text)
    return int(match[1]) if match else None


def _row_draft(values, *, key):
    fields, texts, issues = {}, [], []
    dates = {}
    for name, literal in values.items():
        if not literal:
            continue
        if name in {"education", "degree", "major", "age", "work_experience", "requirements_text"}:
            texts.append((None if name == "requirements_text" else name, literal))
        if name == "headcount":
            number = _count(literal)
            fields["headcount"] = (
                TaggedField(number, "source").as_dict()
                if number is not None
                else TaggedField().as_dict()
            )
            fields["headcount_description"] = TaggedField(literal, "source").as_dict()
        elif name in {"end", "start", "dates"}:
            parsed, rule, errors = dates_from_text(literal, application_context=True)
            if name == "start" and parsed["end"] and not parsed["start"]:
                parsed = {"start": parsed["end"], "end": None}
                rule = None
            if name == "dates":
                dates.update(parsed)
            else:
                dates[name] = parsed[name]
            issues.extend(errors)
            if rule in {"until_filled", "open_ended"}:
                dates["end"] = None
            if rule:
                fields["application_rule"] = TaggedField(rule, "source").as_dict()
        elif name in POSITION_FIELDS and name not in {
            "requirements_text",
            "employment_type",
            "staffing_status",
        }:
            fields[name] = TaggedField(literal, "source").as_dict()
    requirements = "\n".join(literal for _, literal in texts)
    if values.get("responsibilities"):
        requirements += "\n岗位职责：" + values["responsibilities"]
    if requirements:
        fields["requirements_text"] = TaggedField(requirements, "source").as_dict()
    conditions, logic = conditions_from_texts(texts)
    for condition in conditions:
        if condition.type == "other" and structured_signals(condition.fact.value["text"]):
            # Qualified text still requests semantic inspection, not a generic
            # whole-job dispute just for containing 必须/须/experience words.
            if residual_structure_gaps(condition.fact.value["text"]):
                issues.append(
                    {"code": "UNRESOLVED_CONDITION", "message": condition.fact.value["text"]}
                )
    number = title_headcount(fields.get("title", {}).get("value"))
    count_text = fields.get("headcount_description", {}).get("value")
    if (
        number is not None
        and fields.get("headcount", {}).get("value") is None
        and (count_text is None or _count(count_text) == number)
    ):
        fields["headcount"] = TaggedField(number, "source").as_dict()
    employment_text = "\n".join(
        filter(None, [values.get("employment_text"), values.get("requirements_text")])
    )
    employment, errors = employment_from_text(employment_text)
    fields.update(
        {
            name: tagged.as_dict()
            for name, tagged in employment.items()
            if tagged.value is not None or any(name in error["message"] for error in errors)
        }
    )
    issues.extend(errors)
    return {
        "key": key,
        "fields": fields,
        "conditions": [c.as_dict() for c in conditions],
        "condition_logic": logic,
        "dates": dates,
    }, issues


def extract_position_draft(bundle, config=None):
    """Extract available drafts; coverage gaps describe work, never erase good rows."""
    config = {} if config is None else config
    issues, positions, observations, rules = [], [], [], []
    coverage = {
        "supported": False,
        "complete": True,
        "needs_detailed": False,
        "documents": 0,
        "tables": 0,
        "rows": 0,
    }
    common = {
        "fields": {},
        "conditions": [],
        "condition_logic": None,
        "dates": {"start": None, "end": None},
    }

    def issue(code, message, *, semantic=False, **where):
        issues.append({"code": code, "message": message, **where})
        coverage["complete"] = False
        if semantic:
            coverage["needs_detailed"] = True

    if not isinstance(config, dict):
        issue("PARSER_CONFIG", "config must be an object", semantic=True)
        return ExtractionDraft(common, [], coverage, issues).as_dict()
    header_row = config.get("header_row", 0)
    if type(header_row) is not int or header_row < 0:
        issue("PARSER_CONFIG", "header_row must be a nonnegative integer", semantic=True)
        return ExtractionDraft(common, [], coverage, issues).as_dict()
    aliases = dict(_ALIASES)
    for heading, name in (config.get("field_map") or {}).items():
        # Legacy row-name aliases are accepted only as input configuration.
        name = {
            "hiring_unit_text": "hiring_unit",
            "employer_text": "employer",
            "education_text": "education",
            "major_text": "major",
            "deadline_text": "end",
            "employment_arrangement": "employment_text",
        }.get(name, name)
        if name in set(_ALIASES.values()):
            aliases[_header(heading)] = name
    docs = _documents(bundle)
    if not docs:
        issue("MISSING_MATERIAL", "No saved documents", semantic=True)
    capture_issues = capture_diagnostics(bundle) if isinstance(bundle, dict) else []
    issues.extend(capture_issues)
    if capture_issues:
        coverage["complete"] = False
    common_employment, common_requirements, prose_temporal = [], [], []
    for doc_number, document in enumerate(docs):
        coverage["documents"] += 1
        blocks = _body_blocks(document)
        requirement_heading = None
        for number, block in enumerate(blocks):
            text = block["text"].strip()
            is_requirements = bool(
                re.search(
                    r"(?:基本|任职|应聘|招聘|资格|报考|选聘|岗位)(?:条件|要求)|任职资格", text[:80]
                )
            )
            if is_requirements and len(text) <= 80:
                requirement_heading = text
            elif re.match(r"^[一二三四五六七八九十]+[、．.]", text):
                requirement_heading = None
            elif requirement_heading:
                # Do not silently certify title/count tables while ignoring the
                # notice's qualification prose. Preserve it for detailed semantic
                # extraction; source-specific layout adapters may structure it.
                common_requirements.append((None, requirement_heading + "：" + text))
                # Semantic completion is assessed after extracting conditions;
                # generic conduct/health prose is legitimate residual text.
            # Only a short application heading can lend semantics to the next block.
            paired = False
            if (
                number
                and len(blocks[number - 1]["text"].strip()) <= 40
                and re.search(r"报名|应聘|申请", blocks[number - 1]["text"])
                and re.search(r"时间|时限|期限|截止", blocks[number - 1]["text"])
                and not re.search(r"\d\s*(?:年|月|[-/.])", blocks[number - 1]["text"])
            ):
                text = blocks[number - 1]["text"] + "\n" + text
                paired = True
            dates, rule, errors = dates_from_text(text, application_context=paired)
            delegated = bool(
                re.search(
                    r"(?:报名|应聘)(?:截止(?:日期|时间|期)?|时间|期限).{0,30}"
                    r"(?:以.{0,20}(?:附件|附表|另行|后续)|详见|参见|另行公布|另行通知)",
                    text,
                )
                and not re.search(r"(?:报名|应聘).{0,8}(?:截止|结束)(?:后|之后|以后)", text)
            )
            if any(dates.values()) or rule or delegated:
                prose_temporal.append((text, dates, rule, delegated))
            for error in errors:
                issue(error["code"], error["message"], semantic=True)
            common_employment.append(block["text"])
            requirement = re.match(
                r"^(?:通用要求|基本条件|基本要求|应聘条件|招聘条件)[：:](.+)$",
                block["text"].strip(),
                re.S,
            )
            if requirement:
                common_requirements.append((None, requirement[1]))
            elif number and re.fullmatch(
                r"(?:[一二三四五六七八九十、.（）()\d]+)?(?:通用要求|基本条件|基本要求|应聘条件|招聘条件)[：:]?",
                blocks[number - 1]["text"].strip(),
            ):
                common_requirements.append((None, block["text"]))
        for table_number, table in enumerate(document.get("tables", [])):
            coverage["tables"] += 1
            rows = table.get("rows", [])
            if header_row >= len(rows) or not isinstance(rows[header_row], list):
                continue
            headings = [_header(v) for v in rows[header_row]]
            names = [aliases.get(h) for h in headings]
            if "title" not in names:
                # Small common temporal tables can have endpoint columns or key/value rows.
                for row in rows:
                    if (
                        isinstance(row, list)
                        and len(row) == 2
                        and aliases.get(_header(row[0])) in {"start", "end", "dates"}
                    ):
                        endpoint = aliases[_header(row[0])]
                        dates, rule, errors = dates_from_text(
                            _text(row[1]), application_context=True
                        )
                        if endpoint == "start" and dates["end"] and not dates["start"]:
                            dates = {"start": dates["end"], "end": None}
                            rule = None
                        observations.append(dates)
                        if rule:
                            rules.append(rule)
                        for error in errors:
                            issue(error["code"], error["message"], semantic=True)
                if any(name in {"start", "end", "dates"} for name in names):
                    for row in rows[header_row + 1 :]:
                        if not isinstance(row, list):
                            continue
                        for column, name in enumerate(names):
                            if name not in {"start", "end", "dates"} or column >= len(row):
                                continue
                            dates, rule, errors = dates_from_text(
                                _text(row[column]), application_context=True
                            )
                            if name == "start":
                                dates = {"start": dates["start"] or dates["end"], "end": None}
                                rule = None
                            observations.append(dates)
                            if rule:
                                rules.append(rule)
                            for error in errors:
                                issue(error["code"], error["message"], semantic=True)
                if len(rows) > header_row + 1 and any(
                    re.fullmatch(r"岗位.*|职位.*|招聘人数|需求人数", heading) and len(heading) <= 20
                    for heading in headings
                ):
                    issue(
                        "UNSUPPORTED_POSITION_TABLE",
                        "No recognizable title column",
                        semantic=True,
                        document=doc_number,
                        table=table_number,
                    )
                continue
            coverage["supported"] = True
            if names.count("title") != 1:
                issue("AMBIGUOUS_TITLE_COLUMNS", "Multiple title columns", semantic=True)
                continue
            for row_number, row in enumerate(rows[header_row + 1 :], header_row + 1):
                if not isinstance(row, list) or not any(_text(v) for v in row):
                    continue
                coverage["rows"] += 1
                title_column = names.index("title")
                title = _text(row[title_column]) if title_column < len(row) else ""
                if title in {"合计", "总计", "小计", "岗位名称", "招聘岗位"}:
                    continue
                if not title:
                    issue(
                        "MISSING_POSITION_TITLE",
                        "Row without title requires review",
                        semantic=True,
                        document=doc_number,
                        table=table_number,
                        row=row_number,
                    )
                    continue
                if len(row) != len(names):
                    issue(
                        "ROW_SHAPE",
                        "Unequal row width; retained available columns",
                        semantic=True,
                        document=doc_number,
                        table=table_number,
                        row=row_number,
                    )
                values = {}
                for column, name in enumerate(names):
                    if column >= len(row) or not _text(row[column]):
                        continue
                    if name:
                        literal = _text(row[column])
                        if name in values and values[name] != literal:
                            values[name] += "\n" + literal
                        else:
                            values[name] = literal
                    elif headings[column] not in _IGNORED:
                        issue("UNSUPPORTED_POSITION_COLUMN", headings[column], semantic=True)
                position, errors = _row_draft(
                    values, key=f"d{doc_number}:t{table_number}:r{row_number}"
                )
                positions.append(position)
                for error in errors:
                    issue(
                        error["code"],
                        error["message"],
                        semantic=True,
                        path=f"positions[{len(positions) - 1}]",
                    )
                if any(
                    c["type"] == "other"
                    and structured_signals((c.get("value") or {}).get("text", ""))
                    for c in position["conditions"]
                ):
                    coverage.update(needs_detailed=True, complete=False)

    def targets_for(text):
        targets = []
        for position in positions:
            title = re.escape(position["fields"]["title"]["value"])
            if re.search(
                title + r"(?:岗位|职位|[：:]|.{0,2}(?:报名|应聘|截止))", text
            ) or re.search(r"(?:岗位|职位)[“\"]?" + title, text):
                targets.append(position)
        return targets

    scoped_observations = {}
    common_scope_unresolved = False
    for text, dates, rule, delegated in prose_temporal:
        targets = targets_for(text)
        if targets:
            for target in targets:
                scoped_observations.setdefault(target["key"], []).append(dates)
                if rule:
                    target["fields"]["application_rule"] = TaggedField(rule, "source").as_dict()
                if rule in {"open_ended", "until_filled"} or delegated:
                    target["dates"]["end"] = None
                if delegated:
                    issue(
                        "SCOPED_APPLICATION_DATE",
                        "Named position timing is delegated",
                        semantic=True,
                        path=f"positions[{positions.index(target)}].dates.end",
                    )
        elif delegated or re.search(
            r"本轮|第一轮|第二轮|首轮|第[一二三四五六七八九十\d]+批|"
            r"(?:其他|部分|个别|其余|不同).{0,4}(?:岗位|职位)|按岗位|分别报名",
            text,
        ):
            common_scope_unresolved = True
            issue(
                "SCOPED_APPLICATION_DATE",
                "Round-specific dates require a scoped adapter",
                semantic=True,
            )
        else:
            observations.append(dates)
            if rule:
                rules.append(rule)
    for target in positions:
        scoped = scoped_observations.get(target["key"])
        if not scoped:
            continue
        merged, errors = merge_date_observations([target["dates"], *scoped])
        for endpoint in ("start", "end"):
            if merged[endpoint] is not None or any(e.get("endpoint") == endpoint for e in errors):
                target["dates"][endpoint] = merged[endpoint]
        for error in errors:
            issue(error["code"], error["message"], semantic=True)
    conditions, logic = conditions_from_texts(common_requirements)
    common["conditions"] = [c.as_dict() for c in conditions]
    common["condition_logic"] = logic
    if common_requirements:
        common["fields"]["requirements_text"] = TaggedField(
            "\n".join(t for _, t in common_requirements), "source"
        ).as_dict()
    if any(c.type == "other" and structured_signals(c.fact.value["text"]) for c in conditions):
        coverage.update(needs_detailed=True, complete=False)
        if any(
            c.type == "other"
            and structured_signals(c.fact.value["text"])
            and residual_structure_gaps(c.fact.value["text"])
            for c in conditions
        ):
            issue(
                "UNRESOLVED_COMMON_CONDITION",
                "Common requirements contain unstructured typed facts",
                semantic=True,
                path="notice_facts.conditions",
            )
    dates, errors = merge_date_observations(observations)
    common["dates"] = dates
    if common_scope_unresolved:
        # Unidentified special scopes prevent common inheritance, never erase
        # independently parsed row dates or other facts.
        common["dates"]["end"] = None
    for error in errors:
        issues.append(error)
        coverage["complete"] = False
        coverage["needs_detailed"] = True
    unique_rules = set(rules)
    if len(unique_rules) == 1:
        common["fields"]["application_rule"] = TaggedField(
            next(iter(unique_rules)), "source"
        ).as_dict()
    elif len(unique_rules) > 1:
        issue("APPLICATION_RULE_CONFLICT", "Multiple declared application rules", semantic=True)
    unscoped_employment = []
    for text in common_employment:
        targets = targets_for(text)
        if not targets:
            unscoped_employment.append(text)
            continue
        employment, errors = employment_from_text(text)
        for target in targets:
            for name, value in employment.items():
                if value.value is not None:
                    previous = target["fields"].get(name)
                    if previous and previous != value.as_dict():
                        target["fields"][name] = TaggedField().as_dict()
                        issue(
                            "EMPLOYMENT_CONFLICT", "Conflicting position employment", semantic=True
                        )
                    else:
                        target["fields"][name] = value.as_dict()
        for error in errors:
            issue(error["code"], error["message"], semantic=True)
    employment, errors = employment_from_text("\n".join(unscoped_employment))
    common["fields"].update(
        {name: value.as_dict() for name, value in employment.items() if value.value is not None}
    )
    for error in errors:
        issue(error["code"], error["message"], semantic=True)
    if not coverage["supported"]:
        issue("UNSUPPORTED_POSITION_LAYOUT", "No recognizable position table", semantic=True)
    return ExtractionDraft(common, positions, coverage, issues).as_dict()


def extract_position_facts(bundle, config=None):
    return normalize_extraction(extract_position_draft(bundle, config))


def table_row_facts(row, validity=None):
    """Legacy entry name, v3 semantics only. validity is deliberately not consumed."""
    del validity
    values = {}
    for name, value in row.items():
        target = {
            "hiring_unit_text": "hiring_unit",
            "employer_text": "employer",
            "education_text": "education",
            "major_text": "major",
            "employment_arrangement": "employment_text",
            "deadline_text": "end",
        }.get(name, name)
        if target in set(_ALIASES.values()):
            values[target] = _text(value)
    # Interpret original row text, never promote a bare deadline_on scalar.
    position, issues = _row_draft(values, key="row:0")
    from .employment import EMPLOYMENT_TYPES

    arrangement = row.get("employment_arrangement")
    if arrangement in EMPLOYMENT_TYPES:
        position["fields"]["employment_type"] = TaggedField(arrangement, "source").as_dict()
    result = normalize_extraction(
        ExtractionDraft(
            positions=[position],
            coverage={"supported": True, "complete": not issues, "needs_detailed": bool(issues)},
            issues=issues,
        )
    )
    if not result["positions"]:
        raise ValueError("Row requires a usable position title")
    return PositionFacts.from_dict(result["positions"][0])
