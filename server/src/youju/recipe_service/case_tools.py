"""Small domain tools registered through the official xdog AgentTool API."""

import asyncio
import inspect
import json

from xdog.agent import AgentTool, AgentToolResult
from xdog.ai import TextContent

from youju.onboarding.xdog import ModelError, check_schema


def case_tools(access):
    cursor = {"type": "integer", "minimum": 0}
    identifier = {"type": "string", "minLength": 1, "maxLength": 128}
    specs = [
        (
            "list_cases",
            (),
            {"cursor": cursor},
            "List paginated metadata from the assigned frozen catalog.",
        ),
        (
            "read_case",
            ("case_id",),
            {"case_id": identifier, "cursor": cursor},
            "Read bounded untrusted saved material; no network, arbitrary paths or gold.",
        ),
        (
            "test_recipe",
            ("case_ids",),
            {
                "case_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": identifier,
                }
            },
            "Test actual canonical files against pinned cases through the host runner.",
        ),
        (
            "inspect_failure",
            ("test_id",),
            {
                "test_id": identifier,
                "cursor": cursor,
                "section": {
                    "enum": [
                        "issues",
                        "issue",
                        "rejected_output",
                        "partial_output",
                        "script_draft",
                        "detailed_draft",
                        "merged_draft",
                    ]
                },
                "issue_index": cursor,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "Read bounded issues or failure:<id> rejected output. "
            "Page large issues with section=issue.",
        ),
    ]
    tools = []
    for name, required, properties, description in specs:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": list(required),
            "properties": properties,
        }

        def execute_for(method, parameters):
            async def execute(tool_call_id, args, cancel=None, on_update=None, **kwargs):
                try:
                    check_schema(args, parameters)
                    if inspect.iscoroutinefunction(method):
                        result = await method(**args)
                    else:
                        result = await asyncio.to_thread(method, **args)
                    text = json.dumps(result, ensure_ascii=False, allow_nan=False)
                    # No silent truncation of crucial diagnostics.
                    if len(text.encode()) > 128 * 1024:
                        raise ValueError("Result exceeds the page limit; use smaller pages")
                    return AgentToolResult(content=(TextContent(text=text),))
                except (ValueError, KeyError, OSError, ModelError):
                    return AgentToolResult(
                        is_error=True,
                        content=(
                            TextContent(
                                text="Error: invalid task-bound case request "
                                "or unavailable pinned input."
                            ),
                        ),
                    )

            return execute

        tools.append(
            AgentTool(
                name=name,
                parameters=schema,
                description=description,
                execute=execute_for(getattr(access, name), schema),
            )
        )
    return tools
