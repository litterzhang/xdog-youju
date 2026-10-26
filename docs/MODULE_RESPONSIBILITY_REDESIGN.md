# Runtime and Recipe Responsibility Boundaries

Date: October 25, 2026. Status: implemented and verified locally; see
`MODULE_REDESIGN_VERIFICATION.md`. No production migration, deployment, model experiment or upload is implied.

## Ownership and dependency direction

```text
Acquisition -> versioned parsed material -> classification
                                            |
                                            v
                              notices.decide_route (cost only)
                                            |
                                            v
                             extraction_service (application wiring)
                                            |
                                            v
                            recipe_runtime.runner (official Flow)
                              parser -> optional detailed extractor
                                            |
                                            v
                        positions.changes -> normalization -> validation
                                            |
                                            v
                           source application -> SQL/admission/query

RecipeService -> injected runtime extension -> source query API
                   source API -> control metadata RPC -> RecipeService
```

The reverse metadata check never acquires a workspace execution lock. It verifies
current task ownership/fencing through authenticated RPC, not private table reads.

| Component | Owner | Forbidden responsibility |
| --- | --- | --- |
| `material_contracts.py` | Parsed data/capture diagnostic projection | Downloading, models, expiry |
| `notices/contracts.py`, `routing.py` | Fallible cost policy | Candidate job dates/publication |
| `extraction_service.py` | Injected model/route/material wiring | Source DAO or author lifecycle |
| `recipe_runtime/contracts.py`, `runner.py` | Artifact checks and official Flow | Registry, DB, publication |
| `recipe_runtime/materials.py` | Separate frozen fact context from parser input | Constructing review authority |
| `positions/drafts.py` | Standard candidate data and key-based refinement | Source-specific field types |
| `positions/deadlines.py`, `conditions.py`, `employment.py` | Shared factual vocabulary | Cost routing or providers |
| `positions/changes.py` | Literal effects of already-associated changes | Inventing associations |
| `positions/relative_dates.py` | Literal publication/count/calendar arithmetic | Capture-time or holiday guesses |
| `positions/normalization.py`, `extraction.py` | Merge, type/invariant checks | Re-extracting a gold answer |
| `recipe_service/authoring.py`, `native_runtime.py` | Generation/testing and SDK sessions | Source DB or source acceptance |
| `runtime_queries.py` | Authorized source reads | Sidecar tables, parsing, writes |
| `onboarding/pipeline.py`, `auditing.py` | Persist/audit/admit source output | Redefining position semantics |
| `jobs.py` | Public saved-fact filtering | Notice route expiry or model calls |

`onboarding/recipes.py` is only a current-symbol facade; there is one shared runtime
and one authoring implementation, not a second legacy execution engine. Existing
stateless SDK/workspace infrastructure is reused rather than copied.

## Active contracts

A Recipe has `workflow.json`, `parser.py`, `config.json`; old validity adapters and
legacy business versions are rejected. `parse(ctx,bundle,config)` returns
`position-draft-v1`. Script and detailed extraction share that contract. Domain-owned
projections (`deadline_on`, `application_start_on`, `application_dates`) cannot be
submitted in draft fields; they are produced exclusively from structured `dates`.
The final source-neutral model is `position-facts-v3`, including an optional official
`position_code`, tagged values and atomic typed conditions. No per-field quotation
requirement is imposed. Original material/version links remain available.

Five route categories are `fixed_active`, `fixed_expired`, `undated_low_priority`,
`undated_high_priority`, `unknown`. Their output is exactly category/mode/reason/
policy-version/evaluation-time. They are not hiring or precise calendar assertions.
Known quick rows have the same persistence/public-query gates as detailed rows.
Zero-row cheap archives describe processing coverage, never proven notice closure.

## Facts, unknowns and refinement

- Available positions and dates survive failed annexes, primary errors and truncation.
  Capture diagnostics are recomputed from pinned input; model completeness claims
  cannot certify missing material. Readable incomplete bundles can be author examples.
- Common dates inherit only where scope is supported; row dates and explicit unknown
  overrides remain separate. Qualifications/interview dates are not signup cutoffs.
- Missing years remain partial dates. An unambiguous same-year written range is source
  normalization; contextual model hypotheses remain inferred and never exact SQL facets.
- Script capability gaps are not gold. Detailed extraction may refine opaque `other`
  clauses into typed atoms. Initial limitations stay in execution diagnostics rather
  than vetoing a resolved date. Genuine competing facts remain explicit uncertainty.
- Unknown condition relationships use `condition_logic=null` while retaining typed
  atoms, never fabricated AND/OR. Common/local scope belongs to their candidate groups.
- Staffing entitlement is not employment mechanism, institution identity or duration.
  Negated/conflicting declarations stay unresolved rather than taking a first keyword.
- Supported relative deadlines use literal publication anchors and reviewed contiguous
  workday maps, with calendar dependencies that invalidate stale derived output.
- Reviewed whole-notice extension/cancellation uses version-bound `fact_dependencies`.
  The source validates associations; the domain validates literal scope/effect. A
  metadata flag alone cannot cancel jobs. Unsupported/stale changes retain job facts
  but withhold potentially obsolete deadlines/publication until resolved.

## Agent tools and failure evidence

The managed SDK author owns skills, official Flow capability, compaction and snapshots.
Bash/filesystem/Flow execute inside the existing sandbox. Tasks persist and close their
runtime; owned workspace IO is awaited before assignment release. No author turn cap
or duplicate model client/Agent engine is introduced.

`list_cases`, `read_case`, `test_recipe`, `inspect_failure` remain frozen test tools.
Optional `list_notices`, `read_notice`, `read_parse_result` are separately scoped live
saved-data diagnostics. They cannot fetch URLs, run parsing or expose held-out gold.
Actual rejected output and host failure envelopes remain distinguishable.

## Versioning, storage and enablement

Canonical JSON helpers live in `serialization.py`. `runtime_fingerprint()` binds shared
extraction code, not source admission policy or routing implementation. Source admission
has its own `AUDIT_POLICY`, so a source-policy-only update needs re-audit, not Agent
regeneration. Parsing receipts and source acceptance are separate decisions.

`Position` keeps typed JSON facts plus indexed SQLite Date projections. Only resolved
source dates feed those exact columns; inferred/unknown values stay tagged in JSON.
Public expiry is computed from each job's date/time under one aware request clock.
Neither a route category nor source-wide notice uncertainty hides valid job rows.

Old live schemas are not auto-migrated or relabelled. The explicit storage verifier
writes only a new private copy. Production deployment, old data migration, paid
semantic audit and WeChat/browser/device verification require separate authorization.

## Verification plan

Static import boundaries; five-category and capture counterfactuals; typed fields,
scopes/date precision and refinement tests; real official Flow; native SDK restore/
compaction/cancellation and owned-IO tests; SQL admission/query parity for both paths;
reverse-query RPC/source authorization; client contract/type checks and private builds.

Real USTC verification reuses saved material with an explicit clock, no downloads or
models. Runtime-completed, unaccepted diagnostic and SQL-persisted rows are separate
counters. Observed coverage is not labelled accuracy or confirmed hiring availability.
