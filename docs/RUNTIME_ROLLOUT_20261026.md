# Shared Runtime Rollout — October 26, 2026

## Authorized scope and safeguards

The user authorized backend deployment, deletion of production job entities,
re-extraction from saved material, and high-tier AI review of prior uncertain
announcements. This operation does not change source-generated Recipe artifacts,
submit a WeChat build, enable incremental crawling, or treat AI opinions as gold.

Consistent private SQLite backups were integrity-checked before review and before
job deletion. Operational evidence is under
`server/.artifacts/runtime-rollout-20261026-141045/`; backups have mode 0600.
The source writer lock excludes ordinary background work during replay.

The reset deleted **336** Position rows. There were **zero** user feedback records;
cleanup would refuse rather than silently delete feedback. Source registrations,
resources, material versions, parsed originals, Recipes, parse/audit history and
attachments were retained. Protected table counts and SQLite foreign keys were
checked in the deletion transaction. Original jobs are not restored implicitly.

## Advanced AI review before reset

Both review paths use the existing official xdog integration and `gpt-6.1-sol`.
Resource review uses persisted, at-most-once batch reservations and independent
per-page evidence validation. Of 123 previously uncertain page assessments:

- 44 valid decisions were adopted: 41 reports, two recruitment-related resources,
  and one unrelated resource.
- 54 answers failed existing evidence/intent safety checks; five were inconclusive.
- 20 pages remained unreadable/capture-limited. Raising the bounded input allowance
  from 128 KB to 256 KB did not make those pages readable.

Fifteen paid batches were reserved under an explicit sixteen-batch ceiling.
The ordinary rules/local coverage target was not relabeled as achieved, and no
ongoing high-tier auto-review configuration was silently enabled.

Separately, all 19 prior extraction-review announcements were submitted to the
independent auditor in three grouped cases. Two groups returned no submission;
an explicit three-case smaller-batch recovery was recorded, not a hidden retry
or fabricated success. Valid opinions cover **12** announcements; **seven** still
lack an accepted audit result. Across six total case dispatches, two cases
completed, two returned no submission, and two failed result validation. Valid
opinions contain eleven suspected errors, ten uncertain findings and two supported
findings. These overlapping field findings are not job or accuracy counts.
AI findings never directly overwrite position fields or create human labels.

## Re-extraction and version boundaries

The same service revision `d8c0b1220fdb44bc8b8ce4b11023973b` is reused, with unchanged
artifact digest `1f38dd13f15f4da70a2b685321eebf7d75a17882ea347924a421349519654811`.
A distinct runtime-side Recipe binding records the new shared runtime fingerprint
instead of rewriting an old validation receipt. Generation **1492** audits the
184 current announcement candidates, including the new advanced-review candidate.

Two shared maintenance corrections were required:

- Re-admission of the same immutable service revision preserves its terminal
  service review. Artifact bytes and workspace must match; a changed artifact
  cannot masquerade as the previously accepted revision. All current full-source
  admission and semantic checks still run.
- Saved-only audits explicitly disable acquisition retry scheduling. Ordinary
  audits retain their existing default behavior. Re-extraction is asynchronous
  with concurrency two, using the standard Recipe runtime and audit ledger.

The advanced audit also identified the explicit wording `事业编制职工`; the shared
employment helper now recognizes it without a source-specific script edit.
Missing material, unresolved dates, unsupported layouts and model failures remain
visible. No source-authoring build/repair is automatically started by this replay.

## Verification boundary

Ruff passed. The post-maintenance focused server suite passed **165 tests**,
covering runtime completeness, staffing, admission, unchanged-revision readmission,
saved-only retries and Recipe ownership. Live review and replay are separate from
those offline checks. Final replay/public API receipts are recorded in the
operation directory; completion is not inferred merely from process startup.


## Durable background handoff and observed deployment

The one-off operator replay controller was deliberately cancelled for handoff to
`youju-source-worker.service`; its already committed audit rows are reused by the
same Recipe/generation cache. Up to two in-flight calls may have been cancelled;
they were not fabricated as completed results. The handoff is recorded separately
from an extraction failure. The source worker owns the writer lock and continues
without depending on the operator terminal.

Background `Pipeline.step` honors saved-only replay only when both candidate
Recipe ID and generation match the recorded maintenance request. A different
candidate/epoch cannot inherit that acquisition policy. Activation marks that
maintenance request complete only after normal admission succeeds.

At **14:36:24 Asia/Shanghai**, generation 1492 had committed **9/184** announcement
results and **54** fresh Position rows. The public jobs endpoint also returned 54.
API, Recipe and public-proxy health endpoints returned HTTP 200. Old Position IDs
were absent; foreign keys passed; original material/version/bundle and fetch-attempt
counts were unchanged. Incremental monitoring remained disabled.

**Re-extraction is still running, not finished.** The receipt is
`deployment-receipt.json` in the operation directory; these are timestamped progress
counts, not a final funnel or a field-accuracy claim. Known remaining diagnostics
include unsupported qualification exceptions, layout gaps, and heuristic condition
signals that can over-identify teaching-related degree wording. Those warnings
were not hidden or treated as independent semantic truth. No source Recipe was
edited to force this deployment to pass.
