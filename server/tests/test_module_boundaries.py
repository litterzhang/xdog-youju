"""Executable architecture rules, not comments that can drift during repair."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "youju"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (name.name for name in node.names)


def test_position_domain_has_no_application_or_cost_authority():
    forbidden = (
        "youju.onboarding",
        "youju.notices",
        "youju.recipe_service",
        "youju.models",
        "youju.recipe_runtime",
        "xdog",
        "httpx",
        "flask",
        "sqlalchemy",
    )
    for path in (ROOT / "positions").glob("*.py"):
        assert not any(module.startswith(forbidden) for module in imports(path)), path


def test_cost_policy_cannot_depend_on_position_extraction():
    for path in (ROOT / "notices").glob("*.py"):
        assert not any(
            module.startswith(
                (
                    "youju.positions",
                    "youju.onboarding",
                    "youju.recipe_service",
                    "xdog",
                    "sqlalchemy",
                )
            )
            for module in imports(path)
        ), path


def test_shared_execution_cannot_import_source_application_or_authoring():
    for path in (ROOT / "recipe_runtime").glob("*.py"):
        assert not any(
            module.startswith(
                ("youju.onboarding", "youju.recipe_service", "youju.models", "sqlalchemy", "flask")
            )
            for module in imports(path)
        ), path


def test_reverse_query_tools_have_no_source_implementation_dependency():
    for name in ("runtime_extension.py", "runtime_tools.py"):
        modules = list(imports(ROOT / "recipe_service" / name))
        assert not any(
            module.startswith(
                ("youju.onboarding", "youju.models", "youju.runtime_queries", "sqlalchemy")
            )
            for module in modules
        )
