"""Pure business helpers for already-parsed, untrusted resource bundles.

Source config accepts ``field_map`` (literal header -> field), ``table_ids``,
``document_formats``, and ``header_row`` (zero based). Filters never turn omitted
position rows into a complete result. Validity always considers the whole bundle;
config cannot override dates, suppress conflicts, or supply missing facts.
No file readers, network, database, model clients, or generated code execute here.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
FORMATS = ("html", "pdf", "xlsx", "xls", "docx", "doc", "xml", "unknown")
TEXT_FIELDS = (
    "education_text",
    "major_text",
    "requirements_text",
    "duties_text",
    "hiring_unit_text",
    "employer_text",
    "employment_arrangement",
    "deadline_text",
)
POSITION_FIELDS = ("title", "headcount", *TEXT_FIELDS, "deadline_on")
ALIASES = {
    "岗位名称": "title",
    "招聘岗位": "title",
    "岗位": "title",
    "职位名称": "title",
    "招聘人数": "headcount",
    "需求人数": "headcount",
    "人数": "headcount",
    "学历": "education_text",
    "学历要求": "education_text",
    "专业": "major_text",
    "专业要求": "major_text",
    "招聘条件": "requirements_text",
    "任职条件": "requirements_text",
    "岗位要求": "requirements_text",
    "其他条件": "requirements_text",
    "岗位职责": "duties_text",
    "工作职责": "duties_text",
    "招聘单位": "hiring_unit_text",
    "用人单位": "hiring_unit_text",
    "合同签订单位": "employer_text",
    "劳动合同主体": "employer_text",
    "用工形式": "employment_arrangement",
    "聘用方式": "employment_arrangement",
    "报名截止日期": "deadline_text",
    "报名截止时间": "deadline_text",
    "报名截止": "deadline_text",
}
DATE_RE = re.compile(
    r"(?<!\d)(\d(?:\s*\d){3})\s*[年./-]\s*(\d(?:\s*\d)?)"
    r"\s*[月./-]\s*(\d(?:\s*\d)?)(?!\d)\s*日?"
)
APPLICATION = re.compile(r"报名|应聘(?:申请)?|投递(?:简历|材料)?|申报|application", re.I)
CUTOFF = re.compile(r"截止|截至|最后期限|deadline|clos(?:e|es|ing)", re.I)
ROLLING = re.compile(
    r"长期招聘|长期有效|长期接受|常年招聘|滚动招聘|招满|聘满|额满|随到随|rolling|until filled", re.I
)
REVISION = re.compile(r"原定|原报名|延期|延长|更正|取消招聘|终止招聘|撤销招聘")
NON_APPLICATION = re.compile(r"到岗|入职|年龄|出生|周岁|毕业|资格审查|缴费|笔试|面试")
PER_JOB = re.compile(
    r"(?:各|不同|部分|个别|其他|其余).{0,4}(?:岗位|职位)|(?:岗位|职位)[A-Za-z0-9甲乙丙]"
)

VALIDITY_BASES = (
    "explicit_deadline",
    "derived_deadline",
    "amended_deadline",
    "rolling",
    "per_position",
    "conflicting",
    "unreadable",
    "event",
    "unsupported",
    "not_stated",
)
_EVIDENCE_SCHEMA = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
APPLICATION_PERIOD_KINDS = ("fixed_deadline", "open_ended", "until_filled", "not_stated", "unknown")
APPLICATION_PERIOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "text", "evidence_ids"],
    "properties": {
        "kind": {"type": "string", "enum": list(APPLICATION_PERIOD_KINDS)},
        "text": {"type": ["string", "null"], "maxLength": 8192},
        "evidence_ids": _EVIDENCE_SCHEMA,
    },
}
VALIDITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "deadline", "evidence_ids", "reason"],
    "properties": {
        "status": {"type": "string", "enum": ["not_expired", "expired", "unknown"]},
        "deadline": {"type": ["string", "null"], "format": "date"},
        "evidence_ids": _EVIDENCE_SCHEMA,
        "reason": {"type": "string", "minLength": 1},
        "basis": {"type": "string", "enum": list(VALIDITY_BASES)},
        "scope": {"type": "string", "enum": ["complete_materials", "available_materials"]},
        "material_issues": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "application_period": APPLICATION_PERIOD_SCHEMA,
        "current_hiring_status": {"type": "string", "enum": ["not_verified"]},
    },
}
POSITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "evidence"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 256},
        "headcount": {"type": ["integer", "null"], "minimum": 1},
        **{field: {"type": ["string", "null"]} for field in TEXT_FIELDS},
        "deadline_on": {"type": ["string", "null"], "format": "date"},
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "properties": {field: _EVIDENCE_SCHEMA for field in POSITION_FIELDS},
        },
    },
}
EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "positions", "errors"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["success", "partial", "review", "cancelled", "failed"],
        },
        "positions": {"type": "array", "items": POSITION_SCHEMA},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "message"],
                "properties": {"code": {"type": "string"}, "message": {"type": "string"}},
            },
        },
    },
}
RESULT_SCHEMA = {
    **EXTRACTION_SCHEMA,
    "required": [*EXTRACTION_SCHEMA["required"], "validity"],
    "properties": {**EXTRACTION_SCHEMA["properties"], "validity": VALIDITY_SCHEMA},
}


def _error(code, message, **details):
    return {"code": code, "message": message, **details}


def _text(value):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def documents(bundle):
    """Return material documents without fetching or interpreting file formats."""
    if not isinstance(bundle, dict):
        return []
    page = bundle.get("page")
    attachments = bundle.get("attachments") or []
    return ([page] if isinstance(page, dict) else []) + [
        doc for doc in attachments if isinstance(doc, dict)
    ]


def evidence_index(bundle):
    """Conflicting duplicate IDs are not valid evidence."""
    index, duplicates = {}, set()
    for doc in documents(bundle):
        for block in doc.get("blocks", []):
            if not isinstance(block, dict):
                continue
            key, text = block.get("id"), block.get("text")
            if not isinstance(key, str) or not key or not isinstance(text, str):
                continue
            if key in index and index[key] != text:
                duplicates.add(key)
            index[key] = text
    return {key: text for key, text in index.items() if key not in duplicates}


def material_issues(bundle):
    """A positive observation may survive missing material; negative certainty may not."""
    if not isinstance(bundle, dict):
        return ["missing_bundle"]
    docs = documents(bundle)
    issues = []
    if not isinstance(bundle, dict) or bundle.get("status") != "complete":
        issues.append("bundle_not_complete")
    if not docs or not isinstance(bundle.get("page"), dict):
        issues.append("missing_page")
    if bundle.get("errors"):
        issues.append("bundle_errors")
    for doc in docs:
        if doc.get("status") != "ok" or doc.get("errors") or doc.get("truncated"):
            issues.append("incomplete_material")
    if not any(_text(value) for value in evidence_index(bundle).values()):
        issues.append("missing_readable_evidence")
    attachment_urls = {
        doc.get("url") for doc in bundle.get("attachments", []) if isinstance(doc, dict)
    }
    for link in (bundle.get("page") or {}).get("links", []):
        if link.get("is_attachment") and link.get("url") not in attachment_urls:
            issues.append("missing_attachment")
    return list(dict.fromkeys(issues))


def content_blocks(document):
    """Separate explicit HTML navigation from prose, without dropping stored evidence.

    Link labels are not article prose. Unknown/layout-only containers remain content;
    we do not infer navigation from recruitment words or source-specific CSS names.
    """
    labels = {_text(link.get("label")) for link in document.get("links", [])}
    output = []
    for block in document.get("blocks", []):
        literal = _text(block.get("text"))
        locator = _text(block.get("locator"))
        if document.get("format") == "html":
            if block.get("region_kind") in {"navigation", "recommendation"}:
                continue
            if re.search(r"(?:^|>\s*)(?:nav|header|footer|aside|head|title)(?=:|\s|$)", locator):
                continue
            if literal in labels and re.search(r"(?:^|>\s*)a(?=:|\s|$)", locator):
                continue
        if literal:
            output.append(block)
    return output


def headline_candidates(bundle):
    """Return literal early body headings alongside the browser/site title.

    A source may put a short headline in ordinary paragraphs rather than h1. Only the
    first three prose blocks are considered; table cells, links and full sentences are
    not promoted to headlines. A title without a block ID cannot prove an event.
    """
    page = bundle.get("page", {})
    cells = {
        key
        for table in page.get("tables", [])
        for row in table.get("cell_evidence", [])
        for key in row
    }
    prose = [block for block in content_blocks(page) if block.get("id") not in cells]
    candidates = []
    for block in prose[:3]:
        text = _text(block.get("text"))
        if 1 < len(text) <= 160 and not re.search(r"[。；;！？!?]", text):
            candidates.append((text, [block["id"]]))
    title = _text(page.get("title"))
    if title:
        ids = [key for key, text in evidence_index(bundle).items() if title in _text(text)]
        if not any(text == title for text, _ in candidates):
            candidates.append((title, ids[:3]))
    return candidates


def _headers(table, config=None):
    config = config or {}
    row_number = config.get("header_row", 0)
    if type(row_number) is not int or row_number < 0:
        return [], 0
    rows = table.get("rows", [])
    if row_number >= len(rows) or not isinstance(rows[row_number], list):
        return [], row_number
    mapping = dict(ALIASES)
    for header, field in config.get("field_map", {}).items():
        if isinstance(header, str) and field in POSITION_FIELDS and field != "deadline_on":
            mapping[re.sub(r"\s+", "", header)] = field
    return [mapping.get(re.sub(r"\s+", "", _text(cell))) for cell in rows[row_number]], row_number


def _cell_ids(table, row, col):
    try:
        value = table["cell_evidence"][row][col]
    except (KeyError, IndexError, TypeError):
        return []
    return [value] if isinstance(value, str) and value else []


def _row_has_literal_evidence(table, row_number, index):
    """Structural exclusions require intact evidence, including empty cells."""
    row = table["rows"][row_number]
    return all(
        len(ids := _cell_ids(table, row_number, column)) == 1
        and ids[0] in index
        and _text(index[ids[0]]) == _text(value)
        for column, value in enumerate(row)
    )


def _display_count_row(table, fields, start, index):
    """Recognize only a leading serial-count display proved by all following rows.

    A lone number is otherwise an incomplete row. It is never a job headcount.
    Missing titles, evidence, columns or non-contiguous serials keep it unchecked.
    """
    rows = table.get("rows", [])
    if fields.count("title") != 1 or len(rows) <= start + 2:
        return None
    serials = [
        column for column, value in enumerate(rows[start]) if _text(value) in {"序号", "序列号"}
    ]
    if len(serials) != 1:
        return None
    serial, title = serials[0], fields.index("title")
    number = start + 1
    summary, body = rows[number], rows[number + 1 :]
    if (
        len(summary) != len(fields)
        or serial == title
        or _text(summary[serial]) != str(len(body))
        or any(_text(value) for column, value in enumerate(summary) if column != serial)
        or not _row_has_literal_evidence(table, number, index)
    ):
        return None
    for offset, row in enumerate(body, 1):
        if (
            len(row) != len(fields)
            or _text(row[serial]) != str(offset)
            or not _text(row[title])
            or _text(row[title]) in {*ALIASES, "合计", "总计"}
            or not _row_has_literal_evidence(table, number + offset, index)
        ):
            return None
    return number


def _application_material_note(text):
    """Only a material-submission note, not a second vacancy description."""
    return bool(
        re.match(
            r"^(?:所有|全部)?应聘(?:者|人员|人)(?:均)?(?:需|须|应|请)(?:要)?(?:提交|提供)", text
        )
        and re.search(r"求职信|个人简历|申请材料|报名材料", text)
        and not re.search(r"招聘|招录|招收|岗位|职位|新增|名额|\d+\s*(?:人|名)", text)
    )


def _nested_position_layout(document, table, index):
    """Identify a caption + exact child-table copy + optional application note.

    The original document is never changed. Only this duplicate container is
    excluded from tabular completeness accounting. Its child's unknown rows are
    still checked independently, and any extra unrecognized parent row prevents
    exclusion. DOM ancestry and exact evidence distinguish this from a separate
    table which merely happens to repeat a title or vocabulary.
    """
    rows, location = table.get("rows", []), _text(table.get("locator"))
    if (
        document.get("format") != "html"
        or not location
        or not rows
        or any(not isinstance(row, list) or len(row) != 1 for row in rows)
        or "title" in _headers(table)[0]
        or not re.fullmatch(r".{1,150}(?:招聘(?:公告|启事|需求)?|岗位需求)", _text(rows[0][0]))
        or any(not _row_has_literal_evidence(table, row, index) for row in range(len(rows)))
    ):
        return False
    blocks = {block.get("id"): block for block in document.get("blocks", [])}
    if any(
        not _text(blocks.get(_cell_ids(table, row, 0)[0], {}).get("locator")).startswith(
            location + " > "
        )
        for row in range(len(rows))
    ):
        return False
    children = [
        child
        for child in document.get("tables", [])
        if _text(child.get("locator")).startswith(location + " > ")
        and _headers(child)[0].count("title") == 1
        and len(child.get("rows", [])) > 1
    ]
    if not children:
        return False
    covered = set()
    for row_number, row in enumerate(rows[1:], 1):
        block = blocks.get(_cell_ids(table, row_number, 0)[0], {})
        cell_location = _text(block.get("locator"))
        if not cell_location.startswith(location + " > "):
            return False
        nested = [
            child
            for child in children
            if cell_location and _text(child.get("locator")).startswith(cell_location + " > ")
        ]
        if not nested:
            if not _text(row[0]):
                continue
            if row_number == len(rows) - 1 and _application_material_note(_text(row[0])):
                continue
            return False
        source_parts = []
        for child in nested:
            seen = set()
            for child_row, values in enumerate(child["rows"]):
                if not _row_has_literal_evidence(child, child_row, index):
                    return False
                for column, value in enumerate(values):
                    key = _cell_ids(child, child_row, column)[0]
                    child_location = _text(blocks.get(key, {}).get("locator"))
                    if not child_location.startswith(_text(child["locator"]) + " > "):
                        return False
                    if key not in seen and _text(value):
                        source_parts.append(_text(value))
                    seen.add(key)  # Rowspan aliases occur once in the original DOM.
            covered.add(id(child))
        if _text(row[0]) != " ".join(source_parts):
            return False  # Extra prose/positions inside this container remain visible.
    return len(covered) == len(children)


def _date_matches(text):
    found = []
    for match in DATE_RE.finditer(text):
        try:
            found.append((date(*(int(re.sub(r"\s", "", part)) for part in match.groups())), match))
        except ValueError:
            return [], True
    return found, False


def _deadline_at(day, clause):
    """Date-only/explicit 24:00 boundary; other exact times remain unsupported."""
    # 24:00 is exactly the next midnight, unlike an inferred office closing hour.
    clause = re.sub(r"(?<!\d)24\s*[:：]\s*00(?:\s*[:：]\s*00)?(?!\d)", "", clause)
    if re.search(
        r"\d\s*[:：时点]|上午|下午|晚上|中午|午夜|UTC|GMT|时区|"
        r"\d\s*(?:am|pm|hours?|h)\b|noon|midnight",
        clause,
        re.I,
    ):
        return None
    try:
        return datetime.combine(day + timedelta(days=1), time(), SHANGHAI)
    except OverflowError:
        return None


def _deadline_blocks(doc):
    """Pair a short application heading with its immediate date/rolling paragraph.

    Keep both original IDs. This does not merge arbitrary sections, trust URL dates,
    or rewrite the source document. Position-table cells keep their own row scope.
    """
    blocks = content_blocks(doc)
    for number, block in enumerate(blocks):
        text = _text(block.get("text"))
        context = None
        if number and not APPLICATION.search(text):
            previous = blocks[number - 1]
            heading = _text(previous.get("text"))
            if (
                len(heading) <= 40
                and APPLICATION.search(heading)
                and (CUTOFF.search(heading) or "时间" in heading)
                and not DATE_RE.search(heading)
                and len(text) <= 220
                and re.match(r"^(?:即日起|自即日|从即日|\d|招满|长期|本招聘启事|本公告)", text)
            ):
                context = previous
                text = heading + "\n" + text
        yield block, text, context


def _deadline_analysis(bundle):
    """Date evidence and conflicts, separate from attachment acquisition completeness."""
    candidates, reasons, related_ids = [], [], []
    event, event_ids = _event(bundle)
    if event:
        reasons.append("announcement_event_requires_association")
        related_ids.extend(event_ids)
    # Acquisition completeness is not evidence about a literal cutoff. With no
    # usable text there will simply be no supported date candidate. The same
    # observations feed row facts, so metadata must not erase a known row date.
    for doc in documents(bundle):
        table_cell_ids = {
            value
            for table in doc.get("tables", [])
            for row in table.get("cell_evidence", [])
            for value in row
            if isinstance(value, str)
        }
        for table in doc.get("tables", []):
            fields, start = _headers(table)
            if "deadline_text" in fields and len(table.get("rows", [])) > start + 1:
                reasons.append("per_position_deadlines")
                col = fields.index("deadline_text")
                for row in range(start, len(table["rows"])):
                    related_ids.extend(_cell_ids(table, row, col))
            elif "title" in fields:
                # Some job tables embed cutoffs in contact/requirements cells.
                # Do not promote a row deadline into a whole-notice cutoff.
                for row, values in enumerate(table.get("rows", [])[start + 1 :], start + 1):
                    for col, value in enumerate(values):
                        if APPLICATION.search(_text(value)) and CUTOFF.search(_text(value)):
                            reasons.append("per_position_deadlines")
                            related_ids.extend(_cell_ids(table, row, col))
        blocks = list(_deadline_blocks(doc))
        for number, (block, text, context) in enumerate(blocks):
            key = block.get("id")
            if ROLLING.search(text):
                reasons.append("rolling_or_open_ended_recruitment")
                related_ids.append(key)
            if (APPLICATION.search(text) and (REVISION.search(text) or PER_JOB.search(text))) or (
                CUTOFF.search(text) and re.search(r"(?:岗位|职位|岗).{0,20}(?:报名|截止)", text)
            ):
                reasons.append("revised_or_per_position_period")
                related_ids.append(key)
            if key in table_cell_ids:
                continue
            # The next block already combines this short heading and its value.
            if number + 1 < len(blocks) and blocks[number + 1][2] is block:
                continue
            for clause in re.split(r"[。；;]", text):
                if not APPLICATION.search(clause):
                    continue
                matches, invalid = _date_matches(clause)
                explicit = bool(CUTOFF.search(clause))
                if explicit and re.search(r"并非|不是|不再是|不为|误写为|误标为", clause):
                    reasons.append("negated_application_deadline")
                    related_ids.append(key)
                    continue
                period = "时间" in clause and len(matches) == 2
                from_now = bool(re.search(r"(?:即日起|自即日|从即日)\s*(?:至|到)", clause))
                if not explicit and not period and not from_now:
                    continue
                # A notification after an already-declared cutoff is not a new
                # application window and must not invalidate the actual date.
                if (
                    not matches
                    and not invalid
                    and re.search(
                        r"(?:报名|应聘).{0,6}截止\s*(?:后|以后|之后).{0,20}(?:通知|公布|告知)",
                        clause,
                    )
                ):
                    continue
                related_ids.append(key)
                if context:
                    related_ids.append(context["id"])
                if NON_APPLICATION.search(clause):
                    reasons.append("ambiguous_date_purpose")
                    continue
                if period and not explicit:
                    between = clause[matches[0][1].end() : matches[1][1].start()]
                    if not re.fullmatch(r"\s*(?:至|到|[-—~～])\s*", between):
                        reasons.append("ambiguous_application_period")
                        continue
                    if matches[0][0] > matches[1][0]:
                        reasons.append("invalid_application_period")
                        continue
                    matches = matches[1:]
                if invalid or len(matches) != 1:
                    # A standalone heading with no value is still unresolved.
                    reasons.append("missing_or_conflicting_deadline_dates")
                    continue
                day, _ = matches[0]
                end = _deadline_at(day, clause)
                if end is None:
                    reasons.append("unsupported_deadline_time")
                    continue
                candidates.append(
                    {
                        "day": day,
                        "end": end,
                        "id": key,
                        "text": text,
                        "context_ids": [context["id"]] if context else [],
                    }
                )
    if len({(item["day"], item["end"]) for item in candidates}) > 1:
        reasons.append("conflicting_deadlines")
    if not candidates:
        reasons.append("no_explicit_application_deadline")
    index = evidence_index(bundle)
    ids = list(dict.fromkeys(key for key in related_ids if key in index))
    return candidates, list(dict.fromkeys(reasons)), ids


def _validity_metadata(bundle, reasons):
    issues = material_issues(bundle)
    basis = "explicit_deadline"
    for marker, name in (
        ("page_incomplete", "unreadable"),
        ("announcement_event_requires_association", "event"),
        ("per_position_deadlines", "per_position"),
        ("revised_or_per_position_period", "conflicting"),
        ("conflicting_deadlines", "conflicting"),
        ("rolling_or_open_ended_recruitment", "rolling"),
        ("unsupported_deadline_time", "unsupported"),
        ("missing_or_conflicting_deadline_dates", "unsupported"),
        ("no_explicit_application_deadline", "not_stated"),
    ):
        if marker in reasons:
            basis = name
            break
    if reasons and basis == "explicit_deadline":
        basis = "conflicting"
    return {
        "basis": basis,
        "scope": "available_materials" if issues else "complete_materials",
        "material_issues": issues,
    }


def check_validity(bundle, now_iso, config=None):
    """Evaluate explicit date evidence independently of missing attachments.

    ``scope=available_materials`` is not a verified whole-source open/closed claim.
    Missing materials stay reported; absent date evidence, conflicts, rolling and
    row-only periods remain unknown. Config cannot supply dates or suppress uncertainty.
    """
    candidates, reasons, ids = _deadline_analysis(bundle)
    try:
        now = datetime.fromisoformat(now_iso)
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
    except (TypeError, ValueError):
        reasons.append("invalid_evaluation_time")
    metadata = _validity_metadata(bundle, reasons)
    if reasons:
        return {
            "status": "unknown",
            "deadline": None,
            "evidence_ids": ids,
            "reason": "; ".join(reasons),
            **metadata,
        }
    candidate = candidates[0]
    return {
        "status": "expired" if now >= candidate["end"] else "not_expired",
        "deadline": candidate["day"].isoformat(),
        "evidence_ids": list(
            dict.fromkeys(
                key for item in candidates for key in [*item.get("context_ids", []), item["id"]]
            )
        ),
        "reason": "Explicit application deadline; Asia/Shanghai; based on available evidence, "
        "not a guarantee that hiring is open or all amendments were obtained.",
        **metadata,
    }


_OPEN_ENDED = re.compile(
    r"(?:招聘启事|招聘公告|招聘信息|招聘通知|本公告|本启事).{0,8}长期有效"
    r"|(?:长期|常年|全年)(?:招聘|招收)|长期接受(?:报名|应聘|简历)"
    r"|rolling\s+recruitment",
    re.I,
)
_UNTIL_FILLED = re.compile(
    r"(?:招满|聘满|额满)(?:为止|即止|即结束|后截止|即停止(?:招聘|报名)?|后停止(?:招聘|报名)?|止)"
    r"|(?:直至|直到)(?:招满|聘满|额满)|until\s+filled",
    re.I,
)
_CLOSED_DECLARATION = re.compile(
    r"(?:已|已经|现已)(?:招满|聘满|额满|停止报名|停止招聘|终止招聘|取消招聘)"
    r"|(?:报名|招聘)(?:已经|已|现已)结束|现(?:停止报名|停止招聘|终止招聘|取消招聘)"
    r"|不再接受(?:报名|应聘|简历)"
    r"|(?:不再|并非|不是|不|非).{0,4}(?:长期有效|长期招聘|常年招聘|滚动招聘)",
    re.I,
)
_NON_RECRUITMENT_VALIDITY = re.compile(r"身份证|资格证|证书|证件|劳动合同|合同有效")


def application_period_hint(bundle):
    """Classify a literal notice-level application rule, NOT whether jobs are open.

    This separate hint never changes check_validity's explicit-clock expiry result.
    Ambiguous dates, unreadable/cancelled material and row-only statements stay
    unknown; publication dates and credential validity are not application rules.
    Source adapters may use this pure helper or implement their own literal parser.
    """
    candidates, reasons, _ = _deadline_analysis(bundle)
    unknown = {"kind": "unknown", "text": None, "evidence_ids": []}
    index = evidence_index(bundle)
    if candidates and not reasons:
        candidate = candidates[0]
        if candidate["id"] not in index:
            return unknown
        text = index[candidate["id"]]
        if len(text) > 8192:
            matches, invalid = _date_matches(text)
            matching = [match for day, match in matches if day == candidate["day"]]
            if invalid or not matching:
                return unknown
            match = matching[0]
            text = text[max(0, match.start() - 100) : min(len(text), match.end() + 100)]
        return {
            "kind": "fixed_deadline",
            "text": text,
            "evidence_ids": list(
                dict.fromkeys([*candidate.get("context_ids", []), candidate["id"]])
            ),
        }
    if candidates or set(reasons) - {
        "rolling_or_open_ended_recruitment",
        "no_explicit_application_deadline",
    }:
        return unknown
    open_ended, until_filled = [], []
    for doc in documents(bundle):
        cell_ids = {
            key
            for table in doc.get("tables", [])
            for row in table.get("cell_evidence", [])
            for cell in row
            for key in (cell if isinstance(cell, list) else [cell])
            if isinstance(key, str)
        }
        blocks = list(_deadline_blocks(doc))
        if any(_CLOSED_DECLARATION.search(text) for _, text, _ in blocks):
            return unknown
        for block, text, context in blocks:
            key = block.get("id")
            if key not in index or key in cell_ids or _NON_RECRUITMENT_VALIDITY.search(text):
                continue
            if _UNTIL_FILLED.search(text):
                until_filled.append(key)
            elif _OPEN_ENDED.search(text) or (
                context is not None
                and APPLICATION.search(_text(context.get("text", "")))
                and _text(text) in {"长期有效", "长期接受报名", "常年招聘", "滚动招聘"}
            ):
                open_ended.append(key)
    # A literal fill condition limits an otherwise long-term declaration.
    for kind, ids in (("until_filled", until_filled), ("open_ended", open_ended)):
        if ids:
            text = index[ids[0]]
            if len(text) > 8192:
                match = (_UNTIL_FILLED if kind == "until_filled" else _OPEN_ENDED).search(text)
                if match is None:
                    return unknown
                text = text[max(0, match.start() - 100) : min(len(text), match.end() + 100)]
            return {"kind": kind, "text": text, "evidence_ids": list(dict.fromkeys(ids))}
    return {"kind": "not_stated", "text": None, "evidence_ids": []}


def validate_application_period(bundle, period):
    """Independently check new period fields; legacy missing fields remain compatible."""
    errors = []
    if not isinstance(period, dict) or set(period) != {"kind", "text", "evidence_ids"}:
        return [_error("APPLICATION_PERIOD_SCHEMA", "Invalid application-period properties.")]
    expected = application_period_hint(bundle)
    index = evidence_index(bundle)
    ids = _check_ids(
        period["evidence_ids"], index, "validity.application_period.evidence_ids", errors
    )
    if period["kind"] not in APPLICATION_PERIOD_KINDS:
        errors.append(_error("APPLICATION_PERIOD_SCHEMA", "Invalid application-period kind."))
    elif period["kind"] != expected["kind"]:
        errors.append(
            _error(
                "APPLICATION_PERIOD_CONFLICT", "Application rule disagrees with literal evidence."
            )
        )
    if expected["kind"] in {"unknown", "not_stated"}:
        if period["text"] is not None or period["evidence_ids"] != []:
            errors.append(
                _error("APPLICATION_PERIOD_EVIDENCE", "Unresolved periods must not invent a rule.")
            )
    elif not (
        isinstance(period["text"], str)
        and 0 < len(period["text"]) <= 8192
        and _literal_supported(period["text"], ids, index)
        and set(ids) & set(expected["evidence_ids"])
    ):
        errors.append(
            _error(
                "APPLICATION_PERIOD_EVIDENCE", "Application rule requires literal source evidence."
            )
        )
    elif expected["kind"] == "fixed_deadline":
        candidates, _, _ = _deadline_analysis(bundle)
        dates, invalid = _date_matches(period["text"])
        if invalid or not any(day == candidates[0]["day"] for day, _ in dates):
            errors.append(
                _error(
                    "APPLICATION_PERIOD_EVIDENCE",
                    "Quote the recognized cutoff, not a publication date.",
                )
            )
    elif expected["kind"] == "open_ended" and not re.search(
        r"长期有效|长期招聘|常年招聘|全年招聘|长期接受|滚动招聘|rolling", period["text"], re.I
    ):
        errors.append(
            _error("APPLICATION_PERIOD_EVIDENCE", "Quote the actual long-term declaration.")
        )
    elif expected["kind"] == "until_filled" and not _UNTIL_FILLED.search(_text(period["text"])):
        errors.append(_error("APPLICATION_PERIOD_EVIDENCE", "Quote the actual fill condition."))
    return errors


def _literal_supported(value, ids, index):
    if not isinstance(value, str) or not value.strip() or not ids:
        return False
    # Multiple literal cells may be joined with newlines, never paraphrased.
    return all(
        any(_text(part) in _text(index.get(key, "")) for key in ids)
        for part in value.splitlines()
        if part.strip()
    )


def _count(text):
    match = re.fullmatch(r"\s*(\d+)\s*(?:人|名)?\s*", text)
    return int(match[1]) if match and int(match[1]) > 0 else None


def _embedded_count(text):
    match = re.fullmatch(r".+?[（(]\s*(\d+)\s*(?:人|名)\s*[）)]\s*", text)
    return int(match[1]) if match and int(match[1]) > 0 else None


def _event(bundle):
    """Recognize body or browser headlines, never a later procedural mention."""
    candidates = [
        (title, ids)
        for title, ids in headline_candidates(bundle)
        if ids and re.search(r"招聘|招录|聘用|录用|岗位|人员|应聘", title)
    ]
    for kind, pattern in (
        (
            "cancellation",
            r"(?:取消|终止|撤销|停止).{0,16}(?:招聘|岗位|招录)|(?:招聘|岗位|招录).{0,16}(?:取消|终止|撤销)",
        ),
        ("correction", r"更正|延期|延长|调整|补充公告"),
        ("result", r"拟聘|拟录用|录用公示|聘用公示|成绩公示|成绩公告|面试成绩|录取结果"),
    ):
        for title, ids in candidates:
            if re.search(pattern, title):
                return kind, ids[:3]
    return None, []


def _config_errors(config):
    if not isinstance(config, dict):
        return [_error("SOURCE_CONFIG", "Source config must be an object.")]
    invalid = []
    if "field_map" in config and (
        not isinstance(config["field_map"], dict)
        or any(
            not isinstance(header, str)
            or not header.strip()
            or field not in POSITION_FIELDS
            or field == "deadline_on"
            for header, field in config["field_map"].items()
        )
    ):
        invalid.append("field_map")
    if "header_row" in config and (
        type(config["header_row"]) is not int or config["header_row"] < 0
    ):
        invalid.append("header_row")
    for key in ("table_ids", "document_formats"):
        if key in config and (
            not isinstance(config[key], list)
            or any(not isinstance(value, str) or not value for value in config[key])
        ):
            invalid.append(key)
    return [_error("SOURCE_CONFIG", "Invalid source config field.", field=key) for key in invalid]


def extract_positions(bundle, config=None):
    """Extract literal normalized-table rows; unsupported or omitted rows stay visible."""
    config = {} if config is None else config
    errors = _config_errors(config)
    if errors:
        return {"status": "failed", "positions": [], "errors": errors}
    index, positions = evidence_index(bundle), []
    event, _ = _event(bundle)
    if event:
        return {
            "status": "review",
            "positions": [],
            "errors": [_error("EVENT_REQUIRES_REVIEW", event)],
        }
    candidates, deadline_reasons, _ = _deadline_analysis(bundle)
    global_deadline = candidates[0] if not deadline_reasons else None
    for doc in documents(bundle):
        for table in doc.get("tables", []):
            fields, start = _headers(table, config)
            if start >= len(table.get("rows", [])):
                errors.append(
                    _error(
                        "HEADER_ROW", "Configured header row is absent.", table_id=table.get("id")
                    )
                )
                continue
            if "title" not in fields:
                if _nested_position_layout(doc, table, index):
                    continue
                headers = (
                    " ".join(_text(cell) for cell in table.get("rows", [[]])[start])
                    if table.get("rows")
                    else ""
                )
                if len(table.get("rows", [])) > start + 1 and re.search(
                    r"岗位|职位|招聘人数|需求人数", headers
                ):
                    errors.append(
                        _error(
                            "UNSUPPORTED_POSITION_TABLE",
                            "No supported title column.",
                            table_id=table.get("id"),
                        )
                    )
                continue
            if ("table_ids" in config and table.get("id") not in config["table_ids"]) or (
                "document_formats" in config and doc.get("format") not in config["document_formats"]
            ):
                errors.append(
                    _error(
                        "FILTERED_POSITION_TABLE",
                        "A position table was excluded by source config.",
                        table_id=table.get("id"),
                    )
                )
                continue
            if fields.count("title") != 1:
                errors.append(
                    _error(
                        "AMBIGUOUS_TITLE_COLUMNS",
                        "Multiple title columns require a source adapter.",
                        table_id=table.get("id"),
                    )
                )
                continue
            count_row = _display_count_row(table, fields, start, index)
            for row_number, row in enumerate(table.get("rows", [])[start + 1 :], start + 1):
                if row_number == count_row:
                    continue
                context = {"table_id": table.get("id"), "row": row_number}
                if not any(_text(cell) for cell in row):
                    continue
                if len(row) != len(fields):
                    errors.append(
                        _error("ROW_SHAPE", "Row width differs from the header.", **context)
                    )
                    continue
                position = {field: None for field in POSITION_FIELDS}
                position["evidence"] = {}
                for col, field in enumerate(fields):
                    if not field:
                        continue
                    literal, ids = _text(row[col]), _cell_ids(table, row_number, col)
                    if not literal:
                        continue
                    if not _literal_supported(literal, ids, index):
                        errors.append(
                            _error(
                                "MISSING_CELL_EVIDENCE",
                                "Cell lacks matching literal evidence.",
                                field=field,
                                **context,
                            )
                        )
                        continue
                    value = _count(literal) if field == "headcount" else literal
                    if value is None:
                        continue
                    if position[field] is not None and position[field] != value:
                        if field == "headcount":
                            errors.append(
                                _error(
                                    "CONFLICTING_HEADCOUNT",
                                    "Conflicting headcount columns.",
                                    **context,
                                )
                            )
                            position[field] = None
                            continue
                        value = position[field] + "\n" + value
                    position[field] = value
                    position["evidence"].setdefault(field, []).extend(ids)
                title = position["title"]
                if not title or len(title) > 256 or title in ALIASES or title in {"合计", "总计"}:
                    errors.append(
                        _error(
                            "ROW_SKIPPED",
                            "Missing or unsupported literal position title.",
                            **context,
                        )
                    )
                    continue
                embedded = _embedded_count(title)
                if embedded is not None:
                    column_counts = {
                        _count(_text(row[col]))
                        for col, field in enumerate(fields)
                        if field == "headcount" and _count(_text(row[col])) is not None
                    }
                    if column_counts and column_counts != {embedded}:
                        position["headcount"] = None
                        errors.append(
                            _error(
                                "CONFLICTING_HEADCOUNT",
                                "Title and count columns disagree.",
                                **context,
                            )
                        )
                    else:
                        position["headcount"] = embedded
                        position["evidence"].setdefault("headcount", []).extend(
                            position["evidence"]["title"]
                        )
                if "deadline_text" in fields and position["deadline_text"]:
                    dates, invalid = _date_matches(position["deadline_text"])
                    if (
                        len(dates) == 1
                        and not invalid
                        and not ROLLING.search(position["deadline_text"])
                    ):
                        position["deadline_on"] = dates[0][0].isoformat()
                        position["evidence"]["deadline_on"] = list(
                            position["evidence"]["deadline_text"]
                        )
                elif global_deadline:
                    position["deadline_text"] = global_deadline["text"]
                    position["deadline_on"] = global_deadline["day"].isoformat()
                    for field in ("deadline_text", "deadline_on"):
                        position["evidence"][field] = [
                            *global_deadline.get("context_ids", []),
                            global_deadline["id"],
                        ]
                positions.append(position)
    for issue in material_issues(bundle):
        errors.append(_error("INCOMPLETE_MATERIAL", issue))
    if not positions:
        errors.append(
            _error("NO_POSITIONS", "No supported, evidence-backed position rows were extracted.")
        )
    return {
        "status": ("partial" if errors else "success") if positions else "review",
        "positions": positions,
        "errors": errors,
    }


def _check_ids(ids, index, path, errors):
    if (
        not isinstance(ids, list)
        or any(not isinstance(key, str) for key in ids)
        or len(set(ids)) != len(ids)
    ):
        errors.append(_error("INVALID_EVIDENCE", "Expected unique block-ID strings.", path=path))
        return []
    if any(key not in index for key in ids):
        errors.append(
            _error("UNKNOWN_EVIDENCE", "Evidence IDs do not exist in this bundle.", path=path)
        )
    return [key for key in ids if key in index]


def _supported_deadline(value, ids, bundle):
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            return False
    except (TypeError, ValueError):
        return False
    candidates, reasons, _ = _deadline_analysis(bundle)
    if not reasons and any(item["day"] == day and item["id"] in ids for item in candidates):
        return True
    # The current rule checker owns new supported syntax/scope. Legacy row facts
    # must not lose a common cutoff just because the old helper cannot parse it.
    from .periods import extract_application_period, validate_period

    rule = extract_application_period(bundle)
    if (
        not validate_period(bundle, rule)
        and rule["coverage"]["all_scopes"]
        and not rule["conflicts"]
    ):
        if any(
            w["scope"] == "notice" and w["end"][:10] == value and set(w["evidence_ids"]) <= set(ids)
            for w in rule["windows"]
        ):
            return True
    # A per-position cutoff can support a position field, not whole-notice expiry.
    for doc in documents(bundle):
        for table in doc.get("tables", []):
            fields, start = _headers(table)
            for col, field in enumerate(fields):
                if field != "deadline_text":
                    continue
                for row_number, row in enumerate(table.get("rows", [])[start + 1 :], start + 1):
                    if col >= len(row) or not set(_cell_ids(table, row_number, col)) & set(ids):
                        continue
                    dates, invalid = _date_matches(_text(row[col]))
                    if (
                        len(dates) == 1
                        and not invalid
                        and dates[0][0] == day
                        and not ROLLING.search(_text(row[col]))
                    ):
                        return True
    return False


def _employer_supported(value, ids, bundle, index):
    if not _literal_supported(value, ids, index):
        return False
    if any(re.search(r"劳动合同|合同签订单位|合同主体", index[key]) for key in ids):
        return True
    for doc in documents(bundle):
        for table in doc.get("tables", []):
            fields, start = _headers(table)
            for col, field in enumerate(fields):
                if field == "employer_text" and any(
                    set(_cell_ids(table, row, col)) & set(ids)
                    for row in range(start + 1, len(table.get("rows", [])))
                ):
                    return True
    return False


def _table_evidence_rows(bundle):
    rows, position_tables = {}, set()
    for doc_number, doc in enumerate(documents(bundle)):
        for table_number, table in enumerate(doc.get("tables", [])):
            table_key = (doc_number, table_number)
            if "title" in _headers(table)[0]:
                position_tables.add(table_key)
            for number, cells in enumerate(table.get("cell_evidence", [])[1:], 1):
                for key in cells:
                    if isinstance(key, str):
                        rows.setdefault(key, set()).add((*table_key, number))
    return rows, position_tables


def _table_field_errors(position, checked, index, table_rows, position_tables, path):
    """A real cell from another job is not evidence for this job's conditions."""
    title = _text(position.get("title"))
    anchors = {
        row
        for key in checked.get("title", [])
        if _text(index[key]) == title
        for row in table_rows.get(key, ())
    }
    relevant_tables = position_tables | {row[:2] for row in anchors}
    errors = []
    for field in POSITION_FIELDS:
        value = position.get(field)
        if value is None:
            continue
        ids = checked.get(field, [])
        cell_ids = []
        for key in ids:
            rows = {row for row in table_rows.get(key, ()) if row[:2] in relevant_tables}
            if not rows:
                continue
            cell_ids.append(key)
            if not rows & anchors:
                errors.append(
                    _error(
                        "CROSS_POSITION_EVIDENCE",
                        "A position-table cell does not belong to this literal title's row.",
                        path=path + "." + field,
                    )
                )
        if field not in {"headcount", "deadline_on"} and isinstance(value, str) and cell_ids:
            # Preserve the whole qualification cell: 本科 is not equivalent to 非本科.
            for part in value.splitlines():
                part = _text(part)
                if (
                    part
                    and any(part in _text(index[key]) for key in cell_ids)
                    and not any(part == _text(index[key]) for key in cell_ids)
                ):
                    errors.append(
                        _error(
                            "TRUNCATED_LITERAL_FIELD",
                            "A table-derived text field must preserve the complete literal cell.",
                            path=path + "." + field,
                        )
                    )
                    break
    return errors


def validate_result(bundle, result):
    """Validate JSON shapes, provenance and completeness without mutating inputs.

    Whole-notice expiry must be grounded in an unambiguous application deadline.
    The host must also compare validity to check_validity(bundle, its explicit now):
    this signature intentionally has no implicit wall clock or capture-time substitute.
    """
    errors, index, actual = [], evidence_index(bundle), set()
    table_rows, position_tables = _table_evidence_rows(bundle)
    if not isinstance(result, dict):
        return [_error("RESULT_SCHEMA", "Result must be an object.")]
    if set(result) - {"status", "positions", "errors", "validity"} or not {
        "status",
        "positions",
        "errors",
    } <= set(result):
        errors.append(_error("RESULT_SCHEMA", "Missing or unexpected result properties."))
    status = result.get("status")
    if status not in EXTRACTION_SCHEMA["properties"]["status"]["enum"]:
        errors.append(_error("RESULT_SCHEMA", "Unsupported extraction status."))
    if not isinstance(result.get("errors"), list) or any(
        not isinstance(item, dict) or not _text(item.get("code")) or not _text(item.get("message"))
        for item in result.get("errors", [])
    ):
        errors.append(_error("RESULT_SCHEMA", "Errors must contain code and message strings."))
    positions = result.get("positions")
    if not isinstance(positions, list):
        errors.append(_error("RESULT_SCHEMA", "Positions must be an array."))
        positions = []
    for number, position in enumerate(positions):
        path = f"positions[{number}]"
        if not isinstance(position, dict):
            errors.append(_error("POSITION_SCHEMA", "Position must be an object.", path=path))
            continue
        if set(position) - {*POSITION_FIELDS, "evidence"} or not {"title", "evidence"} <= set(
            position
        ):
            errors.append(
                _error("POSITION_SCHEMA", "Missing or unexpected position properties.", path=path)
            )
        if not _text(position.get("title")) or len(_text(position.get("title"))) > 256:
            errors.append(
                _error(
                    "POSITION_SCHEMA", "A literal title of 1–256 characters is required.", path=path
                )
            )
        evidence = position.get("evidence")
        if not isinstance(evidence, dict):
            errors.append(
                _error("POSITION_SCHEMA", "Evidence must map field names to block IDs.", path=path)
            )
            evidence = {}
        if set(evidence) - set(POSITION_FIELDS):
            errors.append(_error("POSITION_SCHEMA", "Unknown evidence field.", path=path))
        checked = {
            key: _check_ids(ids, index, path + "." + key, errors) for key, ids in evidence.items()
        }
        actual.update((_text(position.get("title")), key) for key in checked.get("title", []))
        errors.extend(
            _table_field_errors(position, checked, index, table_rows, position_tables, path)
        )
        for field in POSITION_FIELDS:
            value = position.get(field)
            if value is None:
                continue
            ids = checked.get(field, [])
            supported = False
            if field == "headcount":
                supported = (
                    type(value) is int
                    and value > 0
                    and any(
                        _count(_text(index[key])) == value
                        or (
                            key in checked.get("title", [])
                            and _embedded_count(_text(index[key])) == value
                        )
                        for key in ids
                    )
                )
            elif field == "deadline_on":
                supported = _supported_deadline(value, ids, bundle)
            elif field == "employer_text":
                supported = _employer_supported(value, ids, bundle, index)
            elif (
                field == "employment_arrangement"
                and isinstance(value, str)
                and value in {"劳务派遣", "labor_dispatch", "direct"}
            ):
                # Direct employment is not implied by a recruitment announcement.
                words = (
                    ("劳务派遣",)
                    if value in {"劳务派遣", "labor_dispatch"}
                    else ("直接聘用", "直接签订劳动合同")
                )
                supported = any(
                    any(word in index[key] for word in words)
                    and not re.search(r"不|非|无", index[key])
                    for key in ids
                )
            else:
                supported = _literal_supported(value, ids, index)
            if not supported:
                errors.append(
                    _error(
                        "UNSUPPORTED_FIELD",
                        "Field lacks literal or safely normalized source support.",
                        path=path + "." + field,
                    )
                )
    event, _ = _event(bundle)
    if status == "cancelled" and event != "cancellation":
        errors.append(
            _error("UNSUPPORTED_CANCELLATION", "No evidence-backed cancellation headline.")
        )
    if status == "cancelled" and (
        positions or any("部分" in title for title, _ in headline_candidates(bundle))
    ):
        errors.append(
            _error(
                "UNSUPPORTED_CANCELLATION", "Partial cancellation cannot cancel the whole notice."
            )
        )
    if status == "success":
        if not positions or result.get("errors") or material_issues(bundle) or event:
            errors.append(
                _error(
                    "FALSE_COMPLETE",
                    "Empty, incomplete, event or errored extraction cannot be success.",
                )
            )
        expected = extract_positions(bundle)
        for position in expected["positions"]:
            if not any(
                (_text(position["title"]), key) in actual for key in position["evidence"]["title"]
            ):
                errors.append(
                    _error("OMITTED_POSITION_ROW", "A recognized literal position row was omitted.")
                )
        if any(
            item["code"]
            in {
                "ROW_SKIPPED",
                "ROW_SHAPE",
                "MISSING_CELL_EVIDENCE",
                "UNSUPPORTED_POSITION_TABLE",
                "AMBIGUOUS_TITLE_COLUMNS",
                "CONFLICTING_HEADCOUNT",
            }
            for item in expected["errors"]
        ):
            errors.append(
                _error("UNCHECKED_ROWS", "Source rows remain unsupported or unaccounted for.")
            )
    if not positions and any(
        "title" in _headers(table)[0] and len(table.get("rows", [])) > 1
        for doc in documents(bundle)
        for table in doc.get("tables", [])
    ):
        errors.append(
            _error(
                "SUSPICIOUS_ZERO_POSITIONS",
                "Position-like table rows exist but no positions were returned.",
            )
        )
    if "validity" in result:
        validity = result["validity"]
        if (
            not isinstance(validity, dict)
            or not set(VALIDITY_SCHEMA["required"]) <= set(validity)
            or set(validity) - set(VALIDITY_SCHEMA["properties"])
        ):
            errors.append(_error("VALIDITY_SCHEMA", "Invalid validity properties."))
        else:
            _, date_reasons, _ = _deadline_analysis(bundle)
            expected_metadata = _validity_metadata(bundle, date_reasons)
            if any(name in validity for name in expected_metadata) or (
                material_issues(bundle) and validity.get("status") != "unknown"
            ):
                if any(validity.get(name) != value for name, value in expected_metadata.items()):
                    errors.append(
                        _error(
                            "VALIDITY_CONTEXT", "Validity must retain its basis and material scope."
                        )
                    )
            if "application_period" in validity:
                errors.extend(validate_application_period(bundle, validity["application_period"]))
                if validity.get("current_hiring_status") != "not_verified":
                    errors.append(
                        _error(
                            "HIRING_STATUS_UNSUPPORTED",
                            "A declared rule does not verify current hiring.",
                        )
                    )
            elif (
                "current_hiring_status" in validity
                and validity["current_hiring_status"] != "not_verified"
            ):
                errors.append(
                    _error(
                        "HIRING_STATUS_UNSUPPORTED",
                        "Current hiring cannot be asserted by a parser.",
                    )
                )
            ids = _check_ids(validity["evidence_ids"], index, "validity.evidence_ids", errors)
            if validity["status"] not in ("unknown", "expired", "not_expired") or not _text(
                validity["reason"]
            ):
                errors.append(_error("VALIDITY_SCHEMA", "Invalid validity status or reason."))
            if validity["status"] == "unknown":
                if validity["deadline"] is not None:
                    errors.append(
                        _error("VALIDITY_SCHEMA", "Unknown validity must not assert a deadline.")
                    )
            else:
                candidates, reasons, _ = _deadline_analysis(bundle)
                if reasons or not any(
                    item["day"].isoformat() == validity["deadline"] and item["id"] in ids
                    for item in candidates
                ):
                    errors.append(
                        _error(
                            "UNSUPPORTED_VALIDITY",
                            "Whole-notice validity lacks an unambiguous application deadline.",
                        )
                    )
    return errors
