"""Shared Recipe authoring over injected extraction and isolated workspace ports.

No source registry, acquisition policy, source DAO or source-admission authority.
The existing xdog and workspace infrastructure remain the sole SDK/tool transport.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from youju.onboarding.xdog import ModelError, check_schema
from youju.positions.completeness import STRICT_COMPLETION_CODES, facts_completion_issues
from youju.positions.extraction import validate_output
from youju.recipe_runtime.contracts import RUNTIME_CONTRACT, candidate_digest, template_candidate
from youju.recipe_runtime.contracts import clone as _clone
from youju.recipe_runtime.errors import RecipeError

# Reuse the installed guidance resource without importing source application code.
TEMPLATES = Path(__file__).parents[1] / "onboarding" / "templates"


class RecipeAuthor:
    def __init__(self, models, runner, *, workspace=None):
        self.models, self.runner, self.workspace = models, runner, workspace

    @staticmethod
    def sample_errors(sample, result):
        # Quick coverage is intentional; zero jobs is not a false current-date claim.
        errors = validate_output(sample, result)
        positions = result.get("positions", []) if isinstance(result, dict) else []
        for index, position in enumerate(positions if isinstance(positions, list) else []):
            conditions = position.get("conditions", []) if isinstance(position, dict) else []
            if (
                isinstance(conditions, list)
                and sum(
                    isinstance(item, dict) and item.get("type") == "other" for item in conditions
                )
                > 1
            ):
                errors.append(
                    {
                        "code": "OTHER_CONDITION_CARDINALITY",
                        "path": f"positions[{index}].conditions",
                        "message": "Generate at most one complete other condition per position. "
                        "Fix parser.py/undated_parser.py and preserve this in Flow refinement; "
                        "never depend on runtime/frontend concatenation.",
                    }
                )
        # The author must not submit a detailed candidate which is merely
        # schema-valid while recognizable fields remain in opaque prose.
        # This is structural feedback, not an independent accuracy/gold claim.
        if not errors and result.get("execution", {}).get("path") == "detailed":
            errors.extend(
                e
                for e in facts_completion_issues(positions)
                if e["code"] in STRICT_COMPLETION_CODES
            )
        return errors

    async def validate_candidate(
        self,
        candidate,
        samples,
        now_iso,
        number,
        *,
        require_application_period=False,
        validation_bindings=None,
        expectations=None,
    ):
        candidate = _clone(candidate)
        errors = self.runner.validate(candidate)
        outcomes, failures = [], []
        if not errors:
            for index, sample in enumerate(samples):
                try:
                    result = await self.runner.run(candidate, sample, now_iso=now_iso)
                    sample_errors = self.sample_errors(sample, result)
                    errors.extend(
                        {**item, "sample_index": index, "case_id": sample.get("id")}
                        for item in sample_errors
                    )
                    semantic = None
                    if sample.get("id") in (expectations or {}):
                        from youju.positions.semantics import check_expected

                        semantic = check_expected(result, expectations[sample["id"]])
                        errors.extend(
                            {
                                **issue,
                                "code": "SEMANTIC_EXPECTATION",
                                "semantic_code": issue["code"],
                                "sample_index": index,
                                "case_id": sample["id"],
                                "message": "Candidate disagrees with independent host assertion",
                            }
                            for issue in semantic["issues"]
                        )
                    outcomes.append(
                        {
                            "case_id": sample.get("id"),
                            "semantic": semantic or {"status": "unlabeled_not_accuracy_evidence"},
                            "sample_index": index,
                            "status": result["status"],
                            "position_count": len(result["positions"]),
                            "routing": result["routing"],
                            "coverage": result["coverage"],
                            "workflow_tested": True,
                        }
                    )
                except RecipeError as exc:
                    from youju.onboarding.diagnostics import failure_record, failure_summary

                    record = failure_record(
                        sample,
                        candidate_digest(candidate),
                        now_iso,
                        exc,
                        bindings=validation_bindings,
                    )
                    observed = {**failure_summary(record), "sample_index": index}
                    if self.workspace is not None:
                        observed["failure_id"] = await self.workspace.run_io(
                            self.workspace.record_failure, record
                        )
                    failures.append(observed)
                    # Runtime errors need not carry child validation errors.
                    # A disabled model/timeout must never validate a candidate
                    # merely because the exception's detail list is empty.
                    details = exc.errors or [
                        {
                            "code": exc.code,
                            "message": "Sample execution failed; inspect the failure receipt",
                        }
                    ]
                    errors.extend(
                        {**item, "sample_index": index, "case_id": sample.get("id")}
                        for item in details
                    )
        return {
            "status": "rejected" if errors else "validated",
            "candidate": candidate,
            "digest": candidate_digest(candidate),
            "errors": errors,
            "samples": outcomes,
            "failures": failures,
            "number": number,
            "original_error_count": len(errors),
        }

    @staticmethod
    def _author_input(samples, now_iso, repair_feedback, *, accuracy=None):
        from youju.onboarding.author_prompt import flow_skill
        from youju.positions.contracts import V3_EXTRACTION_SCHEMA
        from youju.positions.drafts import DRAFT_SCHEMA

        if not isinstance(samples, list) or not 1 <= len(samples) <= 3:
            raise RecipeError("AUTHORING_SAMPLES", "Provide one to three parsed bundles")
        if any(not isinstance(s, dict) or not isinstance(s.get("page"), dict) for s in samples):
            raise RecipeError("AUTHORING_SAMPLES", "Each sample requires a parsed page")
        if repair_feedback is not None and not isinstance(repair_feedback, dict):
            raise RecipeError("AUTHORING_FEEDBACK", "Repair feedback must be an object")
        skill, metadata = flow_skill()
        guide = (TEMPLATES / "extraction-guide.md").read_text()
        from datetime import datetime

        from youju.notices import decide_route

        payload = {
            "guide": guide,
            "template": template_candidate(),
            "samples": _clone(samples),
            "now_iso": now_iso,
            "sample_routing": [
                decide_route(
                    sample, sample.get("assessment"), now=datetime.fromisoformat(now_iso)
                ).as_dict()
                for sample in samples
            ],
            "repair_feedback": _clone(repair_feedback),
            "schemas": {"draft": DRAFT_SCHEMA, "extraction": V3_EXTRACTION_SCHEMA},
            "runtime_contract": RUNTIME_CONTRACT,
        }
        return payload, skill, guide, metadata

    @classmethod
    def prepare_workspace(cls, workspace, samples, *, now_iso, repair_feedback=None, accuracy=None):
        payload, skill, guide, _ = cls._author_input(samples, now_iso, repair_feedback)
        workspace.prepare(payload, skill_text=skill, guide_text=guide, template=payload["template"])
        if repair_feedback is not None:
            workspace.record_audit(repair_feedback)
        return payload

    async def author(
        self,
        samples: list[dict],
        *,
        now_iso: str,
        on_attempt: Callable[[dict], Awaitable[None]] | None = None,
        repair_feedback: dict | None = None,
        on_update: Callable[[dict], Awaitable[None]] | None = None,
        prepared: bool = False,
    ) -> dict:
        from xdog.agent.core import AgentToolResult
        from xdog.agent.tools import create_submit_result_tool
        from xdog.ai.types import TextContent

        from youju.onboarding.author_prompt import AUTHOR_SUBMISSION_SCHEMA, author_system_prompt

        if self.workspace is None:
            raise RecipeError(
                "AUTHORING_WORKSPACE", "A persistent source authoring directory is required"
            )
        workspace = self.workspace
        payload, skill, guide, skill_metadata = self._author_input(
            samples, now_iso, repair_feedback
        )
        samples, repair_feedback = payload["samples"], payload["repair_feedback"]
        if prepared:
            saved = await workspace.run_io(workspace.load_input)
            if saved != {"payload": payload, "skill_text": skill, "guide_text": guide}:
                raise RecipeError("AUTHOR_INPUT_CHANGED", "Prepared source input/contract changed")
        else:
            await workspace.run_io(
                self.prepare_workspace,
                workspace,
                samples,
                now_iso=now_iso,
                repair_feedback=repair_feedback,
            )
        attempts, sink = [], {}
        accepted = None
        upstream_submit = create_submit_result_tool()
        run_record = {
            "status": "running",
            "model": self.models.config.author_model,
            "skill": skill_metadata,
        }

        async def submit(tool_call_id, args, cancel=None, on_update=None, ctx=None):
            nonlocal accepted
            accepted = None
            sink.clear()  # A later rejected submission cannot reuse an earlier receipt.
            # Invalid acknowledgments do not trigger expensive host sample runs.
            try:
                check_schema(args.get("result"), AUTHOR_SUBMISSION_SCHEMA)
            except ModelError:
                return AgentToolResult(
                    content=(
                        TextContent(
                            text='Error: write the files, then submit {"result": {"ready": true}}.'
                        ),
                    )
                )
            try:
                candidate = await workspace.run_io(workspace.load_candidate)
                if repair_feedback and candidate_digest(candidate) == repair_feedback.get(
                    "previous_recipe_digest"
                ):
                    details = [
                        {
                            "code": "REPAIR_UNCHANGED",
                            "message": (
                                "The candidate matches the active recipe. Make substantive "
                                "source-file repairs before resubmitting; report host/classifier "
                                "blockers instead of claiming a repair."
                            ),
                        }
                    ]
                    # Local history only: callbacks and returned attempts may update
                    # recipe rows, so never send the unchanged active recipe to them.
                    await workspace.run_io(
                        workspace.record_attempt,
                        {
                            "number": len(attempts) + 1,
                            "status": "rejected",
                            "errors": details,
                            "samples": [],
                        },
                    )
                    return AgentToolResult(
                        content=(TextContent(text="Error: " + json.dumps(details)),)
                    )
                attempt = await self.validate_candidate(
                    candidate, samples, now_iso, len(attempts) + 1
                )
            except (ValueError, OSError, ModelError) as error:
                details = [{"code": "AUTHOR_ARTIFACT_INVALID", "message": str(error)[:500]}]
                await workspace.run_io(
                    workspace.record_attempt,
                    {
                        "number": len(attempts) + 1,
                        "status": "rejected",
                        "errors": details,
                        "samples": [],
                    },
                )
                return AgentToolResult(content=(TextContent(text="Error: " + json.dumps(details)),))
            attempts.append(attempt)
            await workspace.run_io(workspace.record_attempt, attempt)
            if on_attempt is not None:
                await on_attempt(attempt)
            if attempt["errors"]:
                return AgentToolResult(
                    content=(
                        TextContent(
                            text="Error: host validation rejected the files. "
                            + json.dumps(attempt["errors"], ensure_ascii=False)
                        ),
                    )
                )
            accepted = attempt
            return await upstream_submit.execute(
                tool_call_id, args, cancel=cancel, on_update=on_update, ctx=ctx
            )

        run_index = len(self.models.calls)
        first_error = None
        try:
            run_record = await self.models.run_author_agent(
                workspace=workspace,
                payload=payload,
                system_prompt=author_system_prompt(skill, guide),
                schema=AUTHOR_SUBMISSION_SCHEMA,
                submit_tool=replace(upstream_submit, execute=submit),
                sink=sink,
                skill_metadata=skill_metadata,
                on_update=on_update,
            )
            if accepted is None:
                raise RecipeError(
                    "AUTHOR_NO_SUBMISSION", "No recipe passed host validation", attempts=attempts
                )
            # Never activate files changed after the successful host submission.
            final = await workspace.run_io(workspace.load_candidate)
            if candidate_digest(final) != accepted["digest"]:
                raise RecipeError(
                    "AUTHOR_FILES_CHANGED",
                    "Files changed after accepted validation",
                    attempts=attempts,
                )
            return {
                "candidate": accepted["candidate"],
                "digest": accepted["digest"],
                "attempts": attempts,
            }
        except ModelError as error:
            first_error = error
            run_record = error.metadata or {"status": "failed", "error_code": error.code}
            raise RecipeError(
                error.code, str(error), errors=[error.as_dict()], attempts=attempts
            ) from error
        except asyncio.CancelledError as error:
            first_error = error
            run_record = {"status": "cancelled", "error_code": "MODEL_CANCELLED"}
            raise
        except Exception as error:
            first_error = error
            run_record = {
                "status": "failed",
                "error_code": getattr(error, "code", "AUTHOR_HOST_FAILURE"),
            }
            raise
        finally:
            cleanup_error = None
            try:
                await workspace.wait_io()
            except (Exception, asyncio.CancelledError) as error:
                cleanup_error = error
                if first_error is None:
                    run_record = {
                        "status": "cancelled"
                        if isinstance(error, asyncio.CancelledError)
                        else "failed",
                        "error_code": "MODEL_CANCELLED"
                        if isinstance(error, asyncio.CancelledError)
                        else "AUTHOR_HOST_FAILURE",
                    }
            if run_index < len(self.models.calls):
                run_record = {**self.models.calls[run_index], **run_record}
            try:
                await workspace.run_io(workspace.record_run, run_record)
            except (Exception, asyncio.CancelledError):
                # Cleanup cannot turn a failed run into success or replace the first
                # host/persistence failure with a secondary final-history failure.
                if first_error is None and cleanup_error is None:
                    raise
            if first_error is None and cleanup_error is not None:
                raise cleanup_error
