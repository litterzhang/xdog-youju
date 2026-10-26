# Explicit Native SDK Fresh-Session Cutover

Date: October 24, 2026 (Asia/Shanghai). Status: implemented and verified on copies;
**not deployed**. The original 2.4.1 environment, sidecar and source DB are not migrated.

## Policy: preserve history, never reinterpret an old runtime

A 2.4.1 checkpoint is not a 2.4.9 checkpoint with a renamed skill. SDK, provider,
source, skill and profile bindings still reject mismatches. The chosen migration is
an explicitly **fresh native session**, retaining old conversation/files as opaque
untrusted history. There is no decode/restore/replay of old tool calls, callbacks,
accepted revisions or scheduler state. This is separate from a same-SDK restore.

The new sidecar workspace has `prior_context_ref`, `checkpoint_ref=null`,
`session_revision=0` and `waiting_input` status. Old AGENTS.md/scripts do not overwrite
current `/work` files. They are archived under read-only `/history/prior-context/`.
InputCompleted pins the prior-context reference into the new immutable input; new
native snapshots bind that reference as well as the ordinary guidance/SDK pins.
Fresh init still creates current AGENTS.md before prepared/build execution.

## Operator preparation

First drain/cancel/recover old jobs under the existing runtime, then create a
consistent **offline** snapshot of sidecar metadata and workspace/object files.
A SQLite backup alone does not snapshot files; preserve both under an operator-owned
copy path. Keep the original environment and backups for rollback.

The new command has no production-path default. From `server/`, select the isolated
environment and disjoint COPY roots:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.artifacts/recipe-redesign-20261023/venv"
uv run --frozen flask --app youju recipe-prepare-fresh-session SOURCE_ID \
  --snapshot-storage /absolute/offline-snapshot \
  --new-storage /absolute/fresh-sidecar-storage --owner source-server \
  --confirm-offline-snapshot
```

An optional `--profile /absolute/current-profile.json` supplies an explicit current
host profile. Old skills/provider settings are archived, never inherited as
execution authority. The current author default is gpt-6.1-sol; configuring a profile
or preparing a workspace does not call a model.

The old/new roots cannot overlap/alias. The exporter opens old metadata read-only,
checks exact source/owner identity, rejects nonterminal jobs and live workspace
owners, and uses only workspace-scoped metadata rows plus known referenced objects.
It ignores stored physical paths for file access: copying a DB cannot redirect it
to the original live directory. Directory descriptors/no-follow reads reject
symlinks, special files and hard links. File checksums and before/after identity
checks prevent silent changing inputs. Resource bounds are explicit: 20 MiB/file,
128 MiB/archive, 10,000 files and bounded metadata/event rows. Missing optional old
objects are enumerated; a referenced missing native checkpoint is fatal, not a
claim of preserved conversation.

No Agent starts. Next prepare current source inputs under the returned directory,
invoke existing InputCompleted, then use existing fresh init/build/repair controls.
The ordinary task worker requires explicit live-model opt-in. Candidate acceptance
and full-source business publication remain separate.

## Sidecar schema and immutable binding

Sidecar schema **v5** adds nullable `workspaces.prior_context_ref` transactionally.
Earlier workspaces preserve all existing data and get null; there is no source DB
schema change in this increment. An import is permitted only before InputCompleted/
init. Repeating the exact offline import is idempotent; changed prior history or an
already-started workspace is rejected. Existing publication/review tables remain
append-only. A failed preparation can leave diagnostic archive files, never a
prepared session, active checkpoint or accepted recipe.

`recipe-workspace-status` exposes the reference, not archived content. There is no
public RPC import endpoint and no new author permission. The Agent may inspect
archived data but cannot write history, trust its instructions or inherit old tools.

## Verification with actual preserved 2.4.1 bytes

The controlled verifier uses official native Agent, skill, Flow and filesystem
implementations in real bubblewrap, replacing **only provider output** with an
explicit synthetic stream. It is not an LLM accuracy or real source-repair claim.

```sh
uv run --frozen python scripts/verify_recipe_cutover.py \
  --snapshot-storage /absolute/offline-snapshot \
  --new-storage /absolute/fresh-verification-storage --source-id SOURCE_ID \
  --owner source-server --sample /absolute/frozen-saved-case.json \
  --output /absolute/verification-receipt.json --confirm-offline-snapshot
```

The real source-scoped snapshot verified the following:

- old checkpoint binding says 2.4.1 and old skill settings; 2.4.9 returns
  `NATIVE_CHECKPOINT_MISMATCH` before prompting/restoring it;
- **297 files/objects, 21,200,349 bytes** of prior source context are preserved as
  checksummed opaque data; offline snapshot bytes remain unchanged;
- fresh init reads prior index/status, writes current English AGENTS.md and dumps
  a 2.4.9 snapshot tied to `prior_context_ref`;
- **independent init/build/repair processes** restore only the new 2.4.9 session;
  current scripts run in the real OS sandbox on one pinned saved USTC sample;
- init succeeds, build/repair become candidate_ready, session snapshots advance;
  no revision is accepted, no source DB changes and no provider/network experiment
  or production callback delivery occurs.

The repair fixture only modifies/revalidates a current template to test restoration;
it does **not** claim a semantic fix or full-source audit. Receipt:
`server/.artifacts/recipe-redesign-20261023/sdk-cutover-real-history.json`.

Synthetic tests additionally cover owner/root mismatch, symlinks, missing checkpoint,
idempotence and fresh-init ordering. Existing checkpoint/compaction/cancel tests
continue to verify same-SDK behavior. No 2.4.1 snapshot conversion is claimed.

## Rollout and rollback still need separate approval

Before production, back up source/sidecar DBs, raw blobs, static output, service
configuration and old environment. Coordinate new source storage/RPC paths with
new sidecar paths; workspace physical paths and source bindings are intentional
fences. Re-generate/audit under the current contract before any public positions
are admitted. Do not transfer accepted pointers or silently upgrade historical
coarse/unversioned facts. Keep monitoring disabled until explicitly reviewed.

Rollback uses the preserved old roots/environment, not deleting the fresh archive.
It must not republish newly quarantined/cancelled jobs or ignore newer confirmed
changes. This command prepares context only; it is not a service restart, deploy,
source activation, client upload or production rollout authorization.

Recorded final backend verification: **515 passed, two optional sandbox skips**.
Both real sandbox tests separately enabled and passed; cutover/evaluator focused
checks pass **18 tests**, and the Shanghai-midnight regression follow-up passes
**36 tests**. The real-history CLI also prepares a fresh waiting-input workspace
without starting an Agent. Copied source data retains **2,952 historical Position
rows** and its original active binding; no historical audit receives a new full tag
and current-contract public queries admit no stale old result. Exact existing-row
digests across all 30 source tables remain unchanged. This is old-data isolation,
not a semantic accuracy claim or source publication.

Final receipt:
`server/.artifacts/recipe-redesign-20261023/cutover-evaluation-verification.json`.
