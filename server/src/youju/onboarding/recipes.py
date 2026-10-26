"""Source-facing symbol facade; extraction and authoring have shared owners.

Only current Python call-site aliases are retained, never old Recipe/data formats.
The source application prepares explicit bundle.fact_context before execution.
"""

from youju.extraction_service import CANDIDATE_SCHEMA, ConfiguredExtractionModel, RecipeRunner
from youju.recipe_runtime.contracts import RUNTIME_CONTRACT, candidate_digest, template_candidate
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_service.authoring import TEMPLATES, RecipeAuthor

__all__ = [
    "CANDIDATE_SCHEMA",
    "ConfiguredExtractionModel",
    "RecipeRunner",
    "RUNTIME_CONTRACT",
    "candidate_digest",
    "template_candidate",
    "RecipeError",
    "TEMPLATES",
    "RecipeAuthor",
]
