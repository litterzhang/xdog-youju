"""Acquisition-time DOM observations, not business labels or discovery permissions.

Only explicit auxiliary landmarks/names are excluded from prose. Unrecognized
containers remain visible. Repeated anchors supply independent list observations.
"""

import hashlib
import json
import re

from bs4 import Tag

REGION_VERSION = "dom-regions-v1"
_AUXILIARY = re.compile(r"(?:^|[-_])(?:related|recommend(?:ed|ation)?|sidebar)(?:$|[-_])", re.I)
_NAV = re.compile(r"(?:^|[-_])(?:nav|menu|navigation|navbar|menubar)(?:$|[-_]|\d)", re.I)


def region_kind(tag):
    for ancestor in (tag, *tag.parents):
        if not isinstance(ancestor, Tag):
            continue
        names = [ancestor.get("id", ""), *ancestor.get("class", [])]
        if (
            ancestor.name in {"nav", "header", "footer"}
            or ancestor.get("role") in {"navigation", "menu", "menubar"}
            or any(_NAV.search(name) for name in names)
        ):
            return "navigation"
        if (
            ancestor.name == "aside"
            or ancestor.get("role") == "complementary"
            or any(_AUXILIARY.search(name) for name in names)
        ):
            return "recommendation"
    return "content"


def observe_regions(soup, document, locator):
    """Bound structural regions and reference existing IDs; never reassign evidence."""
    regions = []
    candidates = soup.find_all(
        ["main", "article", "section", "div", "ul", "ol", "nav", "aside"], limit=513
    )
    for tag in candidates[:512]:
        path = locator(tag)
        kind = region_kind(tag)
        children = tag.find_all(recursive=False)
        anchors = []
        if kind == "content" and len(children) >= 3:
            for child in children[:101]:
                links = child.find_all("a", href=True)
                if child.name == "a" and child.get("href"):
                    links = [child]
                if (
                    len(links) == 1
                    and len(child.get_text(" ", strip=True)) <= 240
                    and len(links[0].get_text(" ", strip=True)) >= 8
                ):
                    anchors.append(links[0])
        repeated = len(anchors) >= 3
        if kind == "content" and not repeated and tag.name not in {"main", "article"}:
            continue
        ids = [
            b["id"]
            for b in document["blocks"]
            if b["locator"] == path or b["locator"].startswith(path + " > ")
        ]
        if not ids:
            continue
        regions.append(
            {
                "kind": "list" if repeated else "body" if kind == "content" else kind,
                "locator": path,
                "evidence_ids": ids[:128],
                "repeated_items": len(anchors),
                "truncated": len(ids) > 128 or len(children) > 100,
            }
        )
        if len(regions) >= 64:
            break
    shape = [
        (
            tag.name,
            tag.get("role", ""),
            sorted(tag.get("class", []))[:8],
            [child.name for child in tag.find_all(recursive=False)[:24]],
        )
        for tag in candidates[:128]
    ]
    return {
        "version": REGION_VERSION,
        "regions": regions,
        "structure_key": hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest(),
        "truncated": len(candidates) > 512 or len(regions) >= 64,
    }
