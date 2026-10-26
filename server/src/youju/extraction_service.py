"""Application wiring for injected extraction; no source DAO or authoring owner."""

from datetime import datetime

from youju.notices.routing import decide_route
from youju.recipe_runtime.materials import split_material
from youju.recipe_runtime.runner import RecipeRunner as RuntimeRunner

CANDIDATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["workflow", "parser_py", "undated_parser_py", "config"],
    "properties": {
        "workflow": {"type": "object"},
        "config": {"type": "object"},
        "parser_py": {"type": "string", "minLength": 1, "maxLength": 131072},
        "undated_parser_py": {"type": "string", "minLength": 1, "maxLength": 131072},
    },
}


class ConfiguredScriptExecutor:
    """Translate application sandbox failures without granting the runtime SDK authority."""

    def __init__(self, sandbox):
        self.sandbox = sandbox

    async def run_script(self, *args, **kwargs):
        from youju.onboarding.sandbox import SandboxError, ScriptExecutionError
        from youju.recipe_runtime.errors import RecipeError, ScriptDataError

        try:
            return await self.sandbox.run_script(*args, **kwargs)
        except ScriptExecutionError as error:
            raise ScriptDataError("Isolated source code/data failed") from error
        except SandboxError as error:
            raise RecipeError(
                "SCRIPT_RUNTIME_UNAVAILABLE",
                "Isolated execution infrastructure is unavailable",
                rejected_origin="script_infrastructure",
            ) from error


class ConfiguredExtractionModel:
    """Bind native Flow agent nodes; this port never launches a workflow."""

    def __init__(self, models):
        self.models = models

    @property
    def model_id(self):
        return self.models.config.model_for("flow") or "youju-offline/stub"

    @property
    def provider(self):
        return self.models.config.provider or "youju-offline"

    def open_flow(self, payload_supplier):
        from youju.onboarding.xdog import NativeFlowModelSession
        from youju.positions.drafts import DRAFT_SCHEMA, validate_model_draft

        return ExtractionModelSession(
            NativeFlowModelSession(
                self.models,
                "extract_position_draft",
                DRAFT_SCHEMA,
                payload_supplier,
                submission_validator=lambda value: validate_model_draft(
                    value, payload_supplier()["previous_draft"]
                ),
            ),
            payload_supplier,
        )


class ExtractionModelSession:
    """Translate application model errors into the portable runtime port contract."""

    def __init__(self, native, payload_supplier):
        self.native, self.payload_supplier = native, payload_supplier

    def _call(self, operation, *args):
        from youju.onboarding.xdog import ModelError
        from youju.recipe_runtime.errors import RecipeError

        try:
            return operation(*args)
        except ModelError as error:
            raise RecipeError(
                error.code,
                str(error),
                errors=[error.as_dict()],
                rejected_result=error.rejected_output,
                rejected_origin="model_submission"
                if error.rejected_output is not None
                else "model_execution",
            ) from error

    @property
    def stubs(self):
        return ExtractionModelStubs(self) if self.native.stubs is not None else None

    def factory(self, model):
        return self._call(self.native.factory, model)

    def check(self):
        return self._call(self.native.check)

    def close(self, error=None):
        self.native.close(error)


class ExtractionModelStubs:
    """Translate errors at the same boundary for SDK offline agent substitutions."""

    def __init__(self, session):
        self.session = session

    def subflow(self, node, **kwargs):
        return None

    def script(self, node, **kwargs):
        return None

    def agent(self, node, **kwargs):
        from functools import partial

        return self.session._call(partial(self.session.native.stubs.agent, node, **kwargs))


class RecipeRunner(RuntimeRunner):
    """Application wiring; the shared runtime knows no source store or SDK config."""

    def __init__(self, sandbox, models=None, *, timeout_seconds=None):
        self.models = models
        enabled = models is not None and (models.stub is not None or models.config.live_enabled)
        super().__init__(
            ConfiguredScriptExecutor(sandbox),
            ConfiguredExtractionModel(models) if enabled else None,
            timeout_seconds=timeout_seconds,
        )

    async def run(
        self, candidate, bundle, *, now_iso, routing=None, previous_result=None, on_event=None
    ):
        if routing is None:
            routing = decide_route(
                bundle, bundle.get("assessment"), now=datetime.fromisoformat(now_iso)
            ).as_dict()
        material, context = split_material(bundle)
        return await super().run(
            candidate,
            material,
            routing=routing,
            fact_context=context,
            previous_result=previous_result,
            on_event=on_event,
        )
