"""Capability validation for editable source graphs, before the SDK can load code."""

from youju.positions.drafts import DRAFT_SCHEMA

from .workflow import port

OPERATIONS = {
    "parse": {"runtime:review", "recipe:parse", "recipe:undated", "runtime:script_failed"},
    "detail": {"runtime:inspect", "runtime:model_input", "runtime:merge"},
    "finish": {"runtime:normalize"},
}


def validate_graph(value):
    if not isinstance(value, dict):
        raise ValueError("Workflow must be an inline object")
    nodes = value.get("nodes")
    if (
        not isinstance(nodes, list)
        or sorted(n.get("id", "") for n in nodes if isinstance(n, dict))
        != ["detail", "finish", "parse"]
        or len(nodes) != 3
    ):
        raise ValueError("The main Flow has exactly parse/detail/finish subflow nodes")
    if value.get("state") != {"packet": {}}:
        raise ValueError("Main Flow must preserve pinned runtime input")
    count = 0

    def visit(flow, phase, depth):
        nonlocal count
        if depth > 4 or not isinstance(flow, dict):
            raise ValueError("Only bounded inline subflows are supported")
        if set(flow) - {"name", "provider", "state", "nodes", "edges", "entry", "defaults"}:
            raise ValueError("Workflow cannot add tools, scheduling or execution capabilities")
        if flow.get("provider", "runtime-bound") != "runtime-bound":
            raise ValueError("Model provider is bound by the host")
        if flow.get("defaults", {}) not in ({}, {"model": "runtime:large"}):
            raise ValueError("Model selection is bound by the host")
        if not isinstance(flow.get("name"), str) or not 0 < len(flow["name"]) <= 200:
            raise ValueError("Workflow name must be bounded text")
        children = flow.get("nodes")
        if not isinstance(children, list):
            raise ValueError("Workflow nodes must be a list")
        for node in children:
            count += 1
            if count > 64 or not isinstance(node, dict):
                raise ValueError("Recipe graph exceeds the bounded node contract")
            if not isinstance(node.get("id"), str) or not 0 < len(node["id"]) <= 100:
                raise ValueError("Node IDs must be bounded text")
            kind = node.get("type")
            if kind == "subflow":
                if set(node) - {"id", "type", "subflow"}:
                    raise ValueError(
                        "Subflow ports are derived; arbitrary capabilities are forbidden"
                    )
                if not isinstance(node.get("subflow"), dict):
                    raise ValueError("Subflow paths are forbidden; submit inline artifacts")
                visit(node["subflow"], node["id"] if depth == 0 else phase, depth + 1)
                continue
            if depth == 0:
                raise ValueError("Main nodes must be native subflows")
            if kind == "script":
                if set(node) - {"id", "type", "run", "inputs", "outputs", "deterministic"}:
                    raise ValueError("Scripts may use only approved isolated operation references")
                ref = node.get("run")
                if ref not in OPERATIONS[phase]:
                    raise ValueError(f"Unapproved {phase} operation: {ref}")
                expected_inputs = [port("packet")]
                if ref == "runtime:merge":
                    expected_inputs.append(port("detailed"))
                expected_outputs = [port("result" if ref == "runtime:normalize" else "packet")]
                if ref == "runtime:model_input":
                    expected_outputs.append(port("payload"))
                if node.get("inputs") != expected_inputs or node.get("outputs") != expected_outputs:
                    raise ValueError("Operation ports cannot alter host input/output boundaries")
            elif kind == "agent":
                if phase != "detail" or set(node) - {
                    "id",
                    "type",
                    "model",
                    "inputs",
                    "outputs",
                    "system_prompt",
                    "prompt",
                }:
                    raise ValueError("Only read-only SDK extraction agents are allowed in detail")
                if any(not isinstance(node.get(k, ""), str) for k in ("prompt", "system_prompt")):
                    raise ValueError("Agent prompts must be text")
                prompt = node.get("prompt", "")
                system = node.get("system_prompt", "")
                if (
                    len(prompt.encode("utf-8")) > 16000
                    or len(system.encode("utf-8")) > 32000
                    or prompt.count("{{payload}}") != 1
                    or "{{" in prompt.replace("{{payload}}", "")
                    or "{{" in system
                ):
                    raise ValueError("Bounded prompts must include the pinned payload exactly once")
                if (
                    node.get("model") != "runtime:large"
                    or node.get("inputs") != [port("payload")]
                    or node.get("outputs") != [port("draft", DRAFT_SCHEMA)]
                ):
                    raise ValueError(
                        "Extraction agents must use host model and the canonical draft"
                    )
            else:
                raise ValueError("Only script/agent/inline subflow nodes are allowed")
        for item in flow.get("edges", []):
            if not isinstance(item, dict) or set(item) - {"from", "to", "map", "when"}:
                raise ValueError("Only explicit finite edges and conditions are allowed")

    visit(value, None, 0)
