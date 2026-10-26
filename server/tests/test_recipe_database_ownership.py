"""The authoring daemon trusts its authenticated task producer, not its private DB."""

import ast
from pathlib import Path

from youju.recipe_service.production import create_recipe_app


def test_recipe_factory_needs_no_source_database_and_only_trusted_owner_can_submit(tmp_path):
    app = create_recipe_app(
        {
            "SOURCE_STORAGE": str(tmp_path / "recipe-data"),
            "PROVIDER": "synthetic",
            "RECIPE_RPC_TOKEN": "synthetic-source-owner",
            "RECIPE_CALLBACK_TOKEN": "synthetic-callback",
        }
    )
    client = app.test_client()
    response = client.post(
        "/internal/recipe/CreateWorkspace",
        json={"source_id": "opaque-source"},
        headers={
            "Authorization": "Bearer synthetic-source-owner",
            "X-Recipe-Caller": "source-server",
        },
    )
    assert response.status_code == 200
    assert response.json["result"]["source_id"] == "opaque-source"
    assert not (tmp_path / "youju.sqlite3").exists()
    denied = client.post(
        "/internal/recipe/CreateWorkspace",
        json={"source_id": "opaque-source"},
        headers={"Authorization": "Bearer synthetic-source-owner", "X-Recipe-Caller": "untrusted"},
    )
    assert denied.status_code == 401


def test_recipe_daemon_cannot_import_runtime_business_models():
    source = Path(__file__).parents[1] / "src/youju/recipe_service/production.py"
    imports = [
        node.module
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert "youju.models" not in imports
    assert "youju.database" not in imports
