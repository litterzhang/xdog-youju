"""Trusted child entry point. All limits are applied before loading generated code."""

import importlib.util
import json
import os
import resource
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    for key, value in (
        (resource.RLIMIT_CPU, 15),
        (resource.RLIMIT_AS, 768 * 1024 * 1024),
        (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(key, (value, value))
    os.umask(0o077)
    payload = json.loads(Path("/job/input.json").read_bytes())
    operation = payload["operation"]
    try:
        if operation == "probe":
            value = {"isolated": True, "home": os.environ["HOME"], "network": "unshared"}
        elif operation == "material":
            from youju.onboarding.materials import parse_material

            value = parse_material(
                Path("/job/body.bin").read_bytes(),
                media_type=payload["media_type"],
                url=payload["url"],
                material_key=payload["material_key"],
            )
        elif operation == "script":
            module = payload["module"]
            if module not in {"parser", "undated_parser"}:
                raise ValueError("Unknown module")
            spec = importlib.util.spec_from_file_location(module, f"/job/{module}.py")
            imported = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(imported)
            function = getattr(imported, payload["function"])
            ctx = SimpleNamespace(workspace=Path("/tmp"), node_id=module, workflow_name="recipe")
            value = function(ctx, **payload["inputs"])
            # Source adapters are pure sync transformations; no nested event loop needed.
        else:
            raise ValueError("Unknown operation")
        encoded = json.dumps({"ok": True, "value": value}, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 8 * 1024 * 1024:
            raise ValueError("result_limit")
        print(encoded)
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "error": f"{type(error).__name__}: {str(error)[:500]}",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
