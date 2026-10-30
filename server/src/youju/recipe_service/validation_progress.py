"""Progress-based stopping over frozen validation evidence, not author turns/tokens.

Changed files alone are not progress. An unchanged diagnosed defect/position state
or an observed cycle ends the task; newly exposed diagnostics and improving row
coverage remain valid work. State is bounded, persisted and scoped to fixed inputs.
"""

from youju.serialization import digest

VERSION = "recipe-progress-v1"
RECENT_STATES = 32


class ValidationProgress:
    def __init__(self, saved=None):
        self.state = saved or {"version": VERSION, "recent": [], "observations": 0}
        if (
            self.state.get("version") != VERSION
            or not isinstance(self.state.get("recent"), list)
            or len(self.state["recent"]) > RECENT_STATES
        ):
            raise ValueError("Invalid validation progress state")

    def observe(self, attempt):
        observations = []
        for failure in attempt.get("failures", []):
            observations.append(
                {
                    key: failure.get(key)
                    for key in (
                        "case_id",
                        "error_code",
                        "stage",
                        "issue_digest",
                        "rejected_position_count",
                    )
                }
            )
        observed_cases = {row.get("case_id") for row in observations}
        for sample in attempt.get("samples", []):
            if sample.get("case_id") in observed_cases and sample.get("case_id") is not None:
                continue
            observations.append(
                {
                    key: sample.get(key)
                    for key in (
                        "sample_index",
                        "status",
                        "position_count",
                        "case_id",
                        "semantic",
                    )
                }
            )
        state = digest(
            {
                "observations": observations,
                "errors": [
                    {
                        key: error.get(key)
                        for key in (
                            "code",
                            "path",
                            "sample_index",
                            "case_id",
                            "semantic_code",
                            "actual",
                            "expected",
                            "source_path",
                            "position_key",
                            "candidate_types",
                            "condition_id",
                            "reason",
                            "resource_id",
                            "message",
                            "line",
                            "offset",
                            "detail",
                        )
                    }
                    for error in attempt["errors"]
                ],
                "error_count": attempt.get("original_error_count", len(attempt["errors"])),
            }
        )
        recent = self.state["recent"]
        stop = bool(attempt["errors"]) and state in recent
        reason = (
            (
                "unchanged_confirmed_defects"
                if recent and recent[-1] == state
                else "oscillating_defects"
            )
            if stop
            else "changed_or_new_diagnostic_state"
        )
        self.state["recent"] = [*recent, state][-RECENT_STATES:]
        self.state["observations"] += 1
        return {
            "stop": stop,
            "reason": reason,
            "state_digest": state,
            "observations": self.state["observations"],
            "lifetime_budget": None,
        }
