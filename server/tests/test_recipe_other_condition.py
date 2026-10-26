"""Author-side singleton feedback, not runtime or frontend rewriting."""

from copy import deepcopy

from youju.positions.facts import POSITION_FIELDS, PositionFacts, TaggedField
from youju.recipe_service.authoring import RecipeAuthor
from youju.recipe_service.native_runtime import SYSTEM_PROMPT


def test_author_rejects_split_other_conditions_on_both_paths_without_modifying_output():
    fields = {key: TaggedField() for key in POSITION_FIELDS}
    fields["title"] = TaggedField("合成岗位", "source")
    position = PositionFacts(fields).as_dict()
    for path in ("quick", "detailed"):
        for count in (0, 1, 2):
            row = deepcopy(position)
            row["conditions"] = [
                {
                    "id": str(i),
                    "type": "other",
                    "scope": "position",
                    "operator": "contains",
                    "value": {"text": text},
                    "tag": "source",
                }
                for i, text in enumerate(("遵纪守法，身体健康。", "具有良好沟通能力。")[:count])
            ]
            row["condition_logic"] = (
                {"operator": "all", "items": [{"condition_id": str(i)} for i in range(count)]}
                if count
                else None
            )
            result = {
                "schema_version": "position-facts-v3",
                "status": "partial",
                "positions": [row],
                "errors": [],
                "coverage": {"supported": True, "complete": False, "needs_detailed": True},
                "execution": {"path": path},
            }
            before = deepcopy(result)
            errors = RecipeAuthor.sample_errors({}, result)
            assert [e["code"] for e in errors] == (
                ["OTHER_CONDITION_CARDINALITY"] if count > 1 else []
            )
            assert result == before
    assert "at most ONE type=other condition" in SYSTEM_PROMPT
    assert "host/UI will NOT concatenate" in SYSTEM_PROMPT
