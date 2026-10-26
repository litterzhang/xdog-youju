"""Explicit publication-anchored relative dates and reviewed workday arithmetic.

No machine/capture clock, weekday guessing, source DAO, provider or cost policy.
Only supported literal notice-wide rules are derived; other fields/row dates survive.
"""

import re
from copy import deepcopy
from datetime import date, timedelta

from .deadlines import date_fact

RELATIVE_RE = re.compile(
    r"(?:报名|应聘)(?:截止(?:日期|时间)?|期限|时间)?\s*(?:为|至|于|在|是)?\s*"
    r"(?:自)?(?:本公告|公告)(?:发布|公布)(?:之日|日)?"
    r"(?P<counting>后(?:的)?第|次日起)\s*(?P<amount>[1-9]\d{0,2})\s*(?:个)?"
    r"(?P<unit>自然日|工作日)(?:内|截止|为止)?"
)
_PUBLICATION = re.compile(
    r"^(?:公告发布日期|发布日期|发布时间|发布于)\s*[:：]?\s*"
    r"(?P<y>\d{4})\s*[年/-]\s*(?P<m>\d{1,2})\s*[月/-]\s*(?P<d>\d{1,2})\s*日?\s*[。.]?$"
)
_UNSAFE = re.compile(
    r"面试|笔试|体检|公示|到岗|入职|资格审查|缴费|本轮|分批|分轮|"
    r"第[一二三四五六七八九十\d]+(?:轮|批)|部分岗位|其他岗位|仅适用|仅针对|"
    r"另行(?:通知|公布)|或以|以.+为准|除外|招满|长期|延期|延长|取消"
)


def validate_calendar(value):
    if not isinstance(value, dict) or set(value) != {"name", "timezone", "days"}:
        raise ValueError("Calendar requires name, timezone and a complete reviewed day map")
    if (
        not isinstance(value["name"], str)
        or not 0 < len(value["name"]) <= 128
        or value["timezone"] != "Asia/Shanghai"
        or not isinstance(value["days"], dict)
        or not 1 <= len(value["days"]) <= 3660
    ):
        raise ValueError("Invalid reviewed calendar bounds")
    previous = None
    for text in sorted(value["days"]):
        day = date.fromisoformat(text)
        if day.isoformat() != text or type(value["days"][text]) is not bool:
            raise ValueError("Calendar needs ISO dates and boolean working-day flags")
        if previous is not None and day != previous + timedelta(days=1):
            raise ValueError("Calendar coverage must be contiguous; no missing days may be guessed")
        previous = day
    return value


def _blocks(bundle):
    for document in [bundle.get("page", {}), *bundle.get("attachments", [])]:
        cells = {
            key
            for table in document.get("tables", [])
            for row in table.get("cell_evidence", [])
            for key in row
        }
        for block in document.get("blocks", []):
            if block.get("id") not in cells and block.get("region_kind") not in {
                "navigation",
                "recommendation",
            }:
                yield document, block


def apply_relative_dates(draft, bundle, context=None):
    value = deepcopy(draft)
    observations = list(_blocks(bundle))
    rules = [
        (doc, block, match)
        for doc, block in observations
        for match in RELATIVE_RE.finditer(block.get("text", ""))
    ]
    if not rules:
        return {"draft": value, "dependencies": None}
    anchors = []
    page = bundle.get("page", {})
    for block in page.get("blocks", []):
        match = _PUBLICATION.fullmatch(block.get("text", "").strip())
        if match and block.get("id") and page.get("version_id"):
            try:
                anchors.append(
                    {
                        "on": date(int(match["y"]), int(match["m"]), int(match["d"])).isoformat(),
                        "evidence_id": block["id"],
                        "version_id": page["version_id"],
                    }
                )
            except ValueError:
                pass
    calendar = context.get("calendar") if isinstance(context, dict) else None
    needs_calendar = any(rule["unit"] == "工作日" for _, _, rule in rules)
    deps = {"contract": "position-fact-dependencies-v1", "relative": {"outcome": "unresolved"}}
    if needs_calendar:
        deps["calendar"] = {
            "review_id": calendar.get("review_id") if isinstance(calendar, dict) else None
        }
    resolved, reason = None, "Relative date needs one supported rule and literal publication anchor"
    if (
        len(rules) == 1
        and len({a["on"] for a in anchors}) == 1
        and not any(_UNSAFE.search(block.get("text", "")) for _, block in observations)
    ):
        _, block, rule = rules[0]
        tail = block["text"][rule.end() :].strip(" 。；;，,\t\n")
        if not tail:
            anchor = anchors[0]
            end = date.fromisoformat(anchor["on"])
            amount = int(rule["amount"])
            try:
                if needs_calendar:
                    if not isinstance(calendar, dict) or not calendar.get("review_id"):
                        raise ValueError("A reviewed workday calendar is required")
                    mapping = validate_calendar(calendar["value"])["days"]
                    count = 0
                    for _ in range(3660):
                        end += timedelta(days=1)
                        if end.isoformat() not in mapping:
                            raise ValueError("Reviewed calendar does not cover the full interval")
                        count += int(mapping[end.isoformat()])
                        if count == amount:
                            break
                    else:
                        raise ValueError("Reviewed calendar cannot establish relative end")
                else:
                    end += timedelta(days=amount)
                resolved = date_fact(end.year, end.month, end.day)
                deps["relative"] = {
                    "outcome": "applied",
                    "amount": amount,
                    "unit": "workdays" if needs_calendar else "calendar_days",
                    "counting": "exclude_anchor_day",
                    "anchor": anchor,
                    "rule_evidence_id": block["id"],
                    "end": end.isoformat(),
                }
            except (ValueError, KeyError, OverflowError) as error:
                reason = str(error)
    value.setdefault("notice_facts", {}).setdefault("dates", {})["end"] = resolved
    if resolved:
        value["notice_facts"].setdefault("fields", {})["application_rule"] = {
            "value": "fixed_deadline",
            "tag": "source",
        }
    else:
        value["issues"].append(
            {
                "code": "RELATIVE_DATE_UNRESOLVED",
                "message": reason,
                "path": "notice_facts.dates.end",
            }
        )
        value["coverage"].update(complete=False, needs_detailed=False)
    return {"draft": value, "dependencies": deps}
