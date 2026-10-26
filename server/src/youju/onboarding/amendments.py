"""Literal notice-wide amendment semantics over independently associated saved material.

The host store assembles association context. An adapter cannot add it to the
parent's immutable bundle. Partial scopes, extra job changes and stale material
never authorize whole-notice expiry or cancellation.
"""

import copy
import re

_SCOPE = re.compile(r"本次招聘|本招聘|原招聘公告|全部岗位|所有岗位")
_EXTENSION = re.compile(r"报名(?:截止(?:日期|时间)?)?(?:统一)?(?:延长|延期)(?:至|到)\s*")
_CANCEL = re.compile(
    r"(?:本次招聘|原招聘公告|全部岗位|所有岗位).{0,12}(?:取消|终止|撤销|停止)"
    r"|(?:取消|终止|撤销|停止).{0,4}(?:本次招聘|原招聘公告|全部岗位|所有岗位)"
)
_OTHER_CHANGES = re.compile(r"(?:人数|条件|要求|专业|学历|岗位).{0,4}(?:变更|调整|增加|减少|取消)")


def base_view(bundle):
    value = copy.deepcopy(bundle)
    value["attachments"] = [
        doc for doc in value.get("attachments", []) if not doc.get("change_review_id")
    ]
    context = value.get("period_context", {})
    context.pop("change", None)
    if not context:
        value.pop("period_context", None)
    return value


def amendment_observation(document, effect):
    from .toolkit import NON_APPLICATION, PER_JOB, ROLLING, _date_matches, content_blocks

    if document.get("tables"):
        return None  # This narrow amendment grammar does not infer table scopes.
    # Capture/reader completeness is separate from literal available semantics.
    blocks = content_blocks(document)
    if any(
        _OTHER_CHANGES.search(block.get("text", ""))
        or PER_JOB.search(block.get("text", ""))
        or re.search(
            r"仅(?:针对|适用|限)|如果|一旦|倘若|如若|(?:若|如).{0,20}(?:取消|终止|报名|延期|延长)",
            block.get("text", ""),
        )
        for block in blocks
    ):
        return None  # A separate heading/conditional cannot be discarded from scope.

    if (effect == "extension" and any(_CANCEL.search(b.get("text", "")) for b in blocks)) or (
        effect == "cancellation" and any(_EXTENSION.search(b.get("text", "")) for b in blocks)
    ):
        return None  # Conflicting effects need review, not timestamp/adapter precedence.
    found = []
    for block in blocks:
        text = block.get("text", "")
        if (
            not _SCOPE.search(text)
            or PER_JOB.search(text)
            or NON_APPLICATION.search(text)
            or ROLLING.search(text)
        ):
            continue
        if effect == "cancellation":
            if _CANCEL.search(text) and not re.search(
                r"未取消|不取消|暂不|可能|拟取消|如.*取消", text
            ):
                found.append({"text": text, "evidence_id": block["id"], "end": None})
        else:
            markers = list(_EXTENSION.finditer(text))
            if len(markers) != 1:
                continue
            tail = text[markers[0].end() :]
            dates, invalid = _date_matches(tail)
            if invalid or len(dates) != 1 or dates[0][1].start() != 0:
                continue
            if tail[dates[0][1].end() :].strip(" 。；;，,\t\n"):
                continue
            found.append({"text": text, "evidence_id": block["id"], "end": dates[0][0].isoformat()})
    if len(found) != 1 or len(found[0]["text"]) > 8192:
        return None
    return found[0]


def reviewed_period(bundle):
    from .periods import _bindings, extract_application_period
    from .store import digest

    review = bundle["period_context"]["change"]
    original = extract_application_period(base_view(bundle))
    value = copy.deepcopy(original)
    value.setdefault("context_refs", {})["change_ref"] = review["review_id"]
    value["amendment"] = {
        key: review.get(key)
        for key in ("review_id", "effect", "target_bundle_id", "event_bundle_id", "supersedes")
    }
    value["amendment"]["previous_period_digest"] = digest(original)
    if review["status"] != "current":
        value["coverage"] = {
            "all_scopes": False,
            "unresolved": ["reviewed_change_material_is_stale"],
        }
        return value
    documents = [
        doc
        for doc in bundle.get("attachments", [])
        if doc.get("change_review_id") == review["review_id"]
    ]
    observation = (
        amendment_observation(documents[0], review["effect"]) if len(documents) == 1 else None
    )
    if observation is None:
        value["coverage"] = {
            "all_scopes": False,
            "unresolved": ["reviewed_change_semantics_unsupported"],
        }
        return value
    ids = list(dict.fromkeys([*original["evidence_ids"], observation["evidence_id"]]))
    bindings = _bindings(bundle)
    value.update(
        text=observation["text"],
        evidence_ids=ids,
        material_refs=[
            {"evidence_id": key, "version_id": bindings[key]} for key in ids if bindings.get(key)
        ],
        conflicts=[],
        windows=[],
    )
    value.pop("derivation", None)
    if review["effect"] == "cancellation":
        value.update(kind="unknown", coverage={"all_scopes": True, "unresolved": []})
        return value
    if (
        original["kind"] not in {"fixed_deadline", "relative"}
        or not original["windows"]
        or any(w["scope"] != "notice" for w in original["windows"])
        or observation["end"] < max(w["end"][:10] for w in original["windows"])
    ):
        value["coverage"] = {
            "all_scopes": False,
            "unresolved": ["extension_base_or_scope_unresolved"],
        }
        value["kind"] = "unknown"
        return value
    value.update(
        kind="fixed_deadline",
        windows=[
            {
                "scope": "notice",
                "target_ids": [],
                "end": observation["end"],
                "precision": "day",
                "timezone": "Asia/Shanghai",
                "boundary": "inclusive",
                "text": observation["text"],
                "evidence_ids": [observation["evidence_id"]],
            }
        ],
    )
    return value
