# Recipe Control Plane

## Native runtime and ownership

The managed-only service uses official **xdog 2.4.1** native skills, session dump/restore
and auto compaction. New trusted profiles default to `gpt-6.1-sol`; see
`SOL_FLOW_INTEGRATION.md` for the application Flow capability and explicit
profile/cutover boundary. Each execution
creates/restores one Agent, awaits checkpoint persistence, then closes/unloads it.
No author turn, token or whole-run deadline cap is imposed. There is no custom model
client, session serializer, compactor or workflow engine.

The service owns workspace/init/session/task/revision/sample validation. The source
server owns full-source audits and publication. Authenticated RPC handlers only
persist work; a separately configured worker performs execution.

## Internal RPC

All calls are POST JSON under `/internal/recipe/`, authenticated by a configured caller
ID and bearer token. Workspace owner, source allowlist, Agent profile and callback
routing come only from server configuration. IDs and body sizes are bounded. Source
material, filenames, provider payloads and secrets are not echoed through generic errors.

| Method | Required arguments |
| --- | --- |
| CreateWorkspace | source_id |
| InputCompleted | workspace_id, revision, manifest_digest, request_id |
| StartBuild | workspace_id, input_revision, prompt, request_id |
| StartRepair | workspace_id, input_revision, base_revision_id, base_recipe_digest, feedback, request_id |
| GetWorkspace | workspace_id |
| GetTask | task_id; optional after/limit |
| GetTaskResult | task_id |
| GetRecipeRevision | revision_id |
| ReviewRevision | workspace_id, revision_id, decision, feedback, request_id |
| CancelTask | task_id |

`decision` is only `accept` or `reject`; neither executes a task. `SubmitFeedback` is
removed. Repair requires an explicit new input and StartRepair. The service can resolve
a missing base_revision_id by workspace/digest, but source callers always pin its ID.

Request IDs are workspace-scoped and content-fingerprinted. Identical retries return
saved receipts; changing a request's content is rejected. Workspace execution locks
prevent concurrent tasks. Input/profile/session/accepted-base fences reject stale work.
Acceptance CASes the revision's accepted-base snapshot, not its repair-target pointer.

## Frozen inputs and revision publication

The server stages pre-parsed UTF-8 files in the returned input directory. `payload.json`
contains matching source_id and a stable nonempty objective. `manifest.json` maps simple
filenames to SHA-256 bytes; it must not include itself. manifest_digest is SHA-256 of
canonical JSON (sorted keys, ensure_ascii=False, compact separators, finite values).
InputCompleted rejects unsafe files and freezes their exact content. Staging mutations
cannot alter the committed InputRevision. It queues init only when preparation is needed.

Successful init must produce a real English workspace AGENTS.md and a nonempty opaque
checkpoint. Build/repair must submit actual valid files; native preflight exercises the
fast adapter and supplied samples. `candidate_ready` creates RecipeRevision and callback
in the task's metadata transaction. The revision records actual immutable artifacts,
input/profile/task IDs, target and accepted-base pins, and sample-validation attempts.
Database triggers prevent mutation/deletion. GetRecipeRevision never reads live work files.

A rejected revision remains retrievable and is a valid repair target; it cannot later be
accepted. New validation and fresh inputs must produce a new revision. Rejection never
clears an accepted version. The source imports immutable artifacts as a local SourceRecipe
and manages candidate/active IDs in its separate SourceRecipeBinding.

## Callback and review delivery

Task callbacks use an authenticated durable outbox with leases, backoff, stable event IDs
and revision_id. The source inbox records notifications without executing or adopting
anything. Duplicate delivery remains idempotent after later repairs. Polling the exact
recorded task and revision is authoritative if a callback is delayed.

The source persists a full-audit accept/reject intent before sending ReviewRevision.
Lost acknowledgements retry the same request, not a build. Repair rejection/acceptance
also have persisted synchronization states. Source-wide audit must repair selected
failures and preserve healthy position counts before any binding switch. Service review
and local adoption are separate operations; source publication does not depend on a
callback's arrival or an Agent's claims.

## Storage and compatibility

Sidecar metadata lives in `<storage>/authoring/service.sqlite3`; it never opens source
business tables. Work, input/history and content-addressed objects are isolated from
Agent access as appropriate. Schema v4 preserves metadata; old published candidates
without immutable revisions are rejected before schema writes and need fresh storage.
Source old-pointer schemas likewise require an explicit fresh path. No data is deleted
and no live conversion is automatic.

See `RECIPE_SERVICE.md` and `INCREMENTAL_REPAIR.md` for operational commands and source
lifecycle. Offline verification is not real model, browser/device or deployment proof.
