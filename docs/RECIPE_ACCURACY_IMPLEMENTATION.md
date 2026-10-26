# Recipe Accuracy Implementation Progress

## Current timing policy override (October 24, 2026)

`application-evaluator-v3.0` replaces the earlier completeness-dependent expiry
policy. Acquisition status, missing attachments, partial/truncated reader output
and capture warnings do not invalidate dates or positions supported by available
text. Missing factual evidence remains unknown; actual purpose/scope conflicts,
negation and unresolved relative anchors remain timing issues. No material state
is used as a generic date veto. Historical receipts below describe their original
behavior and are not silently rewritten. See `APPLICATION_TIMING_REDESIGN.md`.


Updated: October 24, 2026. Status: **offline implementation and verification; not deployed**.
The active goal implements `RECIPE_ACCURACY_REDESIGN.md`; this receipt does not
replace the supplied guide or claim that every proposed stage is complete.

## Delivered contracts and execution

`recipe-accuracy-v1` is explicitly opt-in. Its canonical files remain
`workflow.json`, `validity.py`, `fast_parse.py`, and `config.json`.

- `periods.py` defines `application-period-v2`, literal/version references, scoped
  windows, coverage/conflict checks and a timezone-aware injected-clock evaluator.
  The adapter supplies a rule, not an authoritative expiry decision. The independent
  checker supports inclusive dates, minute/second precision, recognized calendar
  ranges, and complete recognized job-table cutoff scopes. It cannot close all jobs
  using the earliest row. Missing or unresolved scopes keep the full path.
- `accuracy.py` owns an exact fixed graph executed by official xdog-flow.
  `accuracy_runner.py` runs source scripts only through the existing OS sandbox.
  A checked expired scope uses coarse parsing; other scopes attempt a supported
  full script, then use the shared tool-free detailed extractor when necessary.
  An unavailable model is `MODEL_DISABLED`, not fake success or coarse fallback.
- Capability (`supported/unsupported/uncertain`), material completeness and host
  quality tier (`coarse/full`) are separate. Coarse output may omit positions,
  including all of them. Full output retains schema, date and known-row checks.
  The tier identifies a checked processing contract, **not measured accuracy**.
- Coarse audits do not create public Position rows, qualify as successful full
  admission, or inflate source-wide full-position totals. New accuracy recipes
  require an explicit full tier at public-query boundaries. Historical results
  remain visibly `unversioned`; they are never silently promoted to full.
- `ApplicationRuleReceipt` stores validated extracted rules, not cached expiry
  booleans. Material, candidate and evaluator keys invalidate reuse; a clock
  change recomputes validity without rerunning the adapter. Corrupt rules fail closed.
- Author validation runs the actual workflow. The universal complete/nonempty fast
  probe no longer prevents legitimate detailed fallback. The local author check
  cannot run paid detailed models and does not claim source acceptance.

## Region-first classification

Acquisition-time HTML parsing adds bounded DOM region observations and a structural
fingerprint under `materials-v3-regions`. Original blocks/IDs remain intact.
Explicit navigation/recommendation regions do not contaminate article prose;
repeated content regions can add a list role alongside article analysis. Structural
identity is not a reusable business label. Unknown pages retain review/monitoring.
Discovery inventory, scope, SSRF, depth, deduplication and crawl limits are unchanged.

Adopted classifier keys are recorded separately from candidate assessment ordering.
New admission records pin the classifier key used for public jobs; source summary
and publication no longer blindly select the newest experimental assessment.

## Frozen cases, native tools and repair diagnostics

Source preparation exports the saved case catalog through existing immutable
RecipeService blobs. Only a digest enters the bounded input manifest. InputCompleted
checks catalog/source/body identities before freezing. Initial prompt samples still
have a three-case window; paginated access reaches other saved source pages without
network or production DB access. Limits: 5,000 cases, 8 MiB per case, 128 MiB total,
2 MiB index, 20 cases per test page, and 12,000 characters per material-read page.
These are resource limits, not claims of source completeness.

The native Agent receives `list_cases`, `read_case`, `test_recipe`, and
`inspect_failure` through the official AgentTool API when a catalog exists.
Tests snapshot canonical files and bind receipts to candidate/case/input/checker/
clock/model identities. Receipts and bounded diagnostic blobs persist across runtime
restores. Identical tests reuse receipts; edits invalidate them. Gold is not exposed
or writable: the exported real catalog is explicitly development-only and unlabeled.

Repair now checks available semantic baselines in addition to counts. Unique
source unit/title/location identities permit field, tag, condition, ALL/ANY and
scope comparisons; ambiguous correspondence is reported, not invented. Baseline
changes require review and are not automatically called incorrect. Independent
reviewed-case helpers distinguish human/rule expectations from model suggestions.
Native repeated submission of the same rejected files ends with
`AUTHOR_NO_IMPROVEMENT`, preserving diagnosis without imposing a lifetime turn cap.
Candidate submission, full audit, callback and activation remain separate.

## Host-reviewed semantic acceptance update

Independent host labels now participate in actual audit, publication, repair and
final adoption rather than only a standalone checker. Explicit complete membership
can authorize removing an old false job; partial assertions permit only reviewed
field/condition changes. New semantic labels quarantine stale public output without
mutating original audit/history. Review-origin incremental failures now require a
persisted adapter-attributable diagnosis before repair.

Frozen holdouts are excluded from initial author samples and catalogs, and
feedback exposure is persisted; relabeling cannot manufacture a fresh holdout.
See `SEMANTIC_RECIPE_CASES.md` for the explicit human-review command, trust boundaries,
synthetic acceptance cases and copy-only migration receipt. No real material has
been automatically promoted to gold.

## Archived admission and detailed feedback update

All currently identified saved notices may finish onboarding as validated coarse
archives without fabricated complete positions. Archived incremental results are
labeled separately; reopening requires new material, full extraction and a new
audit. Status-only consumers cannot call coarse archives complete extraction.

Rejected script/model/semantic output is now retained separately, paginated and
bound to source/task/material. Native validation receipts and confirmed-defect
progress survive restores, without a fixed author lifetime cap. Source-side failure
records can be copied into frozen RecipeService inputs without DB/network authority.
Independent semantic checks no longer infer omissions from synthetic empty failure
envelopes. See `RECIPE_DIAGNOSTICS.md` for bounds, privacy, tests and trust boundaries.

## Anchored relative rules and associated changes update

The shared evaluator now supports literal publication-anchored natural days and
workdays under explicit host-reviewed day maps. Missing anchors/counting/calendar
coverage remain unresolved; capture time is never substituted. Reviewed notice-wide
extensions and cancellations pin original/event versions and explicit supersession.
Updated/stale dependencies quarantine prior temporal observations until re-audit.
Associated event pages remain monitored but do not create duplicate positions.

Cancellation is separate from clock expiry and runs no job parser/model. Full-source
synthetic onboarding verifies extension, cancellation and wholly withdrawn sources.
Independent tests include separate scope/conditional headings, not merely the date
sentence. See `TEMPORAL_RULES.md` for host-only commands, bounds and remaining limits.

## Explicit native cutover and evaluator correction update

An offline-copy-only fresh-session command now preserves source-scoped prior files,
metadata and opaque checkpoint bytes under read-only history. Actual 2.4.1 bytes were
rejected by the 2.4.9 restore fence, then a fresh native init/build/repair ran in
independent real-bwrap processes with synthetic provider output. No revision was
accepted or source DB changed. Sidecar v5 adds nullable `prior_context_ref`; see
`RECIPE_SDK_CUTOVER.md` for exact operations, limits, rollback and rollout boundaries.

The combined classifier evaluator previously used model-only predictions for
per-source metrics; it now runs the same rules/local/host path globally and per
source. Cheap resolved coverage, membership and safety metrics separate abstention
from confident exclusion. This correction has synthetic tests, not real benchmark
truth. See `CLASSIFICATION_EVALUATION_STATUS.md` and `REDESIGN_ACCEPTANCE_AUDIT.md`.

## Activation and migration boundary

Do not run these commands against the live environment without deployment approval.
From `server/`, use an isolated environment and an explicit copied database/storage:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.artifacts/recipe-redesign-20261023/venv"
uv run --frozen flask --app youju source-accuracy-migrate \
  --database /absolute/copy.sqlite3 --storage /absolute/copied-blobs
```

The command adds the rule-cache, three semantic-case and two temporal-review tables for the current schema; it does not retag
historical audits, enable models, reparse, generate a recipe or publish positions.
A fresh database also creates this table through the existing explicit initializer.
`YOUJU_SOURCE_RECIPE_ACCURACY=true` opts source CLI worker builds into the new
contract. The ordinary default remains off until reviewed rollout. A direct
`template_candidate(accuracy=True)` is available for isolated SDK tests.

Saved raw HTML can be replayed using the existing `source-reparse --format html`
command on copies, followed by classification and a new recipe/audit generation.
A parser namespace change must not attach old IDs to different source text.
The offline replay below checks this explicitly. No re-download is needed.

SDK migration is separate: 2.4.1 checkpoints do not silently restore as 2.4.9.
Preserve old environments/history and prepare an explicitly approved fresh sidecar/
workspace cutover. Do not relax checkpoint/skill/profile fences. The live `.venv`,
SQLite data, monitoring switch, service units and served client output were not
migrated by this implementation task.

## Verification

All commands use the isolated 2.4.9 environment; no paid provider was called.

Final October 24 automation/acceptance recheck: **515 backend tests passed, two
opt-in tests skipped**; the two real-sandbox tests separately passed. The existing
archived/reopened-source integration test now explicitly asserts zero semantic
labels and zero semantic-check records through full public admission. **114 client
tests**, TypeScript, Ruff and isolated H5/weapp builds passed (existing H5 performance
warning). The fresh 40-case saved replay reproduces the counts below. Final logs
use the `automation-` prefix under the private artifact root; this is not a paid
model experiment, new model qualification, production deployment or upload.

- Initial implementation backend full suite: **469 passed, two opt-in sandbox tests
  skipped**; focused failure/limit follow-up: **67 passed, one opt-in test skipped**.
- After semantic labels/repair integration: **475 passed, two opt-in sandbox tests
  skipped**. Later focused semantic/repair/binding checks: **40 passed**. Additional
  CLI, condition-logic and diagnosis checks: **11 passed** in the latest semantic-case
  suite. Two explicitly enabled real sandbox tests pass. These synthetic checks do
  not label real material as gold.
- Archived-admission/diagnostics/progress increment: **493 backend tests passed,
  two optional sandbox tests skipped**; both explicitly enabled real sandbox tests
  pass, and the final focused suite passes **55 tests**. Source and task-bound raw
  rejection transfer, cross-runtime validation reuse, coarse-only reopening and
  incremental quality labels are verified with explicit synthetic boundaries.
- Anchored temporal/association increment: **511 backend tests passed, two optional
  sandbox tests skipped**; final temporal boundary follow-up: **34 passed**. Both
  explicitly enabled sandbox tests pass. The copy-only migration preserves all
  existing-row digests across 30 tables and is idempotent. See `TEMPORAL_RULES.md`.
- Native fresh-session/evaluator increment: **515 backend tests passed, two optional
  sandbox skips**; both explicitly enabled sandbox tests pass. Focused cutover/
  evaluator checks pass **18 tests** and Shanghai-midnight regression checks pass
  **36 tests**. Actual old 2.4.1 bytes are preserved, not converted; real-bwrap
  independent init/build/repair processes use the fresh 2.4.9 session. See
  `RECIPE_SDK_CUTOVER.md` and the final acceptance audit.
- Client TypeScript check passed; **114 client tests passed**. Client changes only
  accept the new period/tier contract and describe scoped deadlines; no visual redesign.
- Ruff passes. Both separately enabled existing real-Linux sandbox tests pass;
  a real-sandbox synthetic replay also verifies coarse and full template execution.
  A copy-only persistent-cache check observes one rule-adapter call across a
  full-to-coarse clock transition and verifies the exact-minute public boundary.
- Native Agent tests use official sessions, skills, restore and actual
  registered domain tools with an explicit synthetic model stream.
- Staged H5/weapp builds use `YOUJU_CLIENT_OUTPUT_ROOT` under the private artifact
  directory, never the served `client/dist` root. They are not uploads or deployment.

Reproduce saved-material replay from `server/`:

```sh
uv run --frozen python scripts/replay_accuracy.py \
  --corpus .artifacts/recipe-redesign-20261023/offline-corpus \
  --snapshot .artifacts/recipe-redesign-20261023/source-snapshot.sqlite3 \
  --blobs .data/source-blobs \
  --output .artifacts/recipe-redesign-20261023/accuracy-replay.json
```

The command reads the DB snapshot and saved raw originals only, reparses HTML in
real bubblewrap, and executes the actual SDK Flow. Results: **40 saved cases**, all
40 retain the same evidence IDs/text after adding region observations; 18 resolved
recruitment articles complete script extraction, three require a disabled detailed
model, and 19 are not resolved recruitment articles in this rules-only replay.
All 40 whole-notice expiry results remain unknown under the conservative new scope
checks. Known cutoff rules remain inside application_period; missing/uncertain
scope does not authorize coarse routing. Zero network requests and model calls.

These are one-source **unlabeled** material observations, not precision/recall,
source-held-out accuracy, proof that every classification is correct, or a 95%
coverage result. Do not interpret the 18 contract-full outputs as verified gold.
Logs and diagnostics live in `server/.artifacts/recipe-redesign-20261023/`.

## Automation, acceptance and follow-up limits

New sources do **not** need manual labels. Automatic acquisition, classification,
recipe generation/testing, saved-source auditing, per-resource quarantine and
incremental repair remain the normal path. Optional reviewed expectations add
independent semantic checks only where they exist. A missing label is not a failed
semantic check, but absence of gold also does not prove semantic accuracy.

The former requirement to obtain independently annotated multiple-source data
before finishing this implementation was incorrect and is withdrawn. A reusable
cross-source benchmark is a follow-up for qualifying shared classifier/rule changes,
not a recurring onboarding cost imposed on every source. Existing real replay is
USTC-only and unlabeled: no real multi-source accuracy or 95% accuracy is claimed.
See `REDESIGN_ACCEPTANCE_AUDIT.md` for functional evidence and evidence limits.

Separately versioned source-layout authoring is conditional on demonstrated generic
region failures and remains proposed, not shipped. Copy-only native cutover and
historical-publication isolation are verified; production rollout still requires
separate approval, as do paid author/model experiments.

Unsupported temporal wording and partial changes deliberately remain full/review.
A selective period model is not enabled where its proposed rule cannot pass an
independent host check; this avoids useless repeated inference rather than treating
unknown as expired. The implemented reviewed temporal subset is described separately
and is not a claim of universal natural-language accuracy.
