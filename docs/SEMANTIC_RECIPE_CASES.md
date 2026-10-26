# Host-Reviewed Semantic Recipe Cases

Status: implemented locally on October 23, 2026; **not deployed**.
No real USTC material was automatically labeled as gold by this task.

## Optional checks, not an onboarding prerequisite

No source-by-source manual annotation is required. Without a registered label,
normal automatic recipe tests, audits, admission and incremental repair continue;
no semantic receipt is required or invented. Independent labels add stronger checks
for confirmed defects, high-risk exceptions or optional quality sampling. Once a
label exists, its failed/stale receipt blocks that resource, not every unlabeled
source. A shared cross-source benchmark can qualify future rule/model upgrades;
its absence is a measurement limitation, not an offline POC delivery gate.

## Ownership and storage

Only a trusted host/operator registers expectations. The Recipe Agent, model
output, callback and public API cannot supply or modify gold. Expectations live
in immutable host blobs, never `/work`, `/input`, or the RecipeService case catalog.
`SemanticCaseLabel` binds source, bundle ID/digest, expectation digest, reviewer and
split. Registration appends a version; it does not rewrite extraction/audit rows,
queue a model, accept a candidate, or publish a recipe. Repeating the same label is
idempotent. Leased source operations and pending repairs block label changes.

Independent check receipts use `SemanticAuditCheck`, binding audit ID, label ID,
checker implementation and actual saved-result digest. Check output and candidate
output remain separate. A claimed passing `semantic_validation` field in feedback
cannot replace the host receipt. For a resource with a registered label, missing/stale receipts fail closed at publication
and final candidate-adoption boundaries.

## Explicit human-review command

Use an isolated environment and **copied** database/storage until deployment is
approved. From `server/`:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.artifacts/recipe-redesign-20261023/venv"
uv run --frozen flask --app youju source-accuracy-migrate \
  --database /absolute/copy.sqlite3 --storage /absolute/copied-blobs
uv run --frozen flask --app youju source-semantic-label SOURCE_ID BUNDLE_ID \
  --expected /absolute/reviewed-case.json --reviewer local-human \
  --confirm-reviewed --split regression \
  --database /absolute/copy.sqlite3 --storage /absolute/copied-blobs
```

The CLI accepts only `basis=human-confirmed`; a model suggestion is not a human
review. The Python host checker also supports explicitly verified independent-rule
fixtures, not author-supplied truth. The confirmation flag is a review acknowledgement,
not a substitute for actually checking the saved material.

Example **synthetic partial expectation**, not a real-site label:

```json
{
  "basis": "human-confirmed",
  "reference": "manual-review:case-identifier/table-row-2",
  "complete_position_set": false,
  "positions": [
    {
      "selector": {"hiring_unit": "Example University", "title": "Lab assistant"},
      "fields": {"headcount": {"value": 2, "tag": "source"}}
    }
  ]
}
```

Selectors require a title **and** unit/employer; title alone is not identity.
Omitted fields are not assertions of absence. Conditions require an explicit
`conditions` array and `condition_logic`; scope and ALL/ANY are checked. All tagged
facts use the existing `position-facts-v2` validator. No new mandatory per-field
quotation requirement is added to position output.

## What acceptance means

- A wrong asserted field or condition produces a `SEMANTIC_EXPECTATION` diagnostic
  with the underlying semantic code, case, field path, basis and label reference.
- Assertions run during actual `AuditLedger.record`, not just author self-tests.
  Results with semantic mismatches are review/quarantined, not public jobs.
- Registering a new label checks existing active saved output without mutating it.
  Public jobs and source totals immediately exclude a newly confirmed bad output.
  Healthy jobs and frozen admission history remain intact.
- Repair compares healthy baseline semantics as well as counts. Partial assertions
  authorize only their explicitly checked field/condition changes. Unchecked changes
  and ambiguous correspondence still require review.
- A complete independently reviewed position set can supersede an old count floor
  and authorize removing a distinct false job. Exhaustive field/condition assertions
  can independently resolve the entire output; partial labels cannot do so.
- Host-confirmed coarse rechecks are archival, not full semantic passes. They do not
  inherit full field/count obligations, create public jobs or become gold accuracy.

This is checked-case acceptance, not a source-wide accuracy percentage. Unlabeled
material and unchanged old output do not become truth.

## Review-to-repair routing

Changed incremental resources may queue repair from `failed` **or** `review`.
Review is not itself a repair diagnosis: an actual saved host receipt or recognized
adapter runtime error must attribute a reproducible defect to the adapter.
A model's self-reported `SEMANTIC_EXPECTATION` code without a host label cannot do so.

Classification/association uncertainty, acquisition gaps, provider errors, budgets
and unknown failures do not invite source-code repair. Semantic mismatches with
incomplete material require separate available-material attribution and do not
automatically launch a coding Agent. Existing readable partial-material runtime
repair behavior is retained. New-contract expiry exclusions use whole-scope host
evaluation, not an older helper's potentially narrower saved deadline.

Repair still uses the managed persistent Agent/session, immutable revisions, fixed
input, reliable callbacks, full saved-source audit and atomic generation/binding
switch. A callback or sample pass is not activation. Terminal incident fingerprints
remain deduplicated, including review-origin incidents.

## Holdout boundaries

`--split holdout` is only for demonstrably unseen material before source authoring;
previously authored/reanalyzed sources cannot retroactively declare unseen holdouts.
Ordinary manually reviewed live-source cases are regression cases.

Unexposed holdouts are removed from initial author samples and exported case
catalogs, but remain in host source-wide audits. Exporting a failed semantic case
for feedback/repair records `SemanticCaseExposure`. Effective split then becomes
regression, including after relabeling the same material. Repeated exposure is
idempotent. Relabeling does not manufacture a fresh independent test.

Exposure provenance for already-hosted native history predating this mechanism
cannot be inferred reliably; that is why retrospective unseen declarations are
rejected rather than assumed safe.

## Verification and remaining boundaries

`tests/test_semantic_case_ledger.py` uses explicitly synthetic expectations and
persisted SQLite records. Current verification: **475 passed/two optional skips**
in the full suite, **40 passed** in semantic/repair/binding follow-up, **11 passed**
in the latest semantic-case suite, and **two passed** in explicitly enabled real
sandbox tests. The final bounded-file CLI check also passes. It covers:

- same-count field corruption blocked by a real audit;
- distinct false-job removal accepted with independent complete assertions;
- partial-field repair without excusing an unchecked employment-type change;
- stale public output quarantined while audit history remains unchanged;
- forged/model gold, empty partial expectations and retrospective holdout declarations rejected;
- explicit human confirmation required by the CLI, with no queued model task;
- ALL/ANY and scope changes rejected while harmless condition-ID renaming remains valid;
- model-invented semantic errors, provider failures and unattributable missing material do not launch repair;
- hidden holdouts excluded from actual initial samples/catalog and exposure persisted;
- review-origin incremental repair through request, saved-source audit and adoption,
  preserving healthy admission history and acquisition records.

The last test uses explicit Recipe RPC and parser/provider doubles, not a claim
that a paid Agent repaired real source code. Existing native SDK tests separately
cover real official Agent sessions, tools, cancellation and restoration.

Copy-only migration verification preserved the exact row digests of **30 existing
tables**, added the rule-cache and three semantic tables, and was idempotent over
two invocations. It did not retag historical results, change monitoring, migrate
native checkpoints or modify production. Receipt:
`server/.artifacts/recipe-redesign-20261023/semantic-ledger-migration.json`.

Broader rejected-output pagination/provenance, progress-based stopping beyond
identical-file resubmission, source-layout artifact lifecycle, reviewed multi-source
measurement, and copy-only SDK/workspace cutover remain in the active redesign goal.
