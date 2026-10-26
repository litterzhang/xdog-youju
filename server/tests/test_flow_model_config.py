"""Extraction model selection is independent and stays explicitly opt-in."""

import asyncio

import pytest
from test_recipe_runtime_v3 import material, route
from test_recipe_subflows import Unsupported

from youju import create_app
from youju.background import configured_models
from youju.extraction_service import ConfiguredExtractionModel, RecipeRunner
from youju.onboarding.xdog import DEFAULT_FLOW_MODEL, XdogConfig, XdogModels
from youju.positions.parsing import extract_position_draft
from youju.recipe_runtime.contracts import template_candidate


def test_default_flow_model_does_not_change_classification_or_authoring():
    config = XdogConfig(small_model="classifier-small", large_model="classifier-large")
    assert config.model_for("flow") == DEFAULT_FLOW_MODEL == "gpt-5.4-nano"
    assert config.model_for("large") == "classifier-large"
    assert config.model_for("small") == "classifier-small"
    assert config.model_for("author") == "gpt-6.1-sol"
    assert config.live_enabled is False
    assert ConfiguredExtractionModel(XdogModels(config)).model_id == DEFAULT_FLOW_MODEL


@pytest.mark.parametrize("override", [None, "custom-extraction-model"])
def test_worker_flow_model_configuration(override):
    config = dict(
        WORKERS_ENABLE_MODELS=True,
        PROVIDER="synthetic",
        SMALL_MODEL="small",
        LARGE_MODEL="large",
    )
    if override:
        config["FLOW_MODEL"] = override
    models = configured_models(config)
    assert models.config.flow_model == (override or DEFAULT_FLOW_MODEL)
    assert models.metadata["flow_model"] == models.config.flow_model
    with pytest.raises(ValueError, match="Explicit"):
        configured_models({**config, "WORKERS_ENABLE_MODELS": False})


def test_environment_flow_model_reaches_worker(monkeypatch):
    monkeypatch.setenv("YOUJU_FLOW_MODEL", "environment-extraction-model")
    app = create_app({"TESTING": True})
    assert app.config["FLOW_MODEL"] == "environment-extraction-model"
    result = app.test_cli_runner().invoke(args=["source-step", "--help"])
    assert result.exit_code == 0
    assert "--flow-model" in result.output


@pytest.mark.parametrize("value", ["", " ", None])
def test_live_flow_model_must_be_nonblank(value):
    with pytest.raises(ValueError, match="flow models"):
        XdogConfig(
            provider="synthetic",
            small_model="s",
            large_model="l",
            flow_model=value,
            live_enabled=True,
        )


def test_native_flow_reports_selected_model_and_tier_without_provider(monkeypatch):
    import xdog.ai as ai

    def forbidden(*args):
        pytest.fail("Offline stub must not discover any provider")

    monkeypatch.setattr(ai, "provider", forbidden)
    tiers = []

    def stub(task, payload, schema, tier):
        tiers.append(tier)
        return extract_position_draft(payload["bundle"])

    models = XdogModels(XdogConfig(flow_model="custom-flow"), stub=stub)
    result = asyncio.run(
        RecipeRunner(Unsupported(), models).run(
            template_candidate(),
            material(),
            now_iso=route("detailed")["evaluated_at"],
            routing=route("detailed"),
        )
    )
    assert result["execution"]["model_used"]
    assert tiers == ["flow"]
    assert models.calls[0]["tier"] == "flow"
    assert models.calls[0]["model"] == "custom-flow"
