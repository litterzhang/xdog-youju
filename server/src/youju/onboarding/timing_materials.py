"""Separate missing extraction material from dependencies of a registration rule.

A saved primary notice is authoritative for its stated common closing rule. Missing
application forms/position requirements are not hypothetical date amendments.
Acquisition flags are diagnostics only. Only actual timing clauses can conflict.
"""

import re

_TIMING = re.compile(
    r"报名(?:截止|时间|期限|日期|安排)|应聘(?:截止|时间|期限)|截止(?:日期|时间|期)|报名日程"
)
_DELEGATED = re.compile(
    r"(?:以|见|详见|参见|另见).{0,35}(?:附件|附表|岗位表|时间表|日程)|(?:附件|附表|岗位表).{0,35}(?:为准|另定|另有|规定)"
)
_LATER = re.compile(r"另行(?:公布|通知|确定)|另有(?:安排|规定)|另定")


def notification_after_cutoff(text):
    """A result notification after closing is not another registration window."""
    from .toolkit import DATE_RE

    reference = re.search(
        r"(?:报名|应聘).{0,8}截止\s*(?:后|以后|之后).{0,60}(?:通知|公布|告知)", text
    )
    return bool(reference and not DATE_RE.search(text[: reference.start()]))


def delegated_timing(bundle):
    from .toolkit import content_blocks, documents

    blockers = []
    blocks = [block for doc in documents(bundle) for block in content_blocks(doc)]
    for block in blocks:
        for sentence in re.split(r"[。；;\n]", block.get("text", "")):
            previous = ""
            for clause in re.split(r"[，,]", sentence):
                if notification_after_cutoff(clause):
                    previous = ""
                    continue
                temporal = bool(_TIMING.search(clause))
                inherited = bool(
                    _TIMING.search(previous)
                    and re.match(r"\s*(?:具体|实际)?(?:以|详见|参见|另见|另行)", clause)
                )
                if (temporal or inherited) and (_DELEGATED.search(clause) or _LATER.search(clause)):
                    blockers.append("registration_time_delegated_or_pending")
                previous = clause
    return list(dict.fromkeys(blockers))


def material_timing(bundle, windows):
    """Return host policy diagnostics, not an adapter/model confidence score."""
    from .toolkit import material_issues

    page = bundle.get("page", {})
    missing = material_issues(bundle)
    blockers = delegated_timing(bundle)
    primary_ids = {block["id"] for block in page.get("blocks", [])}
    observed_annex_rule = any(
        w["scope"] == "notice" and not set(w["evidence_ids"]) <= primary_ids for w in windows
    )
    if observed_annex_rule:
        blockers = [b for b in blockers if b != "registration_time_delegated_or_pending"]
    # Never turn missing/truncated/failed acquisition metadata into a temporal
    # veto. With no readable cutoff, extraction naturally returns no window.
    return "non_blocking" if missing else "none", list(dict.fromkeys(blockers))


def common_scope(text):
    """Explicit uniform recruitment timing, not 'other/some jobs uniformly'."""
    if re.search(r"(?:其他|部分|个别|其余|不同).{0,4}(?:岗位|职位)|仅适用|除外", text):
        return False
    return bool(
        re.search(
            r"(?:所有|全部|各).{0,5}(?:岗位|职位).{0,24}(?:统一|一律|均为|相同)|"
            r"(?:本次招聘|本公告).{0,12}统一.{0,8}(?:报名|应聘)|"
            r"(?:所有|全部).{0,5}(?:岗位|职位).{0,8}(?:报名|应聘)",
            text,
        )
    )
