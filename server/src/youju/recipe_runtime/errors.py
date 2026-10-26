"""Portable execution failure data, independent of authoring and source persistence."""

from copy import deepcopy


class RecipeError(RuntimeError):
    def __init__(
        self,
        code,
        message,
        *,
        errors=None,
        attempts=None,
        routing=None,
        execution=None,
        rejected_result=None,
        rejected_origin="host_validation",
        partial_result=None,
        stage_outputs=None,
    ):
        super().__init__(message)
        self.code = code
        self.errors = errors or [{"code": code, "message": message}]
        self.attempts = attempts or []
        self.routing = deepcopy(routing)
        self.execution = deepcopy(execution)
        self.rejected_result = deepcopy(rejected_result)
        self.rejected_origin = rejected_origin
        # Host-normalized facts from a prior successful stage, never a Flow success.
        self.partial_result = deepcopy(partial_result)
        self.stage_outputs = deepcopy(stage_outputs or {})

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "errors": self.errors,
            "attempts": self.attempts,
            "routing": deepcopy(self.routing),
            "execution": deepcopy(self.execution),
        }


class ScriptDataError(ValueError):
    """A source adapter failed on its code/data, not its execution infrastructure."""
