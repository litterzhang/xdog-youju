"""Pure, bounded application of trusted associations and literal position changes.

The source adapter owns association/review authority. Text remains untrusted;
metadata alone can never cancel jobs or supply an application deadline.
"""

import re
from copy import deepcopy

from .deadlines import dates_from_text, validate_dates
from .drafts import ExtractionDraft

DEPENDENCY_CONTRACT = "position-fact-dependencies-v1"
CONTRACT = DEPENDENCY_CONTRACT
_SCOPE = r"(?:本次招聘|本招聘|原招聘公告|所有岗位|全部岗位)"
_EXTENSION = r"报名(?:截止(?:日期|时间)?|时间|期限)?(?:统一)?(?:延长|延期)(?:至|到)"
_CANCEL = r"(?:取消|终止|撤销|停止)"
# Only complete literal calendar dates, never contextual years/relative rules.
_LITERAL_DATE = r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)"
_DATE_TOKEN = re.compile(r"[0-9]+\s*(?:年|月|日|[-/.])\s*[0-9]*|[0-9]+[:：][0-9]+")
_UNSAFE = re.compile(
    r"(?:各|不同|部分|个别|其他|其余|相关|指定).{0,4}(?:岗位|职位)"
    r"|(?:岗位|职位)[A-Za-z0-9甲乙丙]|仅|只限|限于|除外|除.{0,30}外|但|例外"
    r"|如果|一旦|倘若|如若|若|视情况|可能|拟|计划|预计|原则上"
    r"|继续(?:招聘|报名)|照常|正常报名|仍可|不受影响"
    r"|岗位.{0,12}(?:保留|继续|不变|不受|正常)"
    r"|如.{0,20}(?:取消|终止|报名|延期|延长)"
    r"|不(?:予|再|会)?(?:取消|终止|撤销|停止|延长|延期)|未(?:予|经)?(?:取消|延长|延期)"
    r"|暂不|并非|不是|误写|误标|不属实|不生效|无效|不适用|示例|假设"
    r"|面试|笔试|体检|公示|考核|到岗|入职|资格审查|缴费"
    r"|长期|常年|滚动|招满|聘满|额满|随到随|工作日|次日|明日|顺延"
    r"|(?:人数|数量|条件|要求|专业|学历|岗位).{0,6}(?:变更|调整|增加|减少|改为|修改|更正|核减)"
    r"|(?:人数|数量|条件|要求|专业|学历|(?<!所有)(?<!全部)岗位).{0,6}取消"
    r"|(?:变更|调整|增加|减少|修改|更正|核减).{0,6}(?:人数|数量|条件|要求|专业|学历|岗位)"
)
_PREFIX = r"(?:(?:现|现将|现决定|经研究决定)[，,：:]?)?"
_EXTENSION_CLAUSE = re.compile(
    _PREFIX + _SCOPE + r"(?:的)?" + _EXTENSION + r"\s*(?P<date>" + _LITERAL_DATE + r")"
)
_CANCEL_CLAUSE = re.compile(
    _PREFIX + r"(?:" + _SCOPE + r"(?:现予以|予以|现)?" + _CANCEL + r"|" + _CANCEL + _SCOPE + r")"
)


def _identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _observation(document, effect):
    """One explicit whole-notice clause; other blocks cannot conceal exceptions."""
    blocks = document.get("blocks")
    if (
        not isinstance(blocks, list)
        or not 1 <= len(blocks) <= 512
        or document.get("tables") not in (None, [])
    ):
        return None
    texts = []
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            return None
        text = block["text"].strip()
        if len(text) > 8192:
            return None
        # Explicit navigation is not article prose. No acquisition flags are read.
        if block.get("region_kind") not in ("navigation", "recommendation"):
            texts.append(text)
    literal = "\n".join(texts)
    if len(literal) > 65536 or _UNSAFE.search(literal):
        return None
    if effect == "extension" and re.search(_CANCEL, literal):
        return None
    if effect == "cancellation" and re.search(r"延长|延期", literal):
        return None
    found = []
    for text in texts:
        # Scope must be in the actual assertion, not borrowed from a heading.
        for clause in re.split(r"[。；;\n]", text):
            clause = clause.strip(" \t，,：:")
            match = (_EXTENSION_CLAUSE if effect == "extension" else _CANCEL_CLAUSE).fullmatch(
                clause
            )
            if match:
                found.append(match)
    if len(found) != 1:
        return None
    if effect == "cancellation":
        return {"end": None}
    exact = found[0]["date"]
    # No other dates (including old deadlines, interview dates, or incomplete dates)
    # can be silently discarded by the domain date parser's clause selection.
    if _DATE_TOKEN.search(literal.replace(exact, "", 1)):
        return None
    dates, rule, issues = dates_from_text(exact, application_context=True)
    end = dates["end"]
    if issues or rule != "fixed_deadline" or not end or end["tag"] != "source":
        return None
    return {"end": end}


def observe_change(document, effect):
    """Public literal-domain check used when staging an association, without I/O."""
    if effect not in {"extension", "cancellation"}:
        return None
    return _observation(document, effect)


def _receipt(change, document):
    # Malformed metadata is not allowed to leak non-JSON objects into receipts.
    keys = ("review_id", "effect", "target_bundle_id", "event_bundle_id", "status")
    receipt = {key: change.get(key) if _identifier(change.get(key)) else None for key in keys}
    receipt["event_version_id"] = (
        document.get("version_id") if document and _identifier(document.get("version_id")) else None
    )
    return receipt


def _extension_consistent(draft, end):
    common = draft["notice_facts"].get("dates", {})
    for raw in [draft["notice_facts"], *draft["positions"]]:
        dates = raw.get("dates", {})
        if not isinstance(dates, dict) or not isinstance(common, dict):
            return False
        try:
            validate_dates({"start": dates.get("start", common.get("start")), "end": end})
            previous = dates.get("end", common.get("end"))
            if previous is not None:
                validate_dates({"start": None, "end": previous})
                if previous["date"] and previous["date"] > end["date"]:
                    return False  # An earlier end is not a literal extension.
        except (TypeError, ValueError, KeyError):
            return False
    return True


def _replace_ends(draft, end):
    for raw in [draft["notice_facts"], *draft["positions"]]:
        if not isinstance(raw.get("dates"), dict):
            raw["dates"] = {}
        raw["dates"]["end"] = deepcopy(end)
        fields = raw.setdefault("fields", {})
        if end is not None:
            fields["application_rule"] = {"value": "fixed_deadline", "tag": "source"}


def apply_fact_changes(draft: dict, context: dict | None) -> dict:
    """Return {draft, disposition, dependencies}, without mutating either input.

    A canonical draft envelope is required (invalid envelopes raise ValueError).
    Absent context/change is a no-op. Known but unusable change context is a domain
    uncertainty, never a model-repair request or evidence of cancellation.
    """
    value = deepcopy(ExtractionDraft.from_dict(draft).as_dict())
    result = {"draft": value, "disposition": None, "dependencies": None}
    if context is None or (isinstance(context, dict) and "change" not in context):
        return result
    context = context if isinstance(context, dict) else {}
    change = context.get("change")
    change = change if isinstance(change, dict) else {}
    documents = context.get("change_documents")
    matches = (
        [
            doc
            for doc in documents
            if isinstance(doc, dict)
            and _identifier(change.get("review_id"))
            and doc.get("change_review_id") == change["review_id"]
            and doc.get("event_bundle_id") == change.get("event_bundle_id")
        ]
        if isinstance(documents, list) and len(documents) <= 128
        else []
    )
    document = matches[0] if len(matches) == 1 else None
    receipt = _receipt(change, document)
    result["dependencies"] = {"contract": CONTRACT, "change": receipt}
    valid = (
        all(
            _identifier(change.get(key))
            for key in ("review_id", "target_bundle_id", "event_bundle_id")
        )
        and _identifier(context.get("bundle_id"))
        and change["target_bundle_id"] == context["bundle_id"]
        and change.get("effect") in ("extension", "cancellation")
        and change.get("status") == "current"
        and "supersedes" in change
        and (change["supersedes"] is None or _identifier(change["supersedes"]))
        and change.get("supersedes") != change.get("review_id")
        and document is not None
        and _identifier(document.get("version_id"))
    )
    observation = _observation(document, change["effect"]) if valid else None
    if observation and change["effect"] == "extension":
        if _extension_consistent(value, observation["end"]):
            _replace_ends(value, observation["end"])
            receipt["outcome"] = "applied"
            return result
        observation = None
    if observation and change["effect"] == "cancellation":
        value["positions"] = []
        value["coverage"]["needs_detailed"] = False
        result["disposition"] = "cancelled"
        receipt["outcome"] = "cancelled"
        return result
    # The association is known, but cannot justify retaining potentially obsolete
    # notice-wide deadlines, nor deleting any good job. Unknown is explicit null,
    # including local endpoints that would otherwise override the common value.
    _replace_ends(value, None)
    issue = {
        "code": "ASSOCIATED_CHANGE_UNRESOLVED",
        "message": "Associated change requires source review; application deadlines are unknown",
        "owner": "position_domain",
        "review_id": receipt["review_id"],
        "effect": receipt["effect"],
        "path": "notice_facts.dates.end",
    }
    if issue not in value["issues"]:
        value["issues"].append(issue)
    value["coverage"].update(complete=False, needs_detailed=False)
    receipt["outcome"] = "unresolved"
    return result
