"""Shared author/runtime ownership, independent of Source policy and DAO imports.

Fresh-import checks isolate the managed package entry from the Flask application's
root bootstrap. They execute real child modules, not stubbed author/runtime code.
"""

import ast
import hashlib
import inspect
import json
import subprocess
import sys
from copy import deepcopy
from importlib.util import resolve_name
from pathlib import Path

import pytest

from youju.extraction_service import ConfiguredExtractionModel, RecipeRunner
from youju.onboarding import recipes
from youju.recipe_runtime.materials import split_material
from youju.recipe_runtime.versioning import runtime_fingerprint
from youju.recipe_service.authoring import RecipeAuthor
from youju.serialization import digest, json_bytes

ROOT = Path(__file__).parents[1] / "src" / "youju"
FORBIDDEN = (
    "youju.onboarding.recipes",
    "youju.onboarding.store",
    "youju.onboarding.pipeline",
    "youju.onboarding.auditing",
    "youju.onboarding.temporal_context",
    "youju.onboarding.classification_policy",
    "youju.jobs",
)


def imports(path):
    module = "youju." + ".".join(path.relative_to(ROOT).with_suffix("").parts)
    package = module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            module = resolve_name(name, package) if node.level else name
            yield module
            yield from (module + "." + alias.name for alias in node.names)


def test_managed_author_and_shared_infrastructure_do_not_import_source_dao_or_facade():
    paths = [
        ROOT / "extraction_service.py",
        ROOT / "serialization.py",
        *sorted((ROOT / "recipe_runtime").glob("*.py")),
        *(
            ROOT / "recipe_service" / (name + ".py")
            for name in (
                "authoring",
                "lifecycle",
                "service",
                "cases",
                "native_runtime",
                "task_inputs",
                "cutover",
                "model_audit",
                "validation_progress",
            )
        ),
        *(
            ROOT / "onboarding" / (name + ".py")
            for name in (
                "author_workspace",
                "diagnostics",
                "xdog",
                "author_prompt",
                "sandbox",
            )
        ),
    ]
    for path in paths:
        for module in imports(path):
            assert not any(module == name or module.startswith(name + ".") for name in FORBIDDEN), (
                path,
                module,
            )


@pytest.mark.parametrize(
    "order",
    [
        [
            "youju.recipe_service.authoring",
            "youju.recipe_service.native_runtime",
            "youju.extraction_service",
        ],
        [
            "youju.extraction_service",
            "youju.recipe_service.lifecycle",
            "youju.recipe_service.native_runtime",
        ],
    ],
)
def test_fresh_managed_imports_and_lazy_diagnostics_never_load_source_dao(order, tmp_path):
    # The web application root currently imports Source endpoints eagerly; that
    # separate entrypoint is not an authoring dependency. Only bypass its __init__,
    # leaving the real recipe_service package init and all dependencies intact.
    program = """
import importlib
import importlib.abc
import sys
import types
from pathlib import Path

root, forbidden, order, work = __import__("json").loads(sys.argv[1])
package = types.ModuleType("youju")
package.__path__ = [root]
package.__package__ = "youju"
sys.modules["youju"] = package

class RejectSourceDAO(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in forbidden):
            raise AssertionError("Managed author imported Source code: " + fullname)

sys.meta_path.insert(0, RejectSourceDAO())
for module in order:
    importlib.import_module(module)
from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.diagnostics import failure_record
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_service.authoring import RecipeAuthor
from youju.recipe_runtime.versioning import runtime_fingerprint
from youju.recipe_service.validation_progress import ValidationProgress
from youju.recipe_runtime.contracts import template_candidate

workspace = AuthorWorkspace(Path(work) / "workspace")
workspace.prepare({}, skill_text="skill", guide_text="guide", template=template_candidate())
workspace.stage_manifest(source_id="opaque-source", objective="Synthetic offline ownership check")
record = failure_record(
    {"id": "case", "source_id": "opaque-source"}, "candidate", "frozen",
    RecipeError("MODEL_DISABLED", "Synthetic", rejected_result={"positions": []}),
)
workspace.record_failure(record)
assert RecipeAuthor.__module__ == "youju.recipe_service.authoring"
assert len(runtime_fingerprint()) == 64
assert ValidationProgress().state["observations"] == 0
assert not any(name in sys.modules for name in forbidden)
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, json.dumps([str(ROOT), FORBIDDEN, order, str(tmp_path)])],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_source_facade_is_only_reexports_with_current_call_signatures():
    tree = ast.parse((ROOT / "onboarding" / "recipes.py").read_text())
    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )
    assert recipes.RecipeAuthor is RecipeAuthor
    assert recipes.RecipeRunner is RecipeRunner
    assert recipes.ConfiguredExtractionModel is ConfiguredExtractionModel
    assert RecipeAuthor.__module__ == "youju.recipe_service.authoring"
    assert RecipeRunner.__module__ == "youju.extraction_service"
    assert list(inspect.signature(RecipeRunner.run).parameters) == [
        "self",
        "candidate",
        "bundle",
        "now_iso",
        "routing",
        "previous_result",
        "on_event",
    ]


def test_canonical_serialization_retains_bytes_and_source_alias_identity():
    from youju.onboarding import store

    value = {"中文": [1, True, None], "a": {"b": 2}}
    expected = '{"a":{"b":2},"中文":[1,true,null]}'.encode()
    assert json_bytes(value) == expected
    assert digest(value) == hashlib.sha256(expected).hexdigest()
    assert store.json_bytes is json_bytes and store.digest is digest
    assert store.toolkit_key() == runtime_fingerprint()
    with pytest.raises(ValueError):
        json_bytes({"invalid": float("nan")})


def test_runtime_fingerprint_tracks_shared_code_not_source_policy_or_routing(tmp_path, monkeypatch):
    from youju.recipe_runtime import versioning

    root = tmp_path / "youju"
    runtime = root / "recipe_runtime" / "versioning.py"
    shared = [
        runtime,
        root / "recipe_runtime" / "runner.py",
        root / "positions" / "facts.py",
        root / "positions" / "issues.py",
        root / "extraction_service.py",
        root / "material_contracts.py",
        root / "model_limits.py",
        root / "serialization.py",
    ]
    for path in shared:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic shared file\n")
    monkeypatch.setattr(versioning, "__file__", str(runtime))
    original = versioning.runtime_fingerprint()
    assert original == versioning.runtime_fingerprint()
    for name in (
        "onboarding/store.py",
        "onboarding/auditing.py",
        "onboarding/sampling.py",
        "onboarding/recipes.py",
        "jobs.py",
        "notices/routing.py",
    ):
        policy = root / name
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("# Source policy-only change must not regenerate a Recipe\n")
        assert versioning.runtime_fingerprint() == original
    for path in shared:
        path.write_text(path.read_text() + "# New extraction semantics\n")
        changed = versioning.runtime_fingerprint()
        assert changed != original
        original = changed


def test_material_split_consumes_only_explicit_frozen_context_and_does_not_mutate():
    change = {"change": {"review_id": "review-1"}, "change_documents": [{"version_id": "v2"}]}
    bundle = {
        "id": "base",
        "page": {"blocks": []},
        "fact_context": change,
        "period_context": {"change": {"review_id": "legacy-must-not-supply-facts"}},
        "attachments": [
            {"version_id": "ordinary", "blocks": []},
            {"version_id": "v2", "change_review_id": "review-1", "blocks": []},
        ],
    }
    snapshot = deepcopy(bundle)
    material, context = split_material(bundle)
    assert bundle == snapshot
    assert context == change and context is not change
    assert "fact_context" not in material and "period_context" not in material
    assert material["attachments"] == [bundle["attachments"][0]]
    material["page"]["blocks"].append({"text": "Changed parser copy"})
    context["change_documents"][0]["version_id"] = "changed"
    assert bundle == snapshot
    legacy_only = {key: value for key, value in bundle.items() if key != "fact_context"}
    assert split_material(legacy_only)[1] is None


def test_application_wiring_keeps_frozen_context_out_of_parser_and_model_inputs():
    import asyncio

    from youju.onboarding.xdog import XdogModels
    from youju.positions.parsing import extract_position_draft
    from youju.recipe_runtime.contracts import template_candidate

    seen = []
    ordinary = {"version_id": "ordinary", "blocks": [], "tables": []}
    bundle = {
        "page": {"blocks": [], "tables": [{"rows": [["岗位名称"], ["合成岗位"]]}]},
        "fact_context": {"adapter_marker": "private-source-metadata"},
        "period_context": {"change": {"review_id": "legacy"}},
        "attachments": [ordinary, {"change_review_id": "review-1", "blocks": [], "tables": []}],
    }
    snapshot = deepcopy(bundle)

    def check_material(material):
        assert "fact_context" not in material and "period_context" not in material
        assert material["attachments"] == [ordinary]

    class Parser:
        async def run_script(self, files, module, function, inputs):
            check_material(inputs["bundle"])
            seen.append("parser")
            draft = extract_position_draft(inputs["bundle"])
            draft["coverage"]["needs_detailed"] = True
            return draft

    def complete(task, payload, schema, tier):
        check_material(payload["bundle"])
        assert "fact_context" not in payload and "period_context" not in payload
        seen.append("model_stub")
        return extract_position_draft(payload["bundle"])

    runner = RecipeRunner(Parser(), XdogModels(stub=complete))
    result = asyncio.run(
        runner.run(
            template_candidate(),
            bundle,
            now_iso="2026-10-25T12:00:00+08:00",
            routing={
                "category": "unknown",
                "mode": "detailed",
                "reason": "Synthetic context-isolation check",
                "policy_version": "ownership-test-v1",
                "evaluated_at": "2026-10-25T12:00:00+08:00",
            },
        )
    )
    assert seen == ["parser", "model_stub"]
    assert result["positions"][0]["title"]["value"] == "合成岗位"
    assert bundle == snapshot
