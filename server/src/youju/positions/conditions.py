"""Typed, source-neutral recruitment conditions and conservative text helpers."""

import re

EDUCATION_LEVELS = frozenset(
    {
        "primary",
        "junior_secondary",
        "senior_secondary",
        "vocational",
        "associate",
        "bachelor",
        "postgraduate",
        "master",
        "doctorate",
        "unrestricted",
    }
)
DEGREE_LEVELS = frozenset({"bachelor", "master", "doctorate", "unrestricted"})
CONDITION_VALUE_SCHEMAS = {
    "education": {"level": {"enum": sorted(EDUCATION_LEVELS)}},
    "degree": {"level": {"enum": sorted(DEGREE_LEVELS)}},
    "study_form": {"form": {"enum": ["full_time", "part_time", "unrestricted"]}},
    "major": {
        "names": {"type": "array", "items": {"type": "string"}},
        "unrestricted": {"type": "boolean"},
    },
    "age": {
        "minimum": {"type": ["integer", "null"], "minimum": 0},
        "maximum": {"type": ["integer", "null"], "minimum": 0},
        "reference_date": {"type": ["string", "null"]},
    },
    "work_experience": {
        "minimum_years": {"type": ["number", "null"], "minimum": 0},
        "kind": {"type": ["string", "null"]},
    },
    "graduation_status": {"status": {"type": "string"}},
    "graduation_year": {"years": {"type": "array", "items": {"type": "integer"}}},
    "political_status": {"statuses": {"type": "array", "items": {"type": "string"}}},
    "license": {"names": {"type": "array", "items": {"type": "string"}}},
    "language": {"name": {"type": "string"}, "requirement": {"type": "string"}},
    "other": {"text": {"type": "string", "minLength": 1}},
}


def value_schema(kind):
    properties = CONDITION_VALUE_SCHEMAS[kind]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def validate_condition_value(kind, value):
    """No coercion: malformed typed requirements cannot become eligibility rules."""
    if value is None:
        return
    spec = CONDITION_VALUE_SCHEMAS[kind]
    if not isinstance(value, dict) or set(value) != set(spec):
        raise ValueError(f"{kind} requires typed keys {sorted(spec)}")
    for key, rule in spec.items():
        item = value[key]
        if "enum" in rule:
            if not isinstance(item, str) or item not in rule["enum"]:
                raise ValueError(f"Unsupported {kind}.{key}")
            continue
        types = rule["type"]
        types = [types] if isinstance(types, str) else types
        if item is None and "null" in types:
            continue
        valid = (
            ("string" in types and isinstance(item, str) and bool(item.strip()))
            or ("integer" in types and type(item) is int)
            or ("number" in types and type(item) in {int, float})
            or ("boolean" in types and type(item) is bool)
            or ("array" in types and isinstance(item, list))
        )
        if not valid:
            raise ValueError(f"Invalid {kind}.{key}")
        if "minimum" in rule and item < rule["minimum"]:
            raise ValueError(f"Negative {kind}.{key}")
        if isinstance(item, list):
            element = rule["items"]["type"]
            if (
                (not item and kind != "major")
                or len(item) > 200
                or any(
                    not (isinstance(x, str) and x.strip())
                    if element == "string"
                    else type(x) is not int or not 1900 <= x <= 2200
                    for x in item
                )
            ):
                raise ValueError(f"Invalid {kind}.{key} entries")
    if kind == "major" and not value["names"] and not value["unrestricted"]:
        raise ValueError("A restricted major requires names")
    if kind == "age":
        from datetime import date

        if value["reference_date"] is not None:
            if date.fromisoformat(value["reference_date"]).isoformat() != value["reference_date"]:
                raise ValueError("Age reference date must be canonical")
        low, high = value["minimum"], value["maximum"]
        if low is None and high is None:
            raise ValueError("Age needs a bound")
        if low is not None and high is not None and low > high:
            raise ValueError("Reversed age bounds")
    if kind == "work_experience" and all(x is None for x in value.values()):
        raise ValueError("Experience needs years or kind")


def conditions_from_texts(texts):
    """Recognize simple atoms; preserve exceptions/alternatives verbatim, never guess AND."""
    from .facts import Condition, TaggedField

    conditions, residual = [], []
    education = {
        "本科": "bachelor",
        "专科": "associate",
        "大专": "associate",
        "研究生": "postgraduate",
        "硕士研究生": "master",
        "博士研究生": "doctorate",
        "高中": "senior_secondary",
        "中专": "vocational",
    }
    degree = {"学士": "bachelor", "硕士": "master", "博士": "doctorate"}

    def add(kind, value, operator="equals"):
        conditions.append(
            Condition(f"c{len(conditions) + 1}", kind, operator, TaggedField(value, "source"))
        )

    for hint, text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        chunks = (
            [text]
            if hint == "major" or re.search(r"或|之一|除外|但|放宽|任一", text)
            else re.split(r"[；;\n，,]+", text)
        )
        for chunk in chunks:
            chunk = re.sub(r"^\s*\d+[.、．]\s*", "", chunk).strip().rstrip("。；;")
            if not chunk:
                continue
            compact = re.sub(r"\s+", "", chunk)
            edu = re.fullmatch(
                r"(全日制|非全日制)?(本科|专科|大专|硕士研究生|博士研究生|研究生|高中|中专)(?:学历)?(及以上|以上)?",
                compact,
            )
            deg = re.fullmatch(
                r"(?:具有|须具有|拥有)?(学士|硕士|博士)(?:学位)?(及以上|以上)?", chunk
            )
            exp = re.fullmatch(
                r"(?:具有|须有)?(\d+(?:\.\d+)?)年(?:及以上|以上)?(相关)?(?:工作)?(?:经验|经历)",
                compact,
            )
            age_max = re.fullmatch(r"(?:年龄)?(?:不超过|不大于|至多)(\d+)(?:周岁|岁)", compact)
            age_min = re.fullmatch(r"(?:年龄)?(?:年满|不低于|至少)(\d+)(?:周岁|岁)", compact)
            # Inclusive upper/lower bounds only. Ambiguous 以下/以上 and
            # exception clauses remain semantic work, not guessed exact limits.
            graduation = re.fullmatch(r"(?:限|须为|仅限)?(应届毕业生|应届生)", compact)
            grad_year = re.fullmatch(r"(20\d{2})届(?:应届)?毕业生", compact)
            political = re.fullmatch(r"(?:政治面貌[：:]?)?(中共党员|中共预备党员)", compact)
            if edu:
                if edu[1]:
                    add("study_form", {"form": "full_time" if edu[1] == "全日制" else "part_time"})
                add("education", {"level": education[edu[2]]}, "at_least" if edu[3] else "equals")
            elif deg and ("学位" in chunk or hint == "degree"):
                add("degree", {"level": degree[deg[1]]}, "at_least" if deg[2] else "equals")
            elif exp:
                add(
                    "work_experience",
                    {"minimum_years": float(exp[1]), "kind": "相关工作" if exp[2] else "工作"},
                    "at_least",
                )
            elif age_max or age_min:
                add(
                    "age",
                    {
                        "minimum": int(age_min[1]) if age_min else None,
                        "maximum": int(age_max[1]) if age_max else None,
                        "reference_date": None,
                    },
                    "at_most" if age_max else "at_least",
                )
            elif graduation:
                add("graduation_status", {"status": "应届毕业生"})
            elif grad_year:
                add("graduation_year", {"years": [int(grad_year[1])]})
            elif political:
                add("political_status", {"statuses": [political[1]]}, "in")
            elif hint == "major" and not re.search(r"或|除外|但|须|应|具有|且|优先|（|\(", chunk):
                unrestricted = chunk in {"不限", "专业不限", "不限专业"}
                add(
                    "major",
                    {
                        "names": []
                        if unrestricted
                        else [n.strip() for n in re.split(r"[、，,]+", chunk) if n.strip()],
                        "unrestricted": unrestricted,
                    },
                    "in",
                )
            else:
                residual.append(chunk)
    # Construct the residual atom at extraction time. This is not a rewrite of
    # arbitrary Recipe/model output or a runtime/frontend concatenation fallback.
    if residual:
        add("other", {"text": "\n".join(residual)}, "unspecified")
    logic = (
        {"operator": "all", "items": [{"condition_id": c.id} for c in conditions]}
        if conditions
        else None
    )
    return tuple(conditions), logic
