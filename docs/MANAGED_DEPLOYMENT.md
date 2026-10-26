# Managed Onboarding Deployment

## Runtime boundary

Production onboarding no longer depends on foreground CLI terminals. Three enabled
systemd units run from the project's uv-managed environment:

- `youju-api.service`: loopback Waitress on port 8089; authentication, registration,
  approval, queueing, status and the authenticated callback receiver.
- `youju-source-worker.service`: asynchronous bounded acquisition/classification,
  managed Recipe RPC submission, source-wide audit and due incrementals. It is a
  companion of the API unit and restarts with it, not an HTTP request thread.
- `youju-recipe.service`: independent loopback RPC on port 8090, native xdog 2.4.1
  init/build execution and durable callback outbox delivery. It stays independent
  during API restarts. Idle workspaces hold no live Agent.

The application factory still starts no processes, performs no model calls and
creates no database. Installing/enabling these units is an explicit deployment.
The old source CLI/legacy authoring path remains for existing POC databases; it is
not silently migrated or used by the production daemon.

## Source lifecycle

An administrator registers an organization/source, records official evidence,
then submits onboarding. Approved sources with persisted pipelines are dynamically
recognized by Recipe RPC; candidate, paused and unknown sources are denied.

The source worker creates a managed workspace, writes up to three pre-parsed
samples and a manifest, commits InputCompleted, then submits StartBuild. Init
creates workspace AGENTS.md and persists the native Agent session; build restores
it. Source intent and request IDs are saved before side effects. An acknowledgement
lost after submission can be retried without duplicate tasks or input revisions.

Callbacks are authenticated, idempotently stored in a separate source-side inbox
and never activate a recipe. The source worker retrieves the immutable result
through RPC, validates/imports it, runs the existing full audit and quarantine
policy, then sends acceptance feedback. Polling reconciles delayed callbacks.
Blocked work remains visible rather than silently starting paid retries. Abandoned
running Recipe tasks are marked interrupted only after proving the workspace has
no live OS-lock owner; an interrupted task is not automatically replayed.

## Configuration and operations

The private server `.env` holds independent RPC/callback secrets and the explicit
`YOUJU_WORKERS_ENABLE_MODELS=true` opt-in. Authoring uses `gpt-6-astra`; ordinary
classification/extraction retain their existing budgets. Agent authoring has no
host turn, total-token or whole-run deadline cap. The reviewed JSON local classifier
is copied into private `.data`; no pickle/code model is loaded.

Production passes process ten resources with concurrency two and idle polling every
three seconds. API-started inventories have a 4,000-resource cap and depth 100;
hitting a cap remains an explicit incomplete-inventory blocker, not full coverage.
Discovery and attachment access retain existing reviewed scope/SSRF restrictions.
Internal routes are not exposed by the public Nginx site. Both daemons probe the
isolation sandbox before reporting ready, including inside their service context.

```sh
systemctl status youju-api.service youju-source-worker.service youju-recipe.service
sudo journalctl -u youju-source-worker.service -u youju-recipe.service -f
```

## Verification — October 14, 2026

All three units were installed/enabled and verified active. Synthetic tests cover
RPC init/build, process-state recreation, lost build acknowledgement, callbacks,
audit/acceptance, dynamic approval scope, lock contention and transport failure.
The full server suite passed 196 tests with two opt-in sandbox tests skipped; final
profile mapping also passed its focused tests. Ruff passed. A single real small-model
structured connectivity check succeeded. Actual service startup passed the sandbox
probe; loopback/public health and internal authentication checks passed.

Receipts, private rollback copies and a consistent product DB backup are under
`server/.artifacts/managed-rollout-20261014/`. No historical catalog/materials were
imported and no real source was started by deployment. A new live source-wide loop
and user/device behavior await administrator submission and verification.


## Blocked-source recovery — October 14, 2026

The source status response now includes bounded recovery options and explicit
inventory limit codes even for older records missing an error field. Admin-only
POST `/api/admin/sources/<id>/resume` can increase depth up to 100 and resource
budget up to 10,000. It replays saved discovery material and requeues eligible
unfinished slots, not completed downloads. Depth 101 remains excluded even after
retry at the cap. Active leases, running author tasks, inactive sources and
unsupported limits reject recovery rather than force-taking ownership.

POST `/api/admin/sources/<id>/repair` uses the existing audit-driven Agent repair
entrance; it requires a settled eligible audit/recipe and never executes models
or activates artifacts in the request. Full source audit remains required.
The status page explains blockers, confirms recovery/costs, supports cancellation,
and shows explicit operator review when no safe action is available.

The live USTC source was resumed from depth 20 to 100 with all 480 completed slots
preserved. Subsequent inventory work was observed. Default/new-source depth is 100.
The full server suite passed 200 tests with two opt-in tests skipped; fourteen
client tests, TypeScript, Ruff, staged H5 and official weapp upload passed. Synthetic
browser checks covered visible depth reason, cancellation, confirmed recovery and
the maximum-depth review path. WeChat **0.1.4** uploaded at **14:46:01 Asia/Shanghai**,
size **377.75390625 KB**. No review/release or live H5 replacement was submitted.
Artifacts: `server/.artifacts/depth-recovery-20261014/` and
`server/.artifacts/wechat-upload-0.1.4-20261014/`.

A later consistent read-only domain snapshot is preserved in `domain-audit.json`.
Every assigned source URL and completed fetch/final response in that snapshot is
on HTTPS employment.ustc.edu.cn. Other USTC subdomain URLs are discovery candidates,
not this source's scheduled acquisitions. Resource inventory is not announcement
or position count; page classification has not completed in this live snapshot.
