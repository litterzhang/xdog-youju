# Shared Agent Runtime Field Completeness

## Scope

This change repairs shared coding/extraction Agent behavior and position-domain
validation. It does not edit USTC or any other source's generated parser, workflow,
configuration or stored jobs. No paid extraction, production reprocessing, service
restart, deployment or client upload is part of this change.

The original saved-data findings remain in `RECIPE_FIELD_QUALITY_NOTES.md`.

## Responsibilities

- `positions/completeness.py` owns a source-neutral completeness checklist and
  conservative structural diagnostics. Optional unknowns without supporting
  information are not failures. Keyword signals are not an accuracy oracle.
- Both Recipe coding Agent entry points and native extraction Agent prompts use
  that checklist: unit/employer, headcount, workplace, dates, employment/staffing
  and every supported condition type. Institution identity does not prove staffing;
  a contact address does not prove workplace; notice totals are not per-role counts.
- `recipe_runtime/runner.py` diagnoses script gaps during `runtime:inspect`, exposing
  them through the existing `coverage.needs_detailed` signal. Native Flow edges
  still decide whether to invoke AI. Explicit workplace prose supplies bounded,
  scope-unresolved attention hints, never automatic inherited facts.
- The position normalizer recomputes final gaps after recovering malformed fields.
  A model's `complete=true` or an edited Flow skipping refinement cannot conceal a
  recognizable omission. Usable jobs survive with scoped diagnostics.
- Detailed author sample checks reject recognized structural gaps, not merely
  malformed JSON. Runtime failures with an empty child-error list also reject the
  candidate instead of accidentally validating it. Quick/archived candidates can
  remain partial. Missing annexes
  alone never force an AI call. Existing input-byte and capability limits remain.

## Conditions and merge semantics

The shared bootstrap recognizes simple explicit education/degree, experience,
inclusive age bounds, graduation status/year and political-status statements.
It extracts explicit trailing title counts, retaining titles unchanged. It never
splits notice totals between roles or manufactures a workplace.

The extraction helper constructs one complete residual atom from unclassified
input clauses. Arbitrary Recipe/model output is **not** concatenated after the
fact: multiple residual conditions, including notice/local inheritance, produce
scoped feedback; model submissions with that defect are rejected.

Preferred and exceptional requirements remain verbatim residual text when the
current schema cannot safely express them. Qualitative skills do not acquire
invented experience years. Unsupported precise age boundaries remain explicit
rather than being silently converted to an inclusive rule.

Model refinement may replace opaque text and add typed atoms while preserving
existing typed IDs. Merge compares the relationships among previously known
atoms, rather than mistaking every new logic-tree node for a conflict. A real
ALL-to-ANY change among known atoms remains a conflict.

## Staffing and verification

Filing/quota management may coexist with explicit non-establishment; it no longer
creates a false competing-entitlement conflict. Explicit entitlement takes
precedence within the existing `staffing_status` field. Same-position dispatch
plus establishment is flagged even when supplied by a model.

**Contract limitation:** this patch does not add a separately searchable management
system field. The existing public staffing enum remains; coding Agents are told
to retain additional management wording in `requirements_text`. This is a runtime
correction, not a claim that the personnel data model has been fully separated.

Generic conduct/health text and preferred requirements do not create a whole-job
warning just because they contain words such as “must.” Actual typed omissions,
cardinality problems and conflicts remain visible. Unresolved eligibility
exceptions/alternatives remain field-level uncertainty; they are not treated as
harmless preferences or as a mandate to bypass schema limits in source code. Row-local diagnostics no longer
make an unrelated job inherit the announcement's aggregate field-review flag.
Dates and extraction certainty remain independent axes.

## Evidence and rollout boundary

Successful receipts now retain script/model/merged drafts up to 32,000 UTF-8 bytes
per stage. Oversized stages retain digest, byte count and explicit truncation,
not an unbounded transcript. These are untrusted extraction evidence, not tool or
acceptance authority. Failure diagnostics retain their existing bounded evidence.

An offline shared-bootstrap replay of five cached USTC announcements recovered
four explicit one-person counts. The prose-only two-role notice still requires AI;
workplaces without sufficient explicit support remain unknown. This replay did
not run source Recipes or verify model accuracy. Its receipt is
`server/.artifacts/field-quality-diagnosis-20261026/runtime-bootstrap-replay.json`.

Runtime fingerprints and author guidance identities change. Production adoption
requires the normal explicit guidance-update/session fences and re-audit; do not
silently rewrite stored acceptance receipts or existing jobs. Offline verification
results are recorded separately from live model or production claims.


## Changed files and verification scope

Runtime/domain changes are in `positions/{completeness,conditions,drafts,employment,
issues,normalization,parsing}.py`, `recipe_runtime/{runner,diagnostics}.py`, and
`jobs.py`. Coding-Agent guidance/admission changes are in
`recipe_service/{native_runtime,authoring}.py`, `onboarding/author_prompt.py`,
`onboarding/auditing.py`, and `onboarding/templates/extraction-guide.md`.

Thirteen focused offline regressions in `test_runtime_field_completeness.py`
exercise native Flow through injected script/model boundaries, completeness,
provenance, singleton residuals, merge logic, staffing, bounded evidence and
fail-closed author validation. Existing API tests now additionally assert that a
row-local dispute does not mark an unrelated expired job for review. Receipt tests
retain exact checks of old fields and additionally verify the new stage receipts.
A stale legacy-migration test assertion was aligned with the existing exact
`WORKSPACE_PROFILE_MIGRATION_REQUIRED` error code; service migration behavior and
its safety fence were not changed.

Static checks and mocked/offline integration checks are not live model accuracy
measurements, production adoption, browser testing or WeChat device verification.


### Verified results

- `uv run --directory server ruff check src tests`: passed.
- Full offline regression run: **1,459 passed, 4 skipped** (opt-in real Linux
  sandbox tests). Output: `server/.artifacts/field-quality-diagnosis-20261026/runtime-full-pytest.log`.
- The final focused runtime regression file, including the additional fail-closed
  author test, is verified separately. Counts overlap; do not add suite totals.
- Cached-material bootstrap: five saved announcements, four explicit title counts
  recovered, no model/network/production writes. The receipt's extraction-runtime
  fingerprint matches the final shared runtime code.
