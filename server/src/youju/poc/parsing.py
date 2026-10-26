"""Conservative USTC table parsing with original-snapshot evidence; no guessed jobs."""

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from youju.models.base import normalize_url

from .scope import DiscoveryScope

PARSER_KEY = "ustc-employment-v1"
DATE = re.compile(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?")
ALIASES = {
    "岗位名称": "title",
    "招聘岗位": "title",
    "岗位": "title",
    "招聘人数": "headcount",
    "人数": "headcount",
    "需求人数": "headcount",
    "学历": "education_text",
    "学历要求": "education_text",
    "专业": "major_text",
    "专业要求": "major_text",
    "招聘条件": "requirements_text",
    "任职条件": "requirements_text",
    "岗位要求": "requirements_text",
    "岗位职责": "duties_text",
    "工作职责": "duties_text",
    "报名截止时间": "deadline_text",
    "报名截止日期": "deadline_text",
}


def text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def locator(tag: Tag) -> str:
    """A CSS path into the original parsed snapshot, computed before any tree modifications."""
    path = []
    while isinstance(tag, Tag) and tag.name != "[document]":
        index = len(tag.find_previous_siblings(tag.name)) + 1
        path.append(f"{tag.name}:nth-of-type({index})")
        tag = tag.parent
    return " > ".join(reversed(path))


def evidence(tag: Tag) -> dict:
    return {"locator": locator(tag), "text": text(tag)}


def page_role(url: str) -> str:
    parts = urlsplit(url)
    name = parts.path.rsplit("/", 1)[-1].lower()
    if name == "indexnews.aspx" and parse_qs(parts.query).get("sign"):
        return "notice"
    if name in {"indexnewslist.aspx", "index.aspx", ""}:
        return "listing"
    return "other"


@dataclass(frozen=True)
class FoundLink:
    url: str
    raw_url: str
    label: str
    role: str
    attachment: bool


def discover(
    soup: BeautifulSoup, final_url: str, scope: DiscoveryScope, limit: int = 500
) -> list[FoundLink]:
    base = soup.find("base", href=True)
    try:
        base_url = urljoin(final_url, base["href"]) if base else final_url
    except ValueError:
        return []  # Do not invent relative URLs when the document base cannot be resolved.
    found = {}
    for anchor in soup.find_all("a", href=True):
        raw = anchor["href"].strip()
        try:
            url = normalize_url(urljoin(base_url, raw))
        except (ValueError, UnicodeError):
            continue
        if not scope.contains(url) or url == normalize_url(final_url):
            continue
        if url in found:
            continue
        suffix = urlsplit(url).path.lower()
        attachment = suffix.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"))
        found[url] = FoundLink(url, raw, text(anchor), page_role(url), attachment)
        if len(found) >= limit:
            break
    return list(found.values())


@dataclass
class ParsedPage:
    status: str
    role: str
    title: str | None
    positions: list[dict] = field(default_factory=list)
    links: list[FoundLink] = field(default_factory=list)
    explanation: str = ""


def _deadline(nodes: list[Tag]) -> tuple[str | None, date | None, list[dict]]:
    if not nodes:
        return None, None, []
    raw = "\n".join(dict.fromkeys(text(node) for node in nodes))
    dates = set()
    invalid = False
    for match in DATE.finditer(raw):
        try:
            dates.add(date(*map(int, match.groups())))
        except ValueError:
            invalid = True
    # Multiple dates are not silently interpreted as one deadline.
    return (
        raw,
        next(iter(dates)) if len(dates) == 1 and not invalid else None,
        [evidence(n) for n in nodes],
    )


def parse_html(html: str, final_url: str, scope: DiscoveryScope) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    links = discover(soup, final_url, scope)
    role = page_role(final_url)
    title = text(soup.title) if soup.title else None
    result = ParsedPage("empty", role, title, links=links)
    content = soup.select_one("#oInfoContent")
    if role == "listing":
        result.status = "valuable" if any(link.role == "notice" for link in links) else "empty"
        result.explanation = "notice_links" if result.status == "valuable" else "no_notice_links"
        return result
    if content is None or role != "notice":
        result.explanation = "unsupported_page_or_missing_content"
        return result
    if title and any(word in title for word in ("取消招聘", "招聘取消", "终止招聘", "撤销招聘")):
        result.status, result.explanation = "cancelled", "explicit_cancellation_requires_review"
        return result
    if title and any(word in title for word in ("更正", "补充公告", "调整公告")):
        result.status, result.explanation = "review", "correction_requires_announcement_association"
        return result

    inner_tables = {id(table) for table in content.find_all("table")}
    paragraphs = [
        node for node in content.find_all("p") if id(node.find_parent("table")) not in inner_tables
    ]
    deadlines = [
        node
        for node in paragraphs
        if re.search(r"报名|投递|应聘申请", text(node)) and re.search(r"截止|截至", text(node))
    ]
    global_deadline = _deadline(deadlines)
    dispatch = next(
        (node for node in paragraphs if "劳务派遣" in text(node) and "签订劳动合同" in text(node)),
        None,
    )
    merged = False
    for table in content.find_all("table"):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if not rows:
            continue
        headers = rows[0].find_all(["td", "th"], recursive=False)
        fields = [ALIASES.get(re.sub(r"\s+", "", text(cell))) for cell in headers]
        if "title" not in fields:
            continue  # Research-demand tables without an explicit job-title column need review.
        for row in rows[1:]:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) != len(headers) or any(
                str(cell.get(attr, "1")) != "1"
                for cell in cells + headers
                for attr in ("rowspan", "colspan")
            ):
                merged = True
                continue
            named = {key: cell for key, cell in zip(fields, cells, strict=True) if key is not None}
            title_cell = named["title"]
            title_text = text(title_cell)
            if not title_text or len(title_text) > 256 or title_text in ALIASES:
                continue
            counts = []
            embedded = re.fullmatch(r"(.+?)\s*[（(]\s*(\d+)\s*(?:人|名)\s*[）)]", title_text)
            if embedded:
                title_text = embedded.group(1).strip()
                counts.append(int(embedded.group(2)))
            if "headcount" in named:
                match = re.fullmatch(r"\s*(\d+)\s*(?:人|名)?\s*", text(named["headcount"]))
                if match:
                    counts.append(int(match.group(1)))
            row_evidence = {key: [evidence(cell)] for key, cell in named.items()}
            if embedded:
                row_evidence.setdefault("headcount", []).append(evidence(title_cell))
            position = {
                "title": title_text,
                "headcount": counts[0]
                if counts and len(set(counts)) == 1 and counts[0] > 0
                else None,
                "evidence": row_evidence,
                "employment_arrangement": "unknown",
            }
            for field_name in ("education_text", "major_text", "requirements_text", "duties_text"):
                if field_name in named:
                    position[field_name] = text(named[field_name]) or None
            deadline_nodes = [named["deadline_text"]] if "deadline_text" in named else None
            deadline_text, deadline_on, deadline_evidence = (
                _deadline(deadline_nodes) if deadline_nodes else global_deadline
            )
            position.update(deadline_text=deadline_text, deadline_on=deadline_on)
            if deadline_evidence:
                row_evidence["deadline_text"] = deadline_evidence
            if dispatch is not None:
                arrangement = text(dispatch)
                position["employment_arrangement"] = "labor_dispatch"
                row_evidence["employment_arrangement"] = [evidence(dispatch)]
                employer = re.search(r"([^。；]{2,128}?)与\s*劳务派遣人员签订劳动合同", arrangement)
                unit = re.search(r"劳务派遣至\s*([^。；]+)", arrangement)
                if employer:
                    position["employer_text"] = employer.group(1).strip()
                    row_evidence["employer_text"] = [evidence(dispatch)]
                if unit:
                    position["hiring_unit_text"] = unit.group(1).strip()
                    row_evidence["hiring_unit_text"] = [evidence(dispatch)]
            result.positions.append(position)
    result.status = "valuable" if result.positions else "review"
    result.explanation = (
        "merged_rows_skipped"
        if merged
        else ("explicit_position_rows" if result.positions else "no_supported_position_table")
    )
    return result
