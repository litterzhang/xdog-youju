# Unified Source Recipe Repair

## Ownership

Only the configured xdog coding Agent edits source parser/workflow artifacts. The source
server gathers and parses materials, prepares fixed inputs, audits outputs and adopts
versions. Source snippets and feedback are untrusted data and never execution authority.
The Recipe service restores native sessions/skills and generates immutable revisions.

## Entry paths

- **Automatic incremental:** recognized recipe parsing failures on new/changed,
  potentially active notices create coalesced RepairRequests. Infrastructure, model
  quotas, access policy and material warnings alone are excluded.
- **Manual audit:** an authorized operator explicitly submits a finished audit. It may
  concern an active recipe or a rejected first-onboarding candidate.
- **Feedback retry:** `previous_request_id` names a failed/superseded source request.
  The new request targets its candidate audit when available, pins new inputs and
  merges the original healthy-position floors. User reports themselves never execute.

All three paths share one generation/import/full-source audit/adoption implementation.
An admitted source stays active while its repair is queued, generating or rejected.
The old recipe and good positions are not replaced merely to start another author run.
An unadmitted source remains unadmitted until its first passing candidate audit.

## Fixed inputs and acceptance

Freeze source/profile/toolkit, base service revision/artifact, all current notice bundle
IDs, selected failures and baseline counts. Supply at most three readable representative
samples, but require every requested target to pass the source-wide audit. The Agent
reads current files/history plus the new fixed input and pinned base artifact. StartRepair
uses a new InputRevision; an unchanged target artifact cannot masquerade as a repair.

Candidate sample validation produces an immutable RecipeRevision, not acceptance. Main
server audit uses saved material and an explicit current clock, rejects stale context,
and preserves previous healthy counts. Only a passing full audit switches the binding
and publication generation. Failed audits retain the old binding and immutable history.
Accept/reject RPCs are persisted/retryable, including after lost acknowledgements.

## Operator controls

From `server/`, with explicit current DB/storage paths:

```sh
uv run --frozen flask --app youju source-repair SOURCE --database DB --storage PATH
uv run --frozen flask --app youju source-repair SOURCE --database DB --storage PATH \
  --audit-id AUDIT_ID --previous-request-id FAILED_REQUEST_ID
```

The authenticated admin `POST /api/admin/sources/<id>/repair` accepts optional audit_id
and previous_request_id. It only queues work and checks source association, existing
leases, readable samples and in-flight requests. Admin status displays active/candidate
revision IDs and bounded repair records, and exposes explicit repair for eligible active
sources as well as blocked first audits. Confirmation warns about model costs.

Monitoring remains independent and opt-in. Repair does not approve attachment domains,
redownload the baseline, erase onboarding history or bypass validators. Automatic incident
fingerprints prevent repeated paid tasks for unchanged failures. A failed request requires
reviewed feedback/retry or genuinely changed inputs, not an endless autonomous loop.

## Verification

Synthetic tests cover baseline preservation, no extra downloads, active job visibility,
new inputs, initial unaccepted targets, regression rejection, feedback retries, material
fences, callbacks and lost acknowledgements. No new real USTC Agent repair or deployment
was performed for this refactor. See `RECIPE_SERVICE.md` for current worker controls;
older command/experiment receipts elsewhere predate this breaking model change.
