"""Canonical artifacts and approved official Flow: extraction, never cost-policy code."""

import ast
import hashlib
import json
from typing import Protocol

from .errors import RecipeError
from .workflow import default_workflow

RUNTIME_CONTRACT = "recipe-runtime-v3"
RECIPE_FORMAT = "youju-source-recipe-v5"


class ScriptExecutor(Protocol):
    async def run_script(self, files: dict, module: str, function: str, inputs: dict): ...


class ExtractionModelSession(Protocol):
    stubs: object | None

    def factory(self, model: str): ...

    def check(self) -> None: ...

    def close(self, error: BaseException | None = None) -> None: ...


class ExtractionModelPort(Protocol):
    model_id: str
    provider: str

    def open_flow(self, payload_supplier) -> ExtractionModelSession: ...


def clone(value, *, limit=8_000_000):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecipeError("RECIPE_JSON", "Expected finite JSON data") from exc
    if len(text.encode()) > limit:
        raise RecipeError("RECIPE_SIZE", "Recipe data exceeds the byte limit")
    return json.loads(text)


def candidate_digest(candidate):
    value = {"format": RECIPE_FORMAT, "candidate": clone(candidate, limit=600_000)}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def template_candidate(*, accuracy=None):
    # ``accuracy`` is a host call-site option, never an old output/Recipe contract.
    return {
        "workflow": default_workflow(),
        "parser_py": "from youju.positions.parsing import extract_position_draft\n\n"
        "def parse(ctx, bundle, config):\n"
        "    return extract_position_draft(bundle, config)\n",
        "undated_parser_py": "from youju.positions.parsing import extract_position_draft\n\n"
        "def parse(ctx, bundle, config):\n"
        "    # Adapt open-ended and genuinely undated source layouts here.\n"
        "    # Missing deadlines never suppress readable positions.\n"
        "    return extract_position_draft(bundle, config)\n",
        "config": {"runtime_contract": RUNTIME_CONTRACT, "position_contract": "position-facts-v3"},
    }


def validate_candidate(candidate):
    try:
        candidate = clone(candidate, limit=600_000)
        if not isinstance(candidate, dict) or set(candidate) != {
            "workflow",
            "parser_py",
            "undated_parser_py",
            "config",
        }:
            raise ValueError("Expected workflow, parser_py, undated_parser_py and config only")
        config = candidate["config"]
        if not isinstance(config, dict) or config.get("runtime_contract") != RUNTIME_CONTRACT:
            raise ValueError("Regenerate Recipe for recipe-runtime-v3")
        if config.get("position_contract") != "position-facts-v3":
            raise ValueError("Only position-facts-v3 is supported")
        from .flow_policy import validate_graph

        validate_graph(candidate["workflow"])
        if (
            not isinstance(config.get("structure_notes", ""), str)
            or len(config.get("structure_notes", "")) > 4000
        ):
            raise ValueError("Source structure notes must be bounded text")
        for field in ("parser_py", "undated_parser_py"):
            code = candidate[field]
            if not isinstance(code, str) or not 0 < len(code.encode()) <= 128 * 1024:
                raise ValueError(f"Invalid {field} source size")
            tree = ast.parse(code)
            funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse"]
            if len(funcs) != 1 or [a.arg for a in funcs[0].args.args] != [
                "ctx",
                "bundle",
                "config",
            ]:
                raise ValueError(f"Define parse(ctx, bundle, config) in {field}")
            args = funcs[0].args
            if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
                raise ValueError("Parser signature cannot grant extra inputs")
        from xdog.flow.loader import (
            parse_workflow,
            validation_errors,
            workflow_input_schema,
            workflow_output_schema,
        )

        parsed = parse_workflow(candidate["workflow"])
        for flow, output in [
            (parsed, "result"),
            *[(node.child, "result" if node.id == "finish" else "packet") for node in parsed.nodes],
        ]:
            inputs = workflow_input_schema(flow).get("properties", {})
            outputs = workflow_output_schema(flow).get("properties", {})
            if (
                set(inputs) != {"packet"}
                or inputs["packet"].get("type") != "object"
                or set(outputs) != {output}
                or outputs[output].get("type") != "object"
            ):
                raise ValueError("Flow stage signatures must preserve packet/result object ports")
        errors = validation_errors(parsed)
        if errors:
            return [e.as_dict() for e in errors]
    except (ValueError, TypeError, KeyError, SyntaxError, RecipeError, RecursionError) as exc:
        return [{"code": "RECIPE_INVALID", "message": str(exc)}]
    return []
