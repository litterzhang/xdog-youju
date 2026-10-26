"""Pure application-date facts. No clock, routing decision or notice-validity oracle."""

import re
from datetime import date, time

_DATE = re.compile(
    r"(?:(?P<year>\d{4})\s*(?:年|[-/.])\s*)?(?P<month>\d{1,2})\s*(?:月|[-/.])\s*(?P<day>\d{1,2})(?:日|号)?(?:\s*(?P<time>\d{1,2}:\d{2})(?::\d{2})?)?"
)
_APPLICATION = re.compile(r"报名|应聘|申请|投递|简历接收")


def date_fact(year, month, day, *, inferred=False, clock=None):
    """Missing year stays missing; any supplied contextual year is explicitly inferred."""
    date(year or 2000, month, day)  # Leap-day without a year remains possible.
    if clock is not None and clock != "24:00":
        clock = time.fromisoformat(clock.zfill(5)).isoformat(timespec="minutes")
    return {
        "date": date(year, month, day).isoformat() if year else None,
        "year": year,
        "month": month,
        "day": day,
        "year_status": "inferred" if inferred else "explicit" if year else "unknown",
        "tag": "ai_inferred" if inferred else "source" if year else "unknown",
        "time": clock,
    }


def validate_date_fact(value):
    if not isinstance(value, dict) or set(value) != {
        "date",
        "year",
        "month",
        "day",
        "year_status",
        "tag",
        "time",
    }:
        raise ValueError("Invalid structured application date")
    if type(value["month"]) is not int or type(value["day"]) is not int:
        raise ValueError("Date components must be integers")
    year = value["year"]
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        raise ValueError("Year must be an integer 1..9999 or null")
    if value["time"] is not None and not isinstance(value["time"], str):
        raise ValueError("Time must be a string or null")
    if value["year_status"] not in {"explicit", "inferred", "unknown"}:
        raise ValueError("Invalid year status")
    expected = date_fact(
        year,
        value["month"],
        value["day"],
        inferred=value["year_status"] == "inferred",
        clock=value["time"],
    )
    if expected != value:
        raise ValueError("Inconsistent date, year certainty or provenance")


def validate_dates(value):
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError("Application dates require start and end")
    for part in value.values():
        if part is not None:
            validate_date_fact(part)
    start, end = value["start"], value["end"]
    if start and end and start["date"] and end["date"]:
        if start["date"] > end["date"]:
            raise ValueError("Application start follows deadline")
        if start["date"] == end["date"] and start["time"] and end["time"]:
            if start["time"] > end["time"]:
                raise ValueError("Application start time follows deadline")


def dates_from_text(text, *, application_context=False):
    """Read recognizable application prose or a cell under an application heading."""
    text = text if isinstance(text, str) else ""
    dates = {"start": None, "end": None}
    issues = []
    text = re.sub(r"(?<!\d)((?:\d\s*){4})年", lambda match: re.sub(r"\s+", "", match[0]), text)
    declaration = bool(re.search(r"招聘|招聘启事|招聘公告", text))
    if not application_context and not _APPLICATION.search(text) and not declaration:
        return dates, None, issues
    if re.search(r"并非|不是|不再是|不为|误写为|误标为|已(?:经)?招满|停止招聘|停止报名", text):
        issues.append({"code": "NEGATED_APPLICATION_DATE", "message": text})
        return dates, None, issues
    if re.search(r"招满(?:即止|为止|后停止报名)|额满(?:即止|为止)", text):
        rule = "until_filled" if application_context or declaration else None
    elif re.search(r"长期有效|常年(?:招聘|接受|报名)", text):
        rule = "open_ended" if application_context or declaration else None
    else:
        rule = None
    # Pair a cutoff clause with its immediately preceding application clause,
    # excluding later interview/notification dates rather than taking the last date.
    original_text = text
    segments = re.split(r"[。；;\n，,]", text)
    selected, preceding_application = [], application_context
    for segment in segments:
        segment = segment.strip()
        explicit_application = bool(_APPLICATION.search(segment))
        other_phase = bool(re.search(r"面试|笔试|体检|公示|考核|通知", segment))
        first_date = _DATE.search(segment)
        prefix = segment[: first_date.start()] if first_date else segment
        if (first_date and re.search(r"面试|笔试|体检|公示|考核|资格审查|缴费", prefix)) or (
            first_date and re.search(r"(?:报名|应聘).{0,8}(?:截止|结束)(?:后|之后|以后)", prefix)
        ):
            preceding_application = False
            continue
        if other_phase and not (explicit_application and re.search(r"截止|截至|结束", segment)):
            preceding_application = False
            continue
        eligibility = re.search(
            r"出生|周岁|年龄|年满|毕业|取得.{0,12}(?:学位|学历|资格|证书)|证书.{0,6}有效", segment
        )
        enrollment = re.search(
            r"(?:报名|应聘)(?:截止|时间|日期|期限)|简历.{0,6}截止|"
            r"(?:报名|应聘|简历|申请)(?:材料|表)?.{0,80}(?:提交|投递|发送|寄送|报送)|"
            r"(?:提交|投递|发送|寄送|报送).{0,80}(?:报名|应聘|简历|申请)|"
            r"(?:日|号).{0,8}(?:前|以前|之前).{0,12}(?:报名|应聘|投递)",
            segment,
        )
        if eligibility and not enrollment:
            preceding_application = False
            continue
        cutoff = bool(re.search(r"截止|截至|结束|最迟|之前|以前|至.*止|(?:日|号)\s*前", segment))
        if explicit_application or (preceding_application and cutoff) or application_context:
            selected.append(segment)
        preceding_application = explicit_application or (
            preceding_application and not _DATE.search(segment)
        )
    text = "；".join(selected)
    # Exact business facts cannot inherit the cost router's approximate reading.
    # Unsupported explicit zones/meridiems and negated declarations abstain;
    # email components such as UTC@example.org are not timezone declarations.
    if re.search(r"并非|不是|不再是|不为|误写为|误标为", text):
        issues.append({"code": "NEGATED_APPLICATION_DATE", "message": text})
        return dates, rule, issues
    if re.search(r"(?<![\w@.+-])(?:UTC|GMT|am|pm)\b(?![@.])|时区|上午|下午|晚上", text, re.I):
        issues.append({"code": "UNSUPPORTED_APPLICATION_TIME", "message": text})
        return dates, rule, issues
    if re.search(r"(?:但|但是|除外情况为|另有安排为)\s*[，,：:]?\s*$", original_text):
        issues.append({"code": "UNFINISHED_APPLICATION_EXCEPTION", "message": text})
        return dates, rule, issues
    if re.search(r"\d{1,2}[:：]\d{2}\s*[+-]\s*\d{2}:?\d{2}", text):
        issues.append({"code": "UNSUPPORTED_APPLICATION_TIME", "message": text})
        return dates, rule, issues
    if re.search(r"\d{1,2}[:：]\d{2}[:：]\d{2}", text):
        issues.append({"code": "UNSUPPORTED_APPLICATION_SECONDS", "message": text})
        return dates, rule, issues
    matches = list(_DATE.finditer(text))
    found = []
    for match in matches:
        try:
            found.append(
                date_fact(
                    int(match["year"]) if match["year"] else None,
                    int(match["month"]),
                    int(match["day"]),
                    clock=match["time"],
                )
            )
        except ValueError as error:
            issues.append({"code": "INVALID_APPLICATION_DATE", "message": str(error)})
    if issues:
        return dates, rule, issues
    connector = text[matches[0].end() : matches[1].start()] if len(matches) == 2 else ""
    if len(found) == 2 and re.fullmatch(r"\s*起?(?:至|到|[-—~～])\s*", connector):
        # The written year applies to an unambiguous same-year syntactic range;
        # this is source normalization, not an AI call or a host-clock guess.
        if found[0]["year"] and not found[1]["year"]:
            if (found[1]["month"], found[1]["day"]) >= (found[0]["month"], found[0]["day"]):
                found[1] = date_fact(
                    found[0]["year"], found[1]["month"], found[1]["day"], clock=found[1]["time"]
                )
            else:
                issues.append(
                    {
                        "code": "INCOMPLETE_APPLICATION_YEAR",
                        "message": "Cross-year range end has no explicit year",
                    }
                )
        dates = {"start": found[0], "end": found[1]}
        rule = "fixed_deadline"
    elif len(found) == 1:
        prefix = text[: matches[0].start()]
        tail = text[matches[0].end() :]
        start_marker = bool(
            re.search(r"开始|启动|起始|开放报名", prefix)
            or re.match(r"\s*起(?:报名|接受|，|；|$)", tail)
        )
        closing_prefix = bool(re.search(r"截止|截至|结束|最迟", prefix))
        if start_marker and not closing_prefix:
            dates["start"] = found[0]
        elif (
            re.search(r"截止|截至|结束|最迟|之前|以前|前$|至.*止|(?:日|号)\s*前", text)
            or application_context
        ):
            dates["end"] = found[0]
            rule = "fixed_deadline"
        else:
            issues.append({"code": "AMBIGUOUS_APPLICATION_DATE", "message": text})
    elif len(found) > 1:
        issues.append({"code": "AMBIGUOUS_APPLICATION_DATE", "message": text})
    return dates, rule, issues


def merge_date_observations(observations):
    """Conflicting observations clear only the disputed endpoint, not other job facts."""
    dates, issues = {"start": None, "end": None}, []
    for name in dates:
        candidates = []
        for observation in observations:
            value = observation.get(name)
            if value is not None and value not in candidates:
                candidates.append(value)
        if len(candidates) == 1:
            dates[name] = candidates[0]
        elif len(candidates) > 1:
            issues.append(
                {
                    "code": "APPLICATION_DATE_CONFLICT",
                    "message": f"Conflicting {name} observations",
                    "endpoint": name,
                    "observations": candidates,
                }
            )
    return dates, issues
