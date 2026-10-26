# Automatic Incremental Recipe Repair

## Trigger and ownership

A completed incremental audit creates a durable `RepairRequest` for new or changed,
readable recruitment notices whose extraction failed with a recognized recipe/output
error. `RepairIncident` pins the incremental run, immutable bundle and audit result.
Changes to attachments count through the bundle identity, even when HTML is unchanged.
Re-auditing an unchanged historical failure does not create another request.

Incremental discovery is not proof of current validity. Independently observed expiry
excludes a notice from automatic authoring; not-expired and unknown notices are eligible.
Unknown validity must not take the expired fast path. Expired notices still use the
existing deterministic archival parser and retain errors if that parser fails.

Output-schema, validity/evidence and known isolated script-code errors are eligible.
Fetch/policy failures, optional missing attachments, classifier/association uncertainty,
model quotas/timeouts, and sandbox/provider infrastructure failures are not. A typed
script exception distinguishes known Python code/data errors from sandbox permission,
memory, startup and unknown failures. Repair never grants additional capabilities.
Human parsing feedback remains non-executing and does not automatically become a task.

## Durable lifecycle

```text
queued -> preparing -> submitted -> auditing -> accepting -> succeeded
                                    \-> rejecting -> failed
                    \---------------> failed / superseded
```

Before dispatch, the source worker freezes current notice IDs, original healthy
position-count floors, the explicit base service revision/artifact and active binding snapshot, classifier/toolkit profiles and
up to three representative saved samples. Queued failures coalesce; submitted inputs
are never amended. The source worker serializes requests using its existing source
process lock. Incident fingerprints prevent repeated paid tasks for the same
source/material/base/profile, including after a failed repair. New material or a new
active recipe can create a new incident; terminal requests are not blindly retried.

The worker calls `InputCompleted`, then authenticated `StartRepair` with the **new**
input revision and base revision/digest. Stable request IDs make lost acknowledgements
retryable. The service restores the existing xdog 2.4.1 Agent session and native skills,
runs its configured `gpt-6-astra` author and persists/unloads it afterward. No author
turn, token or whole-run deadline cap is added. Existing model opt-in, cancellation,
tool safety and ordinary extraction budgets remain in effect.

`candidate_ready` is not acceptance. The source worker validates the immutable result,
then incrementally audits every frozen current notice using saved materials. All
reported targets must parse, and previously healthy notices must retain their position
counts. Candidate audits use their own generation; active jobs and frozen onboarding
remain available throughout preparation and failed regression audits. Material/profile
changes supersede stale work. Authoring may span days: audit validity uses the current
explicit clock rather than the sample-preparation date.

Only a passing full audit atomically switches active recipe/generation. A retryable
acceptance RPC then updates the service binding. Existing callbacks report task
completion; polling retrieves exact task/results independently of callback delivery.
Monitoring can resume after repair-related blocks, without changing its enabled flag
or clearing unrelated policy/provider blocks. Acquisition is paused during pending
repair to avoid racing frozen inputs; repair progresses even when monitoring is blocked.

## Inspection and verification boundaries

Source status and administrator incremental projections expose bounded `repairs`
entries (request/task IDs, state, base/candidate IDs and safe error codes), never physical
paths, input excerpts, conversation snapshots or recipe files. Incremental run summaries
contain `repair_request_ids`. The administrator source status screen shows active/candidate revisions and recent repair records; no separate recipe editor is introduced.

Use a fresh source database initialized with the current schema. Repair/binding tables are
created by the explicit database initializer, not on API startup; old source-pointer
and position schemas remain unsupported. Existing POC databases/workspaces are not reset or migrated.

Offline checks from `server/`:

```sh
uv run --frozen ruff check src tests
uv run --frozen pytest -q tests/test_auto_repair.py tests/test_recipe_lifecycle.py \
  tests/test_recipe_rpc.py tests/test_lifecycle.py tests/test_background.py \
  tests/test_source_repair.py tests/test_xdog_recipes.py
```

These cover incremental triggering, eligibility/exclusions, coalescing/deduplication,
new input/base pins, lost RPC acknowledgements, full saved-material regression audits,
stale material rejection and public-job visibility after adoption. Test doubles supply
Agent output; they do not establish real USTC repair quality. No live author execution,
service restart, deployment, database reset or WeChat upload is implied.


## Unified manual and feedback paths

Manual audit requests and failed-request feedback retries use the same coordinator and
fixed-input StartRepair as automatic requests. Repair targets may be rejected/unaccepted
revisions while the service's accepted-base session fence remains separately pinned.
A failed first onboarding audit can therefore be repaired without restarting inventory.
Rejected full audits persist their synchronization state before RPC, so acknowledgements
can be lost without forgetting the review. See `SOURCE_REPAIR.md` for operator entry points.
