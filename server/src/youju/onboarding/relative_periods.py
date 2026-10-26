"""Literal anchored relative deadlines; unsupported counting semantics abstain.

Calendar data is an explicit host-reviewed input, never inferred from weekdays or
supplied by an adapter. No network, machine clock or captured-at substitution.
"""

import re
from datetime import date, timedelta

from youju.positions.relative_dates import validate_calendar as validate_calendar

RELATIVE_RE = re.compile(
    r"(?:报名|应聘)(?:截止(?:日期|时间)?|期限|时间)?\s*(?:为|至|于|在|是)?\s*"
    r"(?:自)?(?:本公告|公告)(?:发布|公布)(?:之日|日)?"
    r"(?P<counting>后(?:的)?第|次日起)\s*(?P<amount>[1-9]\d{0,2})\s*(?:个)?"
    r"(?P<unit>自然日|工作日)(?:内|截止|为止)?"
)
PUB_RE = re.compile(r"^(?:公告发布日期|发布日期|发布时间|发布于)\s*[:：]?\s*")


def publication_anchor(bundle):
    from .toolkit import _date_matches, content_blocks

    page = bundle.get("page", {})
    anchors = []
    for block in content_blocks(page):
        text = block.get("text", "")
        marker = PUB_RE.match(text)
        if marker is None or re.search(r"UTC|GMT|时区", text, re.I):
            continue
        dates, invalid = _date_matches(text[marker.end() :])
        if not invalid and len(dates) == 1 and dates[0][1].start() == 0 and page.get("version_id"):
            anchors.append(
                {
                    "on": dates[0][0].isoformat(),
                    "evidence_id": block["id"],
                    "version_id": page["version_id"],
                    "kind": "publication",
                }
            )
    if len({a["on"] for a in anchors}) != 1:
        return None
    return anchors[0]


def relative_windows(bundle):
    from .toolkit import NON_APPLICATION, PER_JOB, REVISION, ROLLING, content_blocks, documents

    anchor = publication_anchor(bundle)
    matched, windows, derivations, issues = [], [], [], []
    for document in documents(bundle):
        cells = {
            key
            for table in document.get("tables", [])
            for row in table.get("cell_evidence", [])
            for key in row
        }
        for block in content_blocks(document):
            text = block.get("text", "")
            rules = list(RELATIVE_RE.finditer(text))
            if not rules:
                continue
            matched.append(block)
            if (
                len(rules) != 1
                or block["id"] in cells
                or PER_JOB.search(text)
                or NON_APPLICATION.search(text)
                or REVISION.search(text)
                or ROLLING.search(text)
            ):
                issues.append("relative_scope_or_purpose_unresolved")
                continue
            if anchor is None:
                issues.append("missing_unique_literal_publication_anchor")
                continue
            rule = rules[0]
            tail = text[rule.end() :].strip(" 。；;，,\t\n")
            if tail or re.search(r"或|除外|另行|另有|以.+为准", text):
                issues.append("relative_clause_needs_scope_review")
                continue
            amount = int(rule["amount"])
            end = date.fromisoformat(anchor["on"])
            calendar = None
            if rule["unit"] == "自然日":
                try:
                    end += timedelta(days=amount)
                except OverflowError:
                    issues.append("relative_date_out_of_range")
                    continue
                unit = "calendar_days"
            else:
                calendar = bundle.get("period_context", {}).get("calendar")
                if not isinstance(calendar, dict) or not calendar.get("review_id"):
                    issues.append("missing_reviewed_workday_calendar")
                    continue
                try:
                    data = validate_calendar(calendar["value"])
                    seen = 0
                    for _ in range(3660):
                        end += timedelta(days=1)
                        if end.isoformat() not in data["days"]:
                            raise ValueError("Calendar does not cover relative deadline")
                        seen += int(data["days"][end.isoformat()])
                        if seen == amount:
                            break
                    else:
                        raise ValueError("Calendar cannot establish requested relative deadline")
                except (ValueError, KeyError):
                    issues.append("incomplete_or_invalid_workday_calendar")
                    continue
                unit = "workdays"
            ids = list(dict.fromkeys([block["id"], anchor["evidence_id"]]))
            windows.append(
                {
                    "scope": "notice",
                    "target_ids": [],
                    "end": end.isoformat(),
                    "precision": "day",
                    "timezone": "Asia/Shanghai",
                    "boundary": "inclusive",
                    "text": text[max(0, rule.start() - 200) : max(0, rule.start() - 200) + 8192],
                    "evidence_ids": ids,
                }
            )
            derivations.append(
                {
                    "amount": amount,
                    "unit": unit,
                    "counting": "exclude_anchor_day",
                    "anchor": anchor,
                    "calendar_ref": calendar["review_id"] if calendar else None,
                    "rule_evidence_id": block["id"],
                }
            )
    if matched and any(
        re.search(
            r"第[一二三四五六七八九十\d]+(?:轮|批)|分(?:批|轮)|另行(?:通知|公布)",
            block.get("text", ""),
        )
        for doc in documents(bundle)
        for block in content_blocks(doc)
    ):
        issues.append("relative_round_or_exception_scope_unresolved")
    return matched, windows, derivations, list(dict.fromkeys(issues))
