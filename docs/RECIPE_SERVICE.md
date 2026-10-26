# Recipe Service

## Current architecture

There is one authoring path: the source server uses authenticated Recipe RPC; the
independent `recipe-task-worker` runs the official xdog 2.4.1 native adapter. No source
step opens the sidecar database or starts an inline Agent. The old `recipe-worker`,
legacy workspace option and implicit `SubmitFeedback` repair dispatch are removed.

The Recipe service owns workspaces, frozen inputs, native Agent checkpoints,
init/build/repair tasks, immutable recipe revisions, sample validation and the callback
outbox. The source server owns acquisition and material parsing, full-source audits,
publication bindings, positions and incremental monitoring. This is a same-host
boundary, not a distributed Agent platform or a second workflow engine.

## Records

- **Workspace:** service-assigned physical path, trusted profile/skills, preparation
  state and opaque session checkpoint. Creation alone never starts init.
- **InputRevision:** validated, content-addressed snapshot of pre-parsed materials,
  objective, up to three samples and optional repair diagnostics/base artifact.
- **RecipeTask:** internally stored as `Job`; one init/build/repair execution with
  independent status, progress, input and session/base fences.
- **RecipeRevision:** immutable actual parser/workflow/config artifacts, task/input/
  profile metadata, repair target and accepted-base snapshot, and sample validation.
  SQL triggers reject revision updates/deletes. Editing workspace files cannot alter it.
- **RevisionReview:** immutable owner-supplied accept/reject audit decision. It never
  queues execution or changes a completed task's execution status.
- **SourceRecipeBinding:** source-side candidate/active local artifacts and service
  revision IDs, workspace identity and authoritative publication generation.
- **RepairRequest:** source-side fixed repair intent for automatic incremental failures,
  reviewed manual audits or a failed request's feedback/retry. It does not reopen
  onboarding or replace the current binding while work is pending.

Task completion, sample coverage, full-source audit and adoption are separate facts.
A generated candidate is not a verified vacancy or an accepted source recipe.

## Lifecycle

```text
CreateWorkspace -> server stages parsed input -> InputCompleted
  -> auto init -> native session checkpoint -> close/unload Agent
  -> StartBuild / StartRepair -> restore session -> write and validate actual files
  -> RecipeRevision + callback -> full-source audit -> ReviewRevision + adoption
```

Native skills, session restore and auto compaction belong to xdog. Default author:
`gpt-6.1-sol`. No author turn/token/whole-run deadline cap is added. Tool isolation,
provider context limits, ordinary extraction budgets and explicit cancellation remain.
Idle workspaces have no live Agent. Runtime workers use public `assignment()` and
`task_prompt()` boundaries; source coordination does not call Pipeline private methods.

## Repair and reviews

`StartRepair` always takes a fresh InputRevision and an exact base revision. The target
may be rejected or not yet accepted, including a failed initial onboarding candidate.
Its target revision is distinct from the accepted version/session fence. Base artifacts
and feedback are supplied as untrusted data; they cannot grant permissions.

Both manual and automatic repair use the same candidate/import/full-audit/adoption
path. A rejected candidate can be retried with its audit, new inputs and original
healthy-position floors. Every target must parse and old healthy rows must not regress.
During repair, admitted sources stay active and healthy jobs remain queryable. Failed
initial onboarding stays unadmitted until a passing repair audit freezes its baseline.

Full-source reviews are persisted before RPC delivery. Stable request IDs survive lost
acknowledgements, including rejected onboarding and repair candidates. Review delivery
never reruns an Agent. Callback notifications are idempotent and include revision IDs;
GetTask/GetRecipeRevision remain authoritative. Human parsing reports remain non-executing.

## Running

Use the existing configured API/source daemon and the independent Recipe daemon, or
operator CLI controls from `server/`. Model execution requires explicit opt-in:

```sh
uv run --frozen flask --app youju recipe-workspace-create SOURCE --storage PATH
uv run --frozen flask --app youju recipe-workspace-status WORKSPACE_ID --storage PATH
uv run --frozen flask --app youju recipe-task-worker --storage PATH --enable-models \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model gpt-6.1-sol --watch
uv run --frozen flask --app youju source-repair SOURCE --database DB --storage PATH
uv run --frozen flask --app youju source-repair SOURCE --database DB --storage PATH \
  --previous-request-id FAILED_REQUEST_ID
```

The source CLI uses configured `RECIPE_RPC_URL`/`RECIPE_RPC_TOKEN` when model execution
is explicitly enabled; it never falls back to a legacy local author. An initial source
with no service configuration stops with a visible configuration error.

## Schema and verification boundaries

This POC refactor intentionally breaks the old source binding schema. Initialize a
fresh source database; old source recipe pointers and old flat positions are rejected
without reset. Sidecar schema v4 preserves existing metadata, but pre-revision published
candidates are unsupported and require a fresh storage path. No live database is
silently migrated/reset and no old generated script is manually changed.

Offline synthetic tests cover independent workers, immutable revisions/reviews, source
bindings, initial/manual/incremental repair, feedback retries, rejection, stale context,
RPC loss, callbacks and job visibility. Real Linux sandbox tests require explicit opt-in.
This implementation does not imply a new real USTC/model run, deployment or WeChat upload.
Earlier experiments in other documents are historical receipts, not current commands.
