"""Managed init/build/repair dispatch, without a default Agent implementation.

This worker dispatches only managed tasks and never calls a model when no
runtime adapter is installed.
"""

import asyncio
import math
import re
from uuid import uuid4

from .runtime import AgentRuntime, RuntimeFactory, TaskOutcome
from .service import RecipeService, ServiceBusy, ServiceError

MANAGED_KINDS = ("init", "build", "repair")


class _RuntimeFailure(ValueError):
    """A host-selected code, never an arbitrary runtime/provider exception string."""


class TaskWorker:
    def __init__(
        self,
        service: RecipeService,
        runtime_factory: RuntimeFactory | None = None,
        *,
        poll_seconds: float = 1,
    ):
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("Polling interval must be positive")
        self.service = service
        self.runtime_factory = runtime_factory
        self.poll_seconds = poll_seconds

    async def run_once(self, job_id: str | None = None) -> dict | None:
        if self.runtime_factory is None:
            # Do not even inspect/claim the queue: configuration absence is not a
            # task failure, and installing an adapter should leave work resumable.
            return {
                "status": "runtime_unavailable",
                "error_code": "RUNTIME_UNAVAILABLE",
                **({"job_id": job_id} if job_id is not None else {}),
            }
        service = self.service
        candidates = [job_id] if job_id else service.pending(kinds=MANAGED_KINDS)
        for key in candidates:
            info = service.status(key)
            if info["status"] != "queued" or info.get("kind") not in MANAGED_KINDS:
                continue
            try:
                with service.assignment(info["workspace_id"]):
                    owner = uuid4().hex
                    job = service.claim(key, owner)
                    await self._run(job, owner)
                    return service.status(key)
            except ServiceBusy:
                if job_id:
                    raise
        return None

    async def _run(self, job: dict, owner: str) -> None:
        service, key = self.service, job["id"]
        runtime = execution = watcher = None
        outcome = failure = None
        interrupted = False
        try:
            info = service.workspace_info(job["workspace_id"])
            if job["base_session_revision"] != info["session_revision"]:
                raise _RuntimeFailure("SESSION_REVISION_CHANGED")
            workspace = service.workspace(job["workspace_id"])
            inputs = service.input_snapshot(job["input_revision_id"])
            checkpoint = service.checkpoint(job["workspace_id"])
            task = dict(job)
            prompt = service.task_prompt(key)
            if not isinstance(prompt, dict) or not isinstance(prompt.get("prompt"), str):
                raise _RuntimeFailure("RUNTIME_INVALID_PROMPT")
            task["prompt"] = prompt["prompt"]
            if "feedback" in prompt:
                task["feedback"] = prompt["feedback"]
            if service.progress(key, owner):
                raise asyncio.CancelledError
            runtime = self.runtime_factory()

            async def progress(data):
                # Short synchronous metadata transactions cannot outlive assignment
                # release as cancelled to_thread writes could. No model work here.
                if isinstance(data, dict) and data.get("type") == "checkpoint":
                    # Private SDK state must not pass through progress redaction or
                    # enter public events. save_checkpoint emits a ref-only event.
                    service.save_checkpoint(
                        key,
                        owner,
                        data.get("checkpoint"),
                        recovery=data.get("recovery", False),
                    )
                    if service.progress(key, owner):
                        raise asyncio.CancelledError
                elif service.progress(key, owner, _progress_metadata(data)):
                    raise asyncio.CancelledError

            async def monitor():
                while True:
                    await asyncio.sleep(self.poll_seconds)
                    if service.progress(key, owner):
                        execution.cancel()
                        return

            execution = asyncio.create_task(
                runtime.execute_task(
                    task=task,
                    workspace=workspace,
                    inputs=inputs,
                    agent_profile=info["agent_profile"],
                    checkpoint=checkpoint,
                    on_event=progress,
                )
            )
            watcher = asyncio.create_task(monitor())
            done, _ = await asyncio.wait({execution, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                # Failure to persist status stops execution, never an invisible run.
                await watcher
            outcome = await execution
            _validate_outcome(job["kind"], outcome)
        except asyncio.CancelledError:
            interrupted = True
        except Exception as exc:
            failure = exc
        finally:
            # Hold the assignment even through repeated caller cancellation. A fresh
            # worker must never share the workspace with a still-closing runtime.
            cleanup = asyncio.create_task(_close(runtime, execution, watcher))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
                except Exception:
                    break
            try:
                cleanup.result()
            except (Exception, asyncio.CancelledError):
                failure = _RuntimeFailure("RUNTIME_CLOSE_FAILED")

        if interrupted:
            cancelled = service.status(key)["cancel_requested"]
            service.finish(
                key,
                owner,
                "cancelled" if cancelled else "interrupted",
                error_code="CANCELLED" if cancelled else "WORKER_INTERRUPTED",
            )
            if not cancelled:
                raise asyncio.CancelledError
        elif failure is not None:
            code = _failure_code(failure)
            service.finish(key, owner, "failed", error_code=code)
            raise ServiceError(code) from None
        else:
            # Only the host service can validate actual files, CAS the checkpoint
            # and publish candidate_ready/prepared. Cleanup has already completed.
            try:
                service.finish(
                    key,
                    owner,
                    outcome.status,
                    result=outcome.result,
                    error_code=outcome.error_code,
                    checkpoint=outcome.checkpoint,
                )
            except Exception:
                service.finish(key, owner, "failed", error_code="RUNTIME_RESULT_REJECTED")
                raise ServiceError("RUNTIME_RESULT_REJECTED") from None


def _failure_code(failure):
    if isinstance(failure, _RuntimeFailure):
        return str(failure)
    # Do not trust arbitrary exception attributes/messages. The native adapter
    # alone owns fixed host codes; this lazy import keeps the control-plane-only
    # path independent of native SDK import/setup until an execution error.
    try:
        from .native_runtime import NativeRuntimeError
    except ImportError:
        return "RUNTIME_TASK_FAILED"
    if isinstance(failure, NativeRuntimeError):
        # Compatibility with the adapter's initial fixed-code RuntimeError:
        # prefer its explicit code property once present, never generic errors.
        code = getattr(failure, "code", str(failure))
        if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
            return code
    return "RUNTIME_TASK_FAILED"


async def _close(runtime: AgentRuntime | None, execution, watcher) -> None:
    tasks = [task for task in (execution, watcher) if task is not None]
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if runtime is not None:
            await runtime.aclose()


def _validate_outcome(kind: str, outcome: TaskOutcome) -> None:
    if not isinstance(outcome, TaskOutcome):
        raise _RuntimeFailure("RUNTIME_INVALID_OUTCOME")
    success = "succeeded" if kind == "init" else "candidate_ready"
    if outcome.status not in {success, "blocked", "failed", "cancelled"}:
        raise _RuntimeFailure("RUNTIME_INVALID_OUTCOME")
    if outcome.result is not None and not isinstance(outcome.result, dict):
        raise _RuntimeFailure("RUNTIME_INVALID_OUTCOME")
    if outcome.status == success and (
        not isinstance(outcome.checkpoint, dict) or not outcome.checkpoint
    ):
        raise _RuntimeFailure("RUNTIME_CHECKPOINT_REQUIRED")
    if outcome.checkpoint is not None and not isinstance(outcome.checkpoint, dict):
        raise _RuntimeFailure("RUNTIME_INVALID_OUTCOME")
    if outcome.error_code is not None and (
        not isinstance(outcome.error_code, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", outcome.error_code) is None
    ):
        raise _RuntimeFailure("RUNTIME_INVALID_OUTCOME")


def _progress_metadata(data: dict) -> dict:
    """Keep bounded metadata; drop raw arguments, messages, content and responses."""
    if not isinstance(data, dict):
        raise _RuntimeFailure("RUNTIME_INVALID_EVENT")
    metadata = {"type": "runtime_progress", "untrusted": True}
    for name in ("type", "event", "phase", "status", "tool_name"):
        value = data.get(name)
        if isinstance(value, str):
            metadata["type" if name == "event" else name] = value[:64]
    for name in (
        "turn",
        "attempt",
        "sequence",
        "requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input",
        "output",
        "total",
    ):
        value = data.get(name)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            metadata["agent_sequence" if name == "sequence" else name] = value
    return metadata
