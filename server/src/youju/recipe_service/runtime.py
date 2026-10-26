"""SDK-independent task boundary; no model client, engine, or session serializer.

The native xdog adapter owns skills, compaction and checkpoint interpretation.
The control service owns scheduling, immutable inputs and independent acceptance.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from youju.onboarding.author_workspace import AuthorWorkspace


TaskStatus = Literal["succeeded", "candidate_ready", "blocked", "failed", "cancelled"]
OnEvent = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """A proposed result, not permission to publish or mark a workspace prepared.

    Successful init/build/repair tasks must supply a nonempty opaque checkpoint dictionary.
    Only portable, secret-free JSON data belongs in checkpoints and results: never
    provider credentials, live SDK objects, tools, or serialized Python callables.
    The host validates files and fences the session revision before accepting it.
    """

    status: TaskStatus
    result: dict | None = None
    checkpoint: dict | None = None
    error_code: str | None = None


class AgentRuntime(Protocol):
    """One task's runtime, recreated by the factory and closed before releasing work.

    Restoring the opaque checkpoint must not itself call a model or replay tools.
    Input files come from an immutable snapshot, not the mutable upload directory.
    Filesystem adapters must mount that snapshot read-only; ``workspace.input`` is
    staging and must not be exposed as the task's authoritative input directory.
    Workers reserve work with ``service.assignment(workspace_id)`` and load the
    frozen prompt/feedback with ``service.task_prompt(task_id)``; they do not read
    private service locks or blob storage. RecipeRevision publication and owner
    accept/reject reviews are service operations, never runtime execution.
    ``task['prompt']`` is the host-loaded prompt; profiles contain configuration,
    never live credentials. Adapters must await ``on_event`` and must not swallow
    its errors or cancellation. Events are progress metadata, not transcripts.
    Cancellation must stop task execution; ``aclose`` must join owned background
    work and release resources, including after partial initialization or failure.
    """

    async def execute_task(
        self,
        *,
        task: dict,
        workspace: "AuthorWorkspace",
        inputs: dict,
        agent_profile: dict,
        checkpoint: dict | None,
        on_event: OnEvent,
    ) -> TaskOutcome: ...

    async def aclose(self) -> None: ...


RuntimeFactory = Callable[[], AgentRuntime]
