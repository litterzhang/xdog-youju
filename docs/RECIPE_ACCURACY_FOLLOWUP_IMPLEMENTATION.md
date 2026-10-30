# Recipe Accuracy Follow-up Implementation

## Scope and ownership

Implemented against `RECIPE_ACCURACY_FOLLOWUP_20261030.md`. These are shared
runtime/control-plane changes, not a USTC Recipe patch. No dependency upgrade,
paid-model experiment, production write, deployment or historical reparse is part
of this delivery. Existing notice cost categories, quick/detailed routing, the
1,000,000-byte extraction input ceiling and candidate visibility are unchanged.

| Owner | Implementation |
| --- | --- |
| Position domain | `positions/requirement_coverage.py`, `completeness.py`: pure, scoped attention requests, never inferred facts |
| Refinement domain | `positions/corrections.py`, `drafts.py`: explicit proposals, old-value checks and safe merge |
| Extraction runtime | `recipe_runtime/runner.py`: pinned material, Flow execution and correction receipts |
| Recipe acceptance | `recipe_service/acceptance.py`, `authoring.py`: host-selected saved cases and shared semantic comparisons |
| Source application | `onboarding/semantic_labels.py`: existing label/exposure ledger and trusted regression export |
| Source lifecycle | `onboarding/recipe_versions.py`, `recipe_bridge.py`, `pipeline.py`: audit epochs versus terminal service revision review |

Pure semantic assertions/comparisons now live in `positions/expectations.py` and
`positions/semantics.py`. The source audit imports these same functions; Recipe
acceptance does not query the source database or create a second case/label store.

## Requirement coverage

Each position's declared requirement text is compared with applicable common/local
conditions. Existing `other`, education or an unrelated position cannot mask a
missing requirement. Narrow literal value checks cover education levels, numeric
experience, inclusive age bounds and explicit major lists; alternatives also need
an appropriate logic graph. Diagnostics carry stable position key, field path,
character offsets, excerpt, suspected types and reason. Preferences are not hard
requirements; teaching doctoral students is not an applicant doctorate requirement.

The existing `runtime:inspect` coverage signal reaches the existing Flow branch.
The same checks run after merging/normalization. A complete script still avoids AI;
quick extraction never invokes it. These conservative diagnostics are not a semantic
oracle, a taxonomy matcher or an accuracy metric. Complex scope, synonyms,
exceptions and relationships can remain unresolved rather than becoming facts.

## Explicit correction contract

`position-draft-v1` has an optional `corrections` array. The bounded operations are:

- `replace_field`: stable position key, exact tagged `before`/`after`, business field;
- `withdraw_position`: stable key, exact old position in `before`, null field/after.

Each proposal includes bundle ID, reason and literal evidence: saved document
`version_id`, pointer `/blocks/N/text` or `/tables/N/rows/R/C`, zero-based character
`start`/`end` and exact quote. One operation per position per refinement avoids an
ambiguous edit ordering. Projected date fields and condition graphs are not an
arbitrary editing API. Ordinary condition refinement keeps existing safeguards.

The host checks identity, old values, versions, locations and contract; an existing
quote does **not** prove the semantic claim. Applied/rejected proposals retain old
values and AI judgment provenance in `execution.corrections`. Rejected proposals
preserve the old row and emit `DRAFT_CORRECTION_REJECTED`. Omitted rows are never
implicitly deleted. Scripts cannot propose corrections, and unapplied proposals
cannot normalize directly into business facts. Ordinary job fields still do not
require per-field quotations.

## Independent submission regressions

The host freezes up to eight cases outside the 1–3 startup samples before authoring:
known regression cases first, with room for format/template diversity. Selection
is deterministic and uses existing frozen case blobs. Host-owned expected values
are private content-addressed objects, not author-editable input files or tool
answers. The input snapshot pins only their manifest reference; validation cache
identity includes that reference and checker version.

Native `submit_recipe` runs startup validation and then the frozen regression set.
A failed semantic assertion rejects submission with case/field feedback. Successful
managed publication requires the matching acceptance receipt. Existing source
semantic labels/checks remain the storage authority; model opinions cannot register
as gold. No new mandatory manual-labeling stage is introduced.

Reports separate selected/executed cases, labeled/evaluated/passing semantic cases,
formats/templates, model calls, reported tokens and unknown monetary cost.
Unlabeled replay is never part of a semantic accuracy denominator. A source with no
extra cases reports `no_independent_cases`, not independent accuracy. Exported
labels are regression labels only; unexposed holdouts remain with the source audit.
Repeated regression passes are not unseen generalization.

## Revision review and runtime audit

A completed failed audit must match source, current candidate, audit pointer and
candidate generation before it can request source revision rejection. Reusing an
already-active service revision requires identical artifacts/workspace and never
queues a second accept/reject. Runtime failure remains in its audit and candidate;
activation remains blocked. New revision review and service terminal protection
remain unchanged. Activation also checks the actual audit/result generation.

Legacy pending rejects are not deleted or acknowledged as success. Read-only
inspection is available without initializing/migrating the database:

```bash
uv run --project server --no-sync flask --app youju source-recipe-review-diagnose \
  SOURCE_ID --database /absolute/path/to/runtime.sqlite3
```

A conflicting intent raises `RECIPE_RUNTIME_REVIEW_CONFLICT`. Any reconciliation
requires separate authorization: back up both stores, verify immutable artifacts,
audit/generation and saved terminal review, then explicitly reconcile only the
source-side intent while preserving its prior value and the service history. There
is intentionally no automatic reset or conflict-swallowing repair command.

## Traceable offline evidence

`server/tests/fixtures/requirement_omissions.json` contains only relevant public
paragraphs for “科技成果转化管理” and “科研平台管理”, with original bundle/body digest,
page version, block ID and locator. No production data was changed to obtain it.

| Case | Previously missed | Offline result after correction |
| --- | --- | --- |
| Technology transfer management | An unrelated common `other` hid master's education, listed majors and two years of experience | Located diagnostics trigger Flow completion; a mocked, text-supported refinement retains typed education/major/experience plus one residual |
| Research platform management | Same masking, including explicit two-year platform-management experience | Same branch and post-merge checks; no remaining requirement-coverage gap for the scoped fixture |

These demonstrate routing/merge behavior with a mocked extraction model, **not**
that a real provider now extracts correctly. False-job withdrawal, title repair,
wrong versions/keys/quotes, conflicts, source review idempotence, stale generations,
private-label isolation and native SDK submission rejection are offline regressions.
No universal accuracy percentage or additional format support is claimed.

## Verification and rollout boundary

Verification on October 30, 2026:

- Service suite: `uv run --project server --no-sync pytest server/tests -q --tb=short`
  — **1501 passed, 4 skipped** (explicit opt-in Linux sandbox integrations).
- Final focused files: `test_recipe_followup_lifecycle.py`,
  `test_requirement_coverage.py`, `test_draft_corrections.py`,
  `test_submission_acceptance.py` — **38 passed**. This includes final edge cases
  added during the full-suite run; the counts overlap and must not be added.
- `uv run --project server --no-sync ruff check server/src server/tests` passed.
- `git diff --check` passed.

No live provider, production replay, browser/client build or real sandbox opt-in
was used for this backend-only verification.

Deploying this code would invalidate affected runtime/checker cache identities;
it does not rewrite existing jobs or immutable Recipe artifacts. The author guide
also changes. Restoring an existing checkpoint therefore still requires the
existing explicit `recipe-task-worker --approve-guidance-update` maintenance
approval (or a trusted custom factory's equivalent); no checkpoint migration or
permission relaxation is automatic. Historical repair
or re-extraction needs its own bounded plan, consistent backups, rollback procedure
and explicit production authorization.
