"""Execute official xdog tools only after entering the author's isolated namespace."""

import asyncio
import json
import os
import resource
import sys
from pathlib import Path

MAX_INPUT = 256 * 1024
MAX_OUTPUT = 256 * 1024


def _limits():
    for key, value in (
        (resource.RLIMIT_CPU, 15),
        (resource.RLIMIT_AS, 768 * 1024 * 1024),
        (resource.RLIMIT_FSIZE, 8 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(key, (value, value))
    os.umask(0o077)


async def _execute(payload):
    from xdog.agent.tools import create_bash_tool, create_filesystem_tool
    from xdog.ai import TextContent

    factories = {"bash": create_bash_tool, "filesystem": create_filesystem_tool}
    name = payload["tool_name"]
    if name not in {*factories, "flow"} or not isinstance(payload["args"], dict):
        raise ValueError("unapproved_author_tool")
    capabilities = None
    if name == "flow":
        from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet

        args = payload["args"]
        commands = {"--help", "validate", "run", "generate", "graph", "test"}
        if not isinstance(args.get("arguments"), list) or not args["arguments"]:
            raise ValueError("flow_arguments_required")
        if args["arguments"][0] not in commands:
            raise ValueError("flow_operation_not_authorized")
        args = {**args, "cwd": args.get("cwd", "/work")}
        # All paths/operations are evaluated inside the existing networkless OS
        # namespace; even validate may import untrusted workflow code.
        capabilities = CapabilitySet("application")
        if {provider.name for provider in capabilities.providers} != {"flow"}:
            raise ValueError("unexpected_author_application_capabilities")
        try:
            await capabilities.start(ApplicationPort())
            tools = CapabilityAssembly(capabilities).compose_tools(enabled_tools=["flow"])
            if len(tools) != 1 or tools[0].name != "flow":
                raise ValueError("flow_capability_unavailable")
            tool = tools[0]
        except BaseException:
            await capabilities.aclose()
            raise
    else:
        args = payload["args"]
        tool = create_bash_tool(initial_cwd=Path("/work")) if name == "bash" else factories[name]()
    cancel = asyncio.Event()
    exceeded = False

    async def on_update(result):
        nonlocal exceeded
        size = sum(
            len(part.text.encode()) for part in result.content if isinstance(part, TextContent)
        )
        if size > MAX_OUTPUT:
            exceeded = True
            cancel.set()

    try:
        result = await tool.execute(
            payload["tool_call_id"],
            args,
            cancel=cancel,
            on_update=on_update,
            ctx={
                "fs_workspace": "/work",
                "fs_confine_to": [
                    "/work",
                    "/input",
                    "/history",
                    "/app",
                    "/tmp",
                    "/usr",
                    sys.prefix,
                    sys.base_prefix,
                ],
            },
        )
    except asyncio.CancelledError:
        if exceeded:
            return {"texts": ["Error: author_tool_output_limit"], "details": None}
        raise
    finally:
        if capabilities is not None:
            await capabilities.aclose()
    if any(not isinstance(part, TextContent) for part in result.content):
        return {"texts": ["Error: authoring tools accept text results only"], "details": None}
    value = {"texts": [part.text for part in result.content], "details": result.details}
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > MAX_OUTPUT:
        return {"texts": ["Error: author_tool_output_limit"], "details": None}
    return value


def main():
    # The script directory contains the application's xdog.py adapter. It must
    # not shadow the official xdog namespace; /work is also never an import root
    # while constructing trusted tools. Generated code is run only by the tools.
    script_directory = str(Path(__file__).parent)
    sys.path[:] = [path for path in sys.path if path not in {script_directory, "/work", ""}]
    _limits()
    try:
        body = Path("/job/input.json").read_bytes()
        if len(body) > MAX_INPUT:
            raise ValueError("author_tool_input_limit")
        value = asyncio.run(_execute(json.loads(body)))
        print(json.dumps({"ok": True, "value": value}, ensure_ascii=False, allow_nan=False))
    except Exception as error:
        # There are no provider credentials or host home mounts in this child.
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {str(error)[:500]}"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
