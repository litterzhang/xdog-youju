"""Local coding-Agent feedback, executable only inside its isolated tool process.

This fast-path check is not a source-admission decision. The parent host performs
its own validation, including the real detailed branch, when files are submitted.
"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path

from .recipes import RecipeAuthor, RecipeError, RecipeRunner


class LocalAdapters:
    """Import candidate code only in the already-isolated Bash tool child."""

    async def run_script(self, files, module, function, inputs):
        if os.environ.get("YOUJU_AUTHOR_SANDBOX") != "1":
            raise RuntimeError("Local adapter execution requires the author tool sandbox")
        spec = importlib.util.spec_from_file_location(module, f"/work/{module}.py")
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return getattr(loaded, function)(None, **inputs)


async def check():
    if os.environ.get("YOUJU_AUTHOR_SANDBOX") != "1":
        raise RuntimeError("Run this command through the isolated coding Agent bash tool")
    root = Path("/work")
    candidate = {
        "workflow": json.loads((root / "workflow.json").read_text()),
        "config": json.loads((root / "config.json").read_text()),
        "parser_py": (root / "parser.py").read_text(),
        "undated_parser_py": (root / "undated_parser.py").read_text(),
    }
    payload = json.loads(Path("/input/payload.json").read_text())
    runner = RecipeRunner(LocalAdapters())
    errors, outcomes = runner.validate(candidate), []
    if not errors:
        for number, sample in enumerate(payload["samples"]):
            try:
                result = await runner.run(candidate, sample, now_iso=payload["now_iso"])
                outcomes.append({"sample_index": number, "result": result})
                sample_errors = RecipeAuthor.sample_errors(sample, result)
                errors.extend({**error, "sample_index": number} for error in sample_errors)
                if sample_errors:
                    errors.append({"code": "FAST_PROBE_INCOMPLETE", "sample_index": number})
            except RecipeError as error:
                errors.extend({**item, "sample_index": number} for item in error.errors)
    return {
        "passed": not errors,
        "errors": errors,
        "samples": outcomes,
        "detailed_branch_tested": False,
    }


def main():
    try:
        report = asyncio.run(check())
    except Exception as error:
        report = {"passed": False, "error": f"{type(error).__name__}: {str(error)[:500]}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
