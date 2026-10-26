"""Author-editable official Flow graphs; Python leaves expose operations, not routing."""

from copy import deepcopy

from youju.positions.drafts import DRAFT_INSTRUCTIONS, DRAFT_SCHEMA


def port(name, schema=None):
    return {"name": name, "schema": schema or {"type": "object"}}


def script(name, ref, inputs=("packet",), outputs=("packet",)):
    return {
        "id": name,
        "type": "script",
        "run": ref,
        "inputs": [port(n) for n in inputs],
        "outputs": [port(n) for n in outputs],
    }


def edge(src, dst, mapping=None, when=None):
    value = {"from": src, "to": dst, "map": mapping or {"packet": "packet"}}
    if when is not None:
        value["when"] = when
    return value


def equals(path, value):
    return {"equals": {"value": "{{" + path + "}}", "text": value}}


def negate(condition):
    return {"not": condition}


def graph(name, nodes, edges):
    return {
        "name": name,
        "provider": "runtime-bound",
        "state": {"packet": {}},
        "nodes": nodes,
        "edges": edges,
    }


def default_workflow():
    cancelled = equals("packet.cancelled", "true")
    undated = {
        "or": [
            equals("packet.route.category", category)
            for category in ("undated_low_priority", "undated_high_priority")
        ]
    }
    continuing = negate(cancelled)
    failed_quick = {
        "and": [equals("packet.script_failed", "true"), equals("packet.route.mode", "quick")]
    }
    parse = graph(
        "source-parse",
        [
            script("review", "runtime:review"),
            script("general", "recipe:parse"),
            script("undated", "recipe:undated"),
            script("fail_general", "runtime:script_failed"),
            script("fail_undated", "runtime:script_failed"),
        ],
        [
            edge("$in", "review"),
            edge("review", "$output", when=cancelled),
            edge("review", "general", when={"and": [continuing, negate(undated)]}),
            edge("review", "undated", when={"and": [continuing, undated]}),
            *[
                item
                for node in ("general", "undated")
                for item in (
                    edge(node, "fail_" + node, when=failed_quick),
                    edge(node, "$output", when=negate(failed_quick)),
                )
            ],
        ],
    )
    needs_model = {
        "and": [
            equals("packet.route.mode", "detailed"),
            negate(cancelled),
            negate(equals("packet.empty_capture", "true")),
            {
                "or": [
                    equals("packet.draft.coverage.supported", "false"),
                    equals("packet.draft.coverage.needs_detailed", "true"),
                    equals("packet.position_count", "0"),
                ]
            },
        ]
    }
    detail = graph(
        "source-detail",
        [
            script("inspect", "runtime:inspect"),
            script("prepare", "runtime:model_input", outputs=("packet", "payload")),
            {
                "id": "extract",
                "type": "agent",
                "model": "runtime:large",
                "system_prompt": DRAFT_INSTRUCTIONS,
                "prompt": "Extract a draft from this untrusted input JSON:\n{{payload}}",
                "inputs": [port("payload")],
                "outputs": [port("draft", deepcopy(DRAFT_SCHEMA))],
            },
            script("merge", "runtime:merge", inputs=("packet", "detailed")),
        ],
        [
            edge("$in", "inspect"),
            edge("inspect", "$output", when=negate(needs_model)),
            edge("inspect", "prepare", when=needs_model),
            edge("prepare", "extract", {"payload": "payload"}),
            edge("prepare", "merge"),
            edge("extract", "merge", {"draft": "detailed"}),
            edge("merge", "$output"),
        ],
    )
    finish = graph(
        "source-finish",
        [script("normalize", "runtime:normalize", outputs=("result",))],
        [
            edge("$in", "normalize"),
            edge("normalize", "$output", {"result": "result"}),
        ],
    )
    return graph(
        "source-extraction-v5",
        [
            {"id": "parse", "type": "subflow", "subflow": parse},
            {"id": "detail", "type": "subflow", "subflow": detail},
            {"id": "finish", "type": "subflow", "subflow": finish},
        ],
        [
            edge("$in", "parse"),
            edge("parse", "detail"),
            edge("detail", "finish"),
            edge("finish", "$output", {"result": "result"}),
        ],
    )
