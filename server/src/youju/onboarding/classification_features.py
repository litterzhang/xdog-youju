"""Page-only classification signals; originals and attachment audit state stay intact."""

import hashlib
import re

from .toolkit import _headers, _text, content_blocks, evidence_index


def page_issues(bundle):
    page = bundle.get("page", {})
    issues = []
    if page.get("status") != "ok" or page.get("errors") or page.get("truncated"):
        issues.append("page_incomplete")
    if not any(_text(block.get("text")) for block in page.get("blocks", [])):
        issues.append("page_without_readable_evidence")
    return issues


def prose_blocks(bundle):
    """Remove duplicated descendant text, not unrepresented parent prose.

    Focus strings are classification features only. IDs still point to original
    literal blocks; no rewritten focus string is persisted as source evidence.
    """
    page = bundle.get("page", {})
    all_blocks = page.get("blocks", [])
    indexed = [(_text(block.get("locator")), _text(block.get("text"))) for block in all_blocks]
    output = []
    for block in content_blocks(page):
        text = _text(block.get("text"))
        location = _text(block.get("locator"))
        # Legacy dropdowns often use javascript: anchors, so they are absent
        # from the URL inventory. Do not mistake them for article prose. Inline
        # paragraph/heading links remain content; listing labels are separate.
        if re.search(r"(?:^| > )a:", location) and not re.search(
            r"(?:^| > )(?:p|h[1-6]):", location
        ):
            continue
        descendants = sorted(
            (
                (path, value)
                for path, value in indexed
                if location and path.startswith(location + " > ")
            ),
            key=lambda item: len(item[0]),
        )
        covered = []
        for child_location, value in descendants:
            if any(child_location.startswith(parent + " > ") for parent in covered):
                continue
            if value and value in text:
                text = text.replace(value, " ", 1)
                covered.append(child_location)
        text = _text(text)
        if text:
            output.append({"id": block["id"], "text": text})
    return output


def page_shape(bundle):
    page = bundle.get("page", {})
    index = evidence_index({"page": page})
    prose = prose_blocks(bundle)
    links = []
    seen = set()
    for link in page.get("links", []):
        label, url = _text(link.get("label")), link.get("url")
        if (
            link.get("is_attachment")
            or link.get("region_kind") in {"navigation", "recommendation"}
            or link.get("kind", "page") != "page"
            or not url
            or url in seen
            or len(label) < 8
        ):
            continue
        ids = [key for key, value in index.items() if label in _text(value)]
        if ids:
            seen.add(url)
            links.append({"url": url, "label": label, "id": ids[0]})
    position_headers = [
        key
        for table in page.get("tables", [])
        if _headers(table)[0].count("title") == 1 and len(table.get("rows", [])) > 1
        for key in table.get("cell_evidence", [[]])[0]
        if key in index
    ]
    body = [
        block
        for block in prose
        if not re.match(r"^(?:版权所有|Copyright|地址[：:])", block["text"], re.I)
    ]
    long_prose = [block for block in body if len(block["text"]) >= 100]
    substantial = [block for block in body if len(block["text"]) >= 40]
    title = _text(page.get("title"))
    dated = sum(
        bool(re.fullmatch(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", _text(value)))
        for value in index.values()
    )
    account_markers = {
        marker
        for marker in ("用户名", "密码", "验证码", "注册须知", "用户协议", "帐户", "账户")
        if any(marker in block["text"] for block in prose)
    }
    recognized = True
    if position_headers:
        role, ids = "article", position_headers[:8]
    elif len(account_markers) >= 2 and len(long_prose) <= 1:
        role, ids = "other", [block["id"] for block in prose[:6]]
    elif (
        len(links) >= 3
        and len(long_prose) <= 1
        and (dated >= 3 or re.search(r"新闻|动态|列表|公告", title))
    ):
        role, ids = "notice_list", [link["id"] for link in links[:8]]
    elif (
        substantial
        or (len(body) >= 2 and sum(len(b["text"]) for b in body) >= 100)
        or (
            len(body) >= 2
            and sum(len(b["text"]) for b in body) >= 40
            and any(
                region.get("kind") == "body"
                for region in page.get("structure", {}).get("regions", [])
            )
        )
    ):
        role, ids = "article", [block["id"] for block in prose[:6]]
    elif len(links) >= 3:
        role, ids = "navigation", [link["id"] for link in links[:6]]
    else:
        role, ids = "other", [block["id"] for block in prose[:6]]
        recognized = False
    structural_lists = [
        region
        for region in page.get("structure", {}).get("regions", [])
        if region.get("kind") == "list" and region.get("repeated_items", 0) >= 3
    ]
    roles = [role]
    if structural_lists and "notice_list" not in roles:
        roles.append("notice_list")
    return {
        "role": role,
        "roles": roles,
        "structural_lists": structural_lists,
        "evidence_ids": ids,
        "prose": prose,
        "links": links,
        "position_headers": position_headers,
        "recognized": recognized,
    }


def feature_text(bundle):
    shape = page_shape(bundle)
    title = _text(bundle.get("page", {}).get("title"))
    prose = "\n".join(block["text"] for block in shape["prose"])
    labels = "\n".join(link["label"] for link in shape["links"][:30])

    # Features are bounded, unlike source data. Negative decisions still face
    # the host's checks over the full saved page, not just this model window.
    def bucket(count):
        return "zero" if count == 0 else "one" if count == 1 else "few" if count < 6 else "many"

    page = bundle.get("page", {})
    # Structural observations, NOT the role predicted by a heuristic or source identity.
    # Unit names in original titles remain context; URLs, emails and raw identifiers do not.
    structure = " ".join(
        [
            "links_" + bucket(len(shape["links"])),
            "tables_" + bucket(len(page.get("tables", []))),
            "paragraphs_" + bucket(len(shape["prose"])),
            "body_" + ("short" if len(prose) < 160 else "medium" if len(prose) < 1200 else "long"),
            "position_headers_" + ("present" if shape["position_headers"] else "absent"),
            "pagination_"
            + ("present" if re.search(r"下一页|上一页|末页|next page", labels, re.I) else "absent"),
        ]
    )
    text = f"{structure}\n{title}\n{prose[:24000]}\n{labels[:6000]}"
    text = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[a-zA-Z]+", " [reference] ", text)
    return re.sub(r"\d+", "0", text).casefold()


def content_group(bundle):
    """Group equivalent content across URLs before training/evaluation splits."""
    text = re.sub(r"\s+", " ", feature_text(bundle)).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
