"""Pure, approximate cost routing over saved readable blocks, never date extraction.

No position/period/recipe dependencies, SDK, IO, acquisition-time substitution or
cancellation authority. Internal hypotheses must not become downstream facts.
"""

import re
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .contracts import RoutingCategory, RoutingDecision, RoutingPolicy

_NOTICE_TZ = timezone(timedelta(hours=8))
# Office HTML often splits a year across inline spans: "202 5 年".
_DATE = re.compile(
    r"(?<!\d)(\d(?:\s*\d){3})\s*[年./-]\s*(\d(?:\s*\d)?)"
    r"\s*[月./-]\s*(\d(?:\s*\d)?)(?!\d)\s*日?"
)
_MONTH_RANGE = re.compile(r"(\d{4})年(\d{1,2})月(?:至|到|[—–~～-])(?:(\d{4})年)?(\d{1,2})月")
_SHORT_END = re.compile(r"\s*(?:至|到|—|–|~|～|-)\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日")
_APPLICATION = re.compile(r"报名|应聘|报考|投递|申请|申报|application", re.I)
_ENDPOINT = re.compile(r"截止|截至|时限|期限|至|到|[—–~～]|deadline|clos(?:e|es|ing)", re.I)
_HEADING = re.compile(
    r"^(?:[（(]?[一二三四五六七八九十\d]+[）)、.．、]?\s*)?"
    r"(?:报名|应聘|报考|投递|申请|申报)(?:时间|时限|期限|截止日期|截止时间)?\s*[:：]?$"
)
_OPEN = re.compile(
    r"长期(?:有效|招聘|接受)|常年招聘|滚动招聘|招满|聘满|额满|随到随|"
    r"until filled|open.ended|rolling",
    re.I,
)
_NON_APPLICATION = re.compile(r"面试|笔试|缴费|出生|毕业|到岗|入职|资格审查|公示|考试|年龄")
_CANCEL = re.compile(r"取消招聘|终止招聘|撤销招聘|取消本次|招聘取消|招聘终止")
_AUX_LOCATOR = re.compile(r"(?:^|>\s*)(?:nav|header|footer|aside|head|title)(?=:|\s|$)")
_LINK_LOCATOR = re.compile(r"(?:^|>\s*)a(?=:|\s|$)")


def _documents(bundle):
    if not isinstance(bundle, Mapping):
        return []
    page = bundle.get("page")
    attachments = bundle.get("attachments")
    docs = [page] if isinstance(page, Mapping) else []
    if isinstance(attachments, (list, tuple)):
        docs.extend(item for item in attachments if isinstance(item, Mapping))
    return docs


def _blocks(document):
    blocks = document.get("blocks")
    if not isinstance(blocks, (list, tuple)):
        return []
    links = document.get("links")
    labels = (
        {
            item.get("label")
            for item in links
            if isinstance(item, Mapping) and isinstance(item.get("label"), str)
        }
        if isinstance(links, (list, tuple))
        else set()
    )
    output = []
    for block in blocks:
        if not isinstance(block, Mapping) or not isinstance(block.get("text"), str):
            continue
        text = block["text"].strip()
        locator = block.get("locator", "")
        locator = locator if isinstance(locator, str) else ""
        if block.get("region_kind") in ("navigation", "recommendation"):
            continue
        if document.get("format") == "html" and (
            _AUX_LOCATOR.search(locator) or (text in labels and _LINK_LOCATOR.search(locator))
        ):
            continue
        if text:
            output.append(text)
    return output


def _dates(text):
    """Internal day-level guesses, including inherited month/year in range ends."""
    dates, invalid = [], False
    for match in _DATE.finditer(text):
        try:
            day = date(*(int(re.sub(r"\s+", "", value)) for value in match.groups()))
        except ValueError:
            invalid = True
            continue
        dates.append(day)
        short = _SHORT_END.match(text, match.end())
        if short:
            try:
                dates.append(date(day.year, int(short[1] or day.month), int(short[2])))
            except ValueError:
                invalid = True
    return dates, invalid


def _age_hint(page, today):
    """Approximate page age only; neither publication truth nor a job-date export.

    Prefer the newest plausible day, so a recent update wins over an old signature.
    Birth/qualification/capture clocks and linked navigation are not page-age hints.
    """
    candidates = []
    for text in _blocks(page):
        if re.search(r"抓取|采集|核验|检查时间|出生|毕业|成立于|建校|版权所有", text):
            continue
        # Dates embedded in URLs describe another resource, not this page.
        text = re.sub(r"https?://[^\s<>]+", "", text)
        days, _ = _dates(text)
        candidates.extend(day for day in days if day <= today)
    hints = page.get("date_hints", [])
    if isinstance(hints, list):
        for hint in hints[:64]:
            if not isinstance(hint, Mapping) or hint.get("kind") not in {
                "html_meta",
                "html_time",
                "http_last_modified",
            }:
                continue
            value = hint.get("value")
            if not isinstance(value, str) or len(value) > 256:
                continue
            days, _ = _dates(value)
            if not days and hint["kind"] == "http_last_modified":
                try:
                    parsed = parsedate_to_datetime(value)
                    if parsed.utcoffset() is not None:
                        days = [parsed.astimezone(_NOTICE_TZ).date()]
                except (TypeError, ValueError, OverflowError):
                    pass
            candidates.extend(day for day in days if day <= today)
    return max(candidates) if candidates else None


def decide_route(
    bundle,
    classification=None,
    *,
    now: datetime,
    policy: RoutingPolicy = RoutingPolicy(),  # noqa: B008 - frozen immutable policy
) -> RoutingDecision:
    """Return only routing metadata. ``now`` is mandatory and timezone-aware.

    Day hints use UTC+08:00 and keep the entire endpoint day active; exact times,
    scope conflicts and authoritative expiry belong to independent downstream work.
    Classification may hint recruitment, but cannot supply dates or cancel a notice.
    """
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    if not isinstance(policy, RoutingPolicy):
        raise TypeError("policy must be a RoutingPolicy")
    today = now.astimezone(_NOTICE_TZ).date()

    def decision(category, reason):
        quick = category in {
            RoutingCategory.FIXED_EXPIRED,
            RoutingCategory.UNDATED_LOW_PRIORITY,
        }
        return RoutingDecision(
            category, "quick" if quick else "detailed", reason, policy.version, now
        )

    docs = _documents(bundle)
    texts = [_blocks(document) for document in docs]
    if not any(texts):
        return decision(RoutingCategory.UNKNOWN, "no_readable_blocks")
    business = classification.get("business_type") if isinstance(classification, Mapping) else None
    if business == "cancellation" or any(_CANCEL.search(t) for group in texts for t in group):
        return decision(RoutingCategory.UNKNOWN, "cancellation_requires_independent_review")

    endpoints, invalid, opened, application = [], False, False, False
    for group in texts:
        previous_heading = False
        previous_endpoint = False
        for text in group:
            heading = bool(_HEADING.fullmatch(text))
            for clause in re.split(r"[。；;\n]", text):
                has_application = bool(_APPLICATION.search(clause)) or previous_heading
                application |= has_application
                bounded_round = bool(
                    _MONTH_RANGE.search(re.sub(r"\s+", "", clause))
                    or re.search(r"本(?:轮|次|批)[^。；;]{0,16}(?:截止|截至)", clause)
                )
                opened |= bool(_OPEN.search(clause)) and not bounded_round
                if has_application and not _NON_APPLICATION.search(clause):
                    days, bad = _dates(clause)
                    # Start-only phrasing is not an endpoint, even under a 期限 label.
                    start_only = re.search(r"日起|开始|启动", clause) and not re.search(
                        r"截止|截至|至|到|deadline|clos", clause, re.I
                    )
                    if (
                        previous_endpoint
                        and days
                        and not start_only
                        and not _APPLICATION.search(clause)
                    ):
                        endpoints.extend(days)
                        invalid |= bad
                    month_range = _MONTH_RANGE.search(re.sub(r"\s+", "", clause))
                    if month_range:
                        try:
                            year = int(month_range[3] or month_range[1])
                            month = int(month_range[4])
                            days.append(date(year, month, monthrange(year, month)[1]))
                        except ValueError:
                            bad = True
                    if not start_only and (_ENDPOINT.search(clause) or len(days) > 1):
                        endpoints.extend(days)
                        invalid |= bad
            # Only the immediately following block, never another document.
            previous_heading = heading
            previous_endpoint = heading and bool(_ENDPOINT.search(text))

    if invalid:
        return decision(RoutingCategory.UNKNOWN, "invalid_application_date_hint")
    # Preserve separate open scopes; a bounded current-round/month range is not open.
    if endpoints and not opened:
        if max(endpoints) >= today:
            return decision(RoutingCategory.FIXED_ACTIVE, "fixed_endpoint_hint_not_past")
        return decision(RoutingCategory.FIXED_EXPIRED, "fixed_endpoint_hint_past_cost_only")

    page = bundle.get("page") if isinstance(bundle, Mapping) else None
    trusted_day = _age_hint(page, today) if isinstance(page, Mapping) else None
    if trusted_day is not None and trusted_day <= today:
        if (today - trusted_day).days >= policy.old_age_days:
            return decision(RoutingCategory.UNDATED_LOW_PRIORITY, "old_approximate_page_date")
        return decision(RoutingCategory.UNDATED_HIGH_PRIORITY, "recent_approximate_page_date")
    if opened or application or business == "recruitment":
        return decision(
            RoutingCategory.UNDATED_HIGH_PRIORITY, "undated_application_requires_detail"
        )
    return decision(RoutingCategory.UNKNOWN, "insufficient_routing_evidence")
