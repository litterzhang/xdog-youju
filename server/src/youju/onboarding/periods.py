"""Versioned application rules, independent evidence checks and explicit-clock evaluation.

Adapters supply rules, never expiry decisions. Unsupported semantics abstain. This
module has no I/O, clock, provider, workflow or source-specific execution authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

PERIOD_CONTRACT = "application-period-v2"
EVALUATOR_VERSION = "application-evaluator-v3.0"
_IDS = {
    "type": "array",
    "maxItems": 128,
    "uniqueItems": True,
    "items": {"type": "string", "maxLength": 512},
}
WINDOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "scope",
        "target_ids",
        "end",
        "precision",
        "timezone",
        "boundary",
        "text",
        "evidence_ids",
    ],
    "properties": {
        "scope": {"enum": ["notice", "positions", "round"]},
        "target_ids": _IDS,
        "end": {"type": "string", "maxLength": 64},
        "precision": {"enum": ["day", "minute", "second"]},
        "timezone": {"const": "Asia/Shanghai"},
        "boundary": {"enum": ["inclusive", "exclusive"]},
        "text": {"type": "string", "minLength": 1, "maxLength": 8192},
        "evidence_ids": {**_IDS, "minItems": 1},
    },
}
PERIOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "kind",
        "text",
        "evidence_ids",
        "material_refs",
        "windows",
        "coverage",
        "conflicts",
    ],
    "properties": {
        "schema_version": {"const": PERIOD_CONTRACT},
        "kind": {
            "enum": [
                "fixed_deadline",
                "open_ended",
                "until_filled",
                "scoped",
                "relative",
                "conflicting",
                "not_stated",
                "unknown",
            ]
        },
        "text": {"type": ["string", "null"], "maxLength": 8192},
        "evidence_ids": _IDS,
        "material_refs": {
            "type": "array",
            "maxItems": 128,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evidence_id", "version_id"],
                "properties": {
                    "evidence_id": {"type": "string"},
                    "version_id": {"type": "string", "minLength": 1},
                },
            },
        },
        "windows": {"type": "array", "maxItems": 100, "items": WINDOW_SCHEMA},
        "coverage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["all_scopes", "unresolved"],
            "properties": {"all_scopes": {"type": "boolean"}, "unresolved": _IDS},
        },
        "conflicts": _IDS,
    },
}


PERIOD_SCHEMA["properties"]["derivation"] = {
    "type": "array",
    "maxItems": 100,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount", "unit", "counting", "anchor", "calendar_ref", "rule_evidence_id"],
        "properties": {
            "amount": {"type": "integer", "minimum": 1, "maximum": 999},
            "unit": {"enum": ["calendar_days", "workdays"]},
            "counting": {"const": "exclude_anchor_day"},
            "anchor": {
                "type": "object",
                "additionalProperties": False,
                "required": ["on", "evidence_id", "version_id", "kind"],
                "properties": {
                    "on": {"type": "string", "format": "date"},
                    "evidence_id": {"type": "string"},
                    "version_id": {"type": "string"},
                    "kind": {"const": "publication"},
                },
            },
            "calendar_ref": {"type": ["string", "null"], "maxLength": 64},
            "rule_evidence_id": {"type": "string"},
        },
    },
}
PERIOD_SCHEMA["properties"]["context_refs"] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"calendar_ref": {"type": ["string", "null"], "maxLength": 64}},
}

PERIOD_SCHEMA["properties"]["context_refs"]["properties"]["change_ref"] = {
    "type": ["string", "null"],
    "maxLength": 64,
}
PERIOD_SCHEMA["properties"]["amendment"] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "review_id",
        "effect",
        "target_bundle_id",
        "event_bundle_id",
        "supersedes",
        "previous_period_digest",
    ],
    "properties": {
        "review_id": {"type": "string", "maxLength": 64},
        "effect": {"enum": ["extension", "cancellation"]},
        "target_bundle_id": {"type": "string"},
        "event_bundle_id": {"type": "string"},
        "supersedes": {"type": ["string", "null"]},
        "previous_period_digest": {"type": "string"},
    },
}


def _bindings(bundle):
    from .toolkit import documents

    return {
        block["id"]: doc.get("version_id")
        for doc in documents(bundle)
        for block in doc.get("blocks", [])
    }


def _boundary(window):
    """Include the complete stated precision unit, never guess an office hour."""
    precision = window["precision"]
    if precision == "day":
        end = datetime.combine(
            datetime.strptime(window["end"], "%Y-%m-%d").date(),
            time(),
            ZoneInfo(window["timezone"]),
        )
    else:
        pattern = "%Y-%m-%dT%H:%M" + (":%S" if precision == "second" else "")
        end = datetime.strptime(window["end"], pattern).replace(tzinfo=ZoneInfo(window["timezone"]))
    if window["boundary"] == "inclusive":
        end += {
            "day": timedelta(days=1),
            "minute": timedelta(minutes=1),
            "second": timedelta(seconds=1),
        }[precision]
    return end


def rule_key(bundle, *, adapter_digest, association_digest="none", model_profile="none"):
    """No clock in extraction identity. Entire pinned bundle/dependencies invalidate it."""
    payload = [bundle, adapter_digest, association_digest, model_profile, EVALUATOR_VERSION]
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def extract_application_period(bundle):
    """Reviewed generic extraction. Source adapters can extend syntax, not semantics.

    Registration rules come from available literal evidence. Acquisition flags
    never change their meaning; actual scope/authority/anchor conflicts do.
    Relative/calendar and associated amendments retain independent checks.
    """
    if bundle.get("period_context", {}).get("change"):
        from .amendments import reviewed_period

        return reviewed_period(bundle)
    from .toolkit import (
        APPLICATION,
        CUTOFF,
        NON_APPLICATION,
        PER_JOB,
        REVISION,
        ROLLING,
        _cell_ids,
        _date_matches,
        _deadline_analysis,
        _deadline_blocks,
        _headers,
        application_period_hint,
        documents,
    )

    hint = application_period_hint(bundle)
    period = {
        "schema_version": PERIOD_CONTRACT,
        **hint,
        "material_refs": [],
        "windows": [],
        "coverage": {"all_scopes": False, "unresolved": []},
        "conflicts": [],
    }
    baseline_candidates, reasons, _ = _deadline_analysis(bundle)
    # Material completeness is not temporal uncertainty. Reinspect actual clauses
    # below; the legacy baseline's whole-block purpose/no-date flags are not truth.
    unresolved = [
        reason
        for reason in reasons
        if reason
        not in {
            "unsupported_deadline_time",
            "no_explicit_application_deadline",
            "ambiguous_date_purpose",
            "missing_or_conflicting_deadline_dates",
            "revised_or_per_position_period",
            "page_incomplete",
        }
    ]
    from .relative_periods import RELATIVE_RE, relative_windows

    relative_blocks, derived_windows, derivation, relative_issues = relative_windows(bundle)
    relative_ids = {block["id"] for block in relative_blocks}
    if relative_blocks:
        period["kind"] = "relative"
        text = relative_blocks[0]["text"]
        match = RELATIVE_RE.search(text)
        period["text"] = text[max(0, match.start() - 200) : max(0, match.start() - 200) + 8192]
        period["evidence_ids"] = [relative_blocks[0]["id"]]
        period["derivation"] = derivation
        period["windows"].extend(derived_windows)
        unresolved.extend(relative_issues)
        if any(
            rule["unit"] == "工作日"
            for block in relative_blocks
            for rule in RELATIVE_RE.finditer(block["text"])
        ):
            period["context_refs"] = {
                "calendar_ref": bundle.get("period_context", {})
                .get("calendar", {})
                .get("review_id")
            }
        if derived_windows and not relative_issues:
            unresolved = [
                reason for reason in unresolved if reason != "missing_or_conflicting_deadline_dates"
            ]
    for doc in documents(bundle):
        cells = {
            key
            for table in doc.get("tables", [])
            for row in table.get("cell_evidence", [])
            for key in row
        }
        blocks = list(_deadline_blocks(doc))
        for number, (block, text, context) in enumerate(blocks):
            if block["id"] in relative_ids or block["id"] in cells:
                continue
            # A heading's immediate value is processed with both original IDs.
            if number + 1 < len(blocks) and blocks[number + 1][2] is block:
                continue
            from .timing_materials import common_scope, notification_after_cutoff

            if (
                len(text) <= 80
                and APPLICATION.search(text)
                and PER_JOB.search(text)
                and not common_scope(text)
            ):
                unresolved.append("position_or_round_scope")
            clauses = (
                [text]
                if context
                else re.split(
                    r"[。；;]|[，,](?=\s*(?:面试|笔试|资格审查|缴费|到岗|入职|报名截止后))", text
                )
            )
            for clause in clauses:
                if (
                    APPLICATION.search(clause)
                    and REVISION.search(clause)
                    and re.search(r"时间|日期|截止|期限|延期|延长|原定", clause)
                ):
                    unresolved.append("association_or_rolling_rule")
                    continue
                if not APPLICATION.search(clause) or not CUTOFF.search(clause):
                    continue
                if notification_after_cutoff(clause):
                    continue
                if re.search(r"并非|不是|不再是|不为|误写为|误标为", clause):
                    unresolved.append("negated_application_deadline")
                    continue
                if re.search(r"(?:但|但是|除外情况为|另有安排为)\s*[，,：:]?\s*$", clause):
                    unresolved.append("unfinished_registration_exception")
                if (PER_JOB.search(clause) and not common_scope(clause)) or re.search(
                    r"本轮|第[一二三四五六七八九十0-9]+[轮批]|分批", clause
                ):
                    unresolved.append("position_or_round_scope")
                    continue
                if REVISION.search(clause) or ROLLING.search(clause):
                    unresolved.append("association_or_rolling_rule")
                    continue
                dates, invalid = _date_matches(clause)
                purpose = clause[: dates[0][1].end()] if dates else clause
                purpose = re.split(r"[，,]", purpose)[-1]
                if dates and NON_APPLICATION.search(purpose):
                    # Interview/arrival/credential dates are not application dates;
                    # they also do not invalidate a separate explicit application rule.
                    continue
                if (
                    len(dates) == 2
                    and re.fullmatch(
                        r"\s*(?:至|到|[-—~～])\s*",
                        clause[dates[0][1].end() : dates[1][1].start()],
                    )
                    and dates[0][0] <= dates[1][0]
                ):
                    dates = dates[1:]
                if invalid or len(dates) != 1:
                    unresolved.append("unsupported_or_multiple_dates")
                    continue
                day, match = dates[0]
                end, precision = day.isoformat(), "day"
                tail = clause[match.end() :]
                # A colon clock does not make an explicit timezone or meridiem
                # supported. Do not silently interpret it as Shanghai time;
                # timezone-like email components are not time declarations.
                if re.search(
                    r"(?<![\w@.+-])(?:UTC|GMT|am|pm)\b(?![@.])|时区|上午|下午|晚上",
                    tail,
                    re.I,
                ):
                    unresolved.append("unsupported_time")
                    continue
                clock = re.match(r"\s*(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?", tail)
                if clock:
                    hour, minute = int(clock[1]), int(clock[2])
                    second = int(clock[3]) if clock[3] else 0
                    if hour == 24 and minute == 0 and second == 0:
                        # Explicit end-of-day 24:00 is the next midnight, not
                        # one minute into the following registration day.
                        clock = None
                    elif hour > 23 or minute > 59 or second > 59:
                        unresolved.append("unsupported_time")
                        continue
                    if clock is not None:
                        end += f"T{hour:02}:{minute:02}"
                        precision = "minute"
                    if clock is not None and clock[3]:
                        end += f":{second:02}"
                        precision = "second"
                elif re.search(r"时|点|上午|下午|晚上|UTC|GMT|时区|\b(?:am|pm)\b", tail, re.I):
                    # Unrelated notification text after the date is not a clock;
                    # explicit unsupported time syntax still must not be rounded.
                    if re.search(
                        r"\d\s*[时点]|上午|下午|晚上|UTC|GMT|时区|\b(?:am|pm)\b", tail, re.I
                    ):
                        unresolved.append("unsupported_time")
                        continue
                ids = [*([context["id"]] if context else []), block["id"]]
                period["windows"].append(
                    {
                        "scope": "notice",
                        "target_ids": [],
                        "end": end,
                        "precision": precision,
                        "timezone": "Asia/Shanghai",
                        "boundary": "inclusive",
                        "text": clause[:8192],
                        "evidence_ids": ids,
                    }
                )
    scoped_windows = []
    table_complete = True
    for doc in documents(bundle):
        for table in doc.get("tables", []):
            fields, header = _headers(table)
            if "title" not in fields:
                continue
            rows = table.get("rows", [])
            if len(rows) <= header + 1:
                continue
            if "deadline_text" not in fields:
                # A separate recognized jobs table lacks complete deadline scope.
                table_complete = False
                continue
            title_col, deadline_col = fields.index("title"), fields.index("deadline_text")
            for number, row in enumerate(rows[header + 1 :], header + 1):
                if max(title_col, deadline_col) >= len(row) or not row[title_col].strip():
                    table_complete = False
                    continue
                text = row[deadline_col]
                dates, invalid = _date_matches(text)
                if (
                    invalid
                    or len(dates) != 1
                    or ROLLING.search(text)
                    or REVISION.search(text)
                    or NON_APPLICATION.search(text)
                    or re.search(
                        r"时|点|[:：]|上午|下午|UTC|GMT",
                        text[dates[0][1].end() :] if dates else text,
                    )
                ):
                    table_complete = False
                    continue
                targets = _cell_ids(table, number, title_col)
                ids = list(
                    dict.fromkeys(
                        [
                            *targets,
                            *_cell_ids(table, header, deadline_col),
                            *_cell_ids(table, number, deadline_col),
                        ]
                    )
                )
                scoped_windows.append(
                    {
                        "scope": "positions",
                        "target_ids": targets,
                        "end": dates[0][0].isoformat(),
                        "precision": "day",
                        "timezone": "Asia/Shanghai",
                        "boundary": "inclusive",
                        "text": text,
                        "evidence_ids": ids,
                    }
                )
    if scoped_windows:
        period["windows"].extend(scoped_windows)
        if table_complete:
            unresolved = [reason for reason in unresolved if reason != "per_position_deadlines"]
        else:
            unresolved.append("incomplete_position_scope")
    if not period["windows"] and hint["kind"] == "fixed_deadline" and not unresolved:
        for item in baseline_candidates:
            ids = [*item.get("context_ids", []), item["id"]]
            period["windows"].append(
                {
                    "scope": "notice",
                    "target_ids": [],
                    "end": item["day"].isoformat(),
                    "precision": "day",
                    "timezone": "Asia/Shanghai",
                    "boundary": "inclusive",
                    "text": item["text"],
                    "evidence_ids": ids,
                }
            )
    if period["windows"]:
        distinct = {(w["end"], w["precision"]) for w in period["windows"] if w["scope"] == "notice"}
        period["kind"] = (
            "conflicting"
            if len(distinct) > 1
            else "scoped"
            if scoped_windows
            else "relative"
            if relative_blocks
            else "fixed_deadline"
        )
        if len(distinct) > 1:
            period["conflicts"].append("multiple_notice_cutoffs")
        period["text"] = period["windows"][0]["text"]
        period["evidence_ids"] = list(
            dict.fromkeys(key for window in period["windows"] for key in window["evidence_ids"])
        )
    if not period["windows"] and period["kind"] in {"unknown", "not_stated"}:
        # A literal until-filled declaration is a known rule even when the
        # legacy helper called its absence of a calendar date a parse failure.
        from .toolkit import (
            _CLOSED_DECLARATION,
            _NON_RECRUITMENT_VALIDITY,
            _OPEN_ENDED,
            _UNTIL_FILLED,
            content_blocks,
        )

        declarations = []
        for doc in documents(bundle):
            table_ids = {
                key
                for table in doc.get("tables", [])
                for row in table.get("cell_evidence", [])
                for key in row
            }
            for block in content_blocks(doc):
                if block["id"] in table_ids:
                    continue
                for clause in re.split(r"[。；;]", block["text"]):
                    if _CLOSED_DECLARATION.search(clause) or _NON_RECRUITMENT_VALIDITY.search(
                        clause
                    ):
                        continue
                    kind = (
                        "until_filled"
                        if _UNTIL_FILLED.search(clause)
                        else ("open_ended" if _OPEN_ENDED.search(clause) else None)
                    )
                    if kind and not PER_JOB.search(clause):
                        declarations.append((kind, block))
        if declarations and len({kind for kind, _ in declarations}) == 1:
            kind, block = declarations[0]
            period.update(kind=kind, text=block["text"][:8192], evidence_ids=[block["id"]])
    if not period["windows"] and period["kind"] == "fixed_deadline":
        period["kind"] = "unknown"  # Recognized literal date, unsupported boundary/scope.
    if len(period["windows"]) > 100 or len(period["evidence_ids"]) > 128:
        period.update(
            kind="unknown", text=None, evidence_ids=[], windows=[], conflicts=["rule_limit"]
        )
        unresolved.append("rule_limit")
    bindings = _bindings(bundle)
    period["material_refs"] = [
        {"evidence_id": key, "version_id": bindings[key]}
        for key in period["evidence_ids"]
        if bindings.get(key)
    ]
    if any(not bindings.get(key) for key in period["evidence_ids"]):
        unresolved.append("missing_material_version")
    from .timing_materials import material_timing

    _, material_blockers = material_timing(bundle, period["windows"])
    unresolved.extend(material_blockers)
    period["coverage"] = {
        "all_scopes": bool(period["windows"]) and not unresolved,
        "unresolved": list(dict.fromkeys(unresolved)),
    }
    return period


def validate_period(bundle, period):
    """Check references AND independent registration-purpose/scope observations.

    An adapter may not remove conflicts/unknown scopes found by generic inspection.
    Scope/relative extensions need a reviewed host checker before they authorize
    expiry; ID existence or self-reported coverage is never sufficient.
    """
    from .toolkit import evidence_index
    from .xdog import ModelError, check_schema

    try:
        check_schema(period, PERIOD_SCHEMA)
    except ModelError as exc:
        return [{"code": "PERIOD_SCHEMA", "message": str(exc)}]
    errors = []

    def issue(code, message, path="validity.application_period"):
        errors.append({"code": code, "message": message, "path": path})

    index, bindings = evidence_index(bundle), _bindings(bundle)
    refs = {ref["evidence_id"]: ref["version_id"] for ref in period["material_refs"]}
    all_ids = set(period["evidence_ids"])
    if len(refs) != len(period["material_refs"]) or set(refs) != all_ids:
        issue("PERIOD_VERSION", "Every rule reference needs exactly one pinned material version")
    for key in all_ids:
        if key not in index or not bindings.get(key) or refs.get(key) != bindings.get(key):
            issue("PERIOD_VERSION", "Unknown evidence or mismatched material version")
    if period["text"] is not None and not any(
        period["text"] in index.get(key, "") for key in all_ids
    ):
        # A paired heading/value may be represented as original consecutive blocks.
        if period["text"] != "\n".join(index.get(key, "") for key in period["evidence_ids"]):
            issue("PERIOD_QUOTE", "Rule text must quote pinned material literally")
    generic = extract_application_period(bundle)
    if period.get("amendment") != generic.get("amendment"):
        issue(
            "PERIOD_ASSOCIATION",
            "Amendments require the host's exact reviewed target/event association",
        )
    if period.get("derivation", []) != generic.get("derivation", []) or period.get(
        "context_refs", {}
    ) != generic.get("context_refs", {}):
        issue(
            "PERIOD_DERIVATION",
            "Relative anchors, counting and reviewed calendar must match saved evidence",
        )
    if generic["kind"] == "relative" and period["kind"] not in {"relative", "unknown"}:
        issue("PERIOD_KIND", "A derived registration deadline is not a literal fixed date")
    if period["coverage"]["all_scopes"] and not generic["coverage"]["all_scopes"]:
        issue("PERIOD_COVERAGE", "Whole-scope coverage is not independently supported")
    if period["coverage"]["all_scopes"] and period["windows"] != generic["windows"]:
        issue("PERIOD_COVERAGE", "Adapter must retain every independently supported scope/window")
    if set(generic["conflicts"]) - set(period["conflicts"]):
        issue("PERIOD_CONFLICT", "Adapter omitted independently observed conflicts")
    for number, window in enumerate(period["windows"]):
        path = f"validity.application_period.windows[{number}]"
        try:
            _boundary(window)
        except (TypeError, ValueError, OverflowError):
            issue("PERIOD_DATE", "Invalid deadline or precision", path)
        if not set(window["evidence_ids"]) <= all_ids:
            issue("PERIOD_REFERENCE", "Window references must belong to the rule", path)
        matching = [
            w
            for w in generic["windows"]
            if all(
                window[key] == w[key]
                for key in (
                    "scope",
                    "target_ids",
                    "end",
                    "precision",
                    "timezone",
                    "boundary",
                    "text",
                    "evidence_ids",
                )
            )
        ]
        if not matching:
            issue(
                "PERIOD_SEMANTICS",
                "Deadline purpose, scope or boundary not independently supported",
                path,
            )
    if period["kind"] == "fixed_deadline" and not period["windows"]:
        issue("PERIOD_EMPTY", "A fixed deadline needs at least one supported window")
    if period["kind"] in {"open_ended", "until_filled", "not_stated"} and (
        period["kind"] != generic["kind"] or period["windows"]
    ):
        issue("PERIOD_KIND", "Declaration does not agree with the independently observed rule")
    return errors


def evaluate_period(bundle, period, now_iso):
    """Recompute time from a validated rule; no provider and no machine-date fallback."""
    from .toolkit import material_issues

    errors = validate_period(bundle, period)
    try:
        now = datetime.fromisoformat(now_iso)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Timezone-aware clock required")
    except (TypeError, ValueError):
        raise ValueError("An explicit timezone-aware evaluation clock is required") from None
    issues = material_issues(bundle)
    from .timing_materials import material_timing

    impact, material_blockers = material_timing(bundle, period.get("windows", []))
    blockers = list(
        dict.fromkeys(
            [
                *period.get("coverage", {}).get("unresolved", []),
                *period.get("conflicts", []),
                *material_blockers,
            ]
        )
    )
    kind = period.get("kind")
    result = {
        "status": "unknown",
        "deadline": None,
        "evidence_ids": period.get("evidence_ids", []),
        "reason": "Unresolved registration scopes; full extraction required",
        "basis": "rolling"
        if kind in {"open_ended", "until_filled"}
        else "not_stated"
        if kind == "not_stated"
        else "per_position"
        if kind == "scoped"
        else "conflicting"
        if period.get("conflicts")
        else "unsupported",
        "verification_scope": "saved_notice",
        "material_timing_impact": impact,
        "timing_blockers": blockers,
        "scope": "available_materials" if issues else "complete_materials",
        "material_issues": issues,
        "application_period": period,
        "current_hiring_status": "not_verified",
        "coarse_eligible": False,
        "evaluated_at": now.isoformat(),
        "evaluator_version": EVALUATOR_VERSION,
    }
    if errors:
        return result, errors
    if kind in {"open_ended", "until_filled"}:
        result["reason"] = (
            "The saved notice declares an open-ended or until-filled rule, not an unknown "
            "date extraction; there is no calendar expiry and current hiring is not verified"
        )
    elif kind == "not_stated":
        result["reason"] = "The saved notice states no supported registration cutoff"
    if (
        period.get("amendment", {}).get("effect") == "cancellation"
        and period["coverage"]["all_scopes"]
        and not period["coverage"]["unresolved"]
    ):
        result.update(
            disposition="cancelled",
            basis="event",
            reason="Reviewed notice-wide cancellation; not ordinary clock expiry",
        )
        return result, []
    windows = period["windows"]
    if (
        period["kind"] not in {"fixed_deadline", "scoped", "relative"}
        or not windows
        or period["conflicts"]
        or not period["coverage"]["all_scopes"]
        or period["coverage"]["unresolved"]
    ):
        return result, []
    end = max(_boundary(w) for w in windows)
    expired = now >= end
    result.update(
        status="expired" if expired else "not_expired",
        deadline=max(w["end"][:10] for w in windows),
        basis="amended_deadline"
        if period.get("amendment")
        else "derived_deadline"
        if period.get("derivation")
        else "explicit_deadline",
        coarse_eligible=expired,
        reason="The saved notice's supported registration rule is evaluated with an explicit "
        "clock. Acquisition completeness is a separate diagnostic, not a date veto; "
        "current hiring and subsequent amendments remain unverified",
    )
    return result, []
