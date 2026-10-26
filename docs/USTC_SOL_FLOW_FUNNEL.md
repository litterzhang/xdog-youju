# USTC Sol/Flow Source Funnel Verification

Snapshot clock: **2026-10-24T13:55:03+08:00**. Source: USTC employment website.
This is an isolated **host-template new-contract replay**, not a newly Sol-authored
source Recipe, semantic-accuracy benchmark, production admission or deployment.

## Acquisition and classification

| Saved observation | Count |
| --- | ---: |
| Unique source resources | 1,335 |
| Current saved material bundles | 1,314 |
| Resources without a saved bundle | 21 |
| Recruitment-intent pages across all roles | 224 |
| Recruitment article/detail candidates | 183 |
| Notice-list pages | 165 |
| Recruitment detail/list intersection | 1 |

Roles overlap: do not sum lists and details. 182 detail candidates were resolved
enough to execute Recipe Flow; one remains classification review. All 1,314 saved
HTML views were reparsed with unchanged evidence. After a generic-template fix,
upstream classification was bound to the same snapshot, clock, materials, classifier
and evaluator; **all 182 applicable Recipe cases were re-executed**, with **zero
candidate outputs reused**. Replay made no network or model calls and loaded no
implicit local classifier artifact.

## Recruitment validity and position entries

| Whole-notice timing | Notices | Reported entries | Host-contract accepted | Published by replay |
| --- | ---: | ---: | ---: | ---: |
| not_expired | 1 | 0 | 0 | 0 |
| expired | 1 | 0 | 0 | 0 |
| unknown | 181 | 327 | 327 | 0 |
| cancelled | 0 | 0 | 0 | 0 |

Position entries are not advertised headcount or proven globally distinct vacancies.
Contract acceptance is not independently measured semantic accuracy or eligibility.

## Completion and unresolved cases

- 164 full-path notices produced 327 contract-accepted position entries.
- One confirmed-expired notice archives successfully with zero jobs; coarse
  completeness is deliberately not the full-quality completeness requirement.
- 17 notices remain unfinished because unsupported scripts need a disabled
  detailed model. This includes the not-expired notice: zero parsed entries is
  **not** evidence that it advertises no jobs.
- One recruitment-detail candidate remains classification review.
- Of 181 unknown notices, 156 have incomplete saved material; 140 retain a fixed
  cutoff rule. These groups overlap. A declared date does not prove that every
  registration scope has closed under unresolved material/scope dependencies.
- No old output was retagged and no source/admission pointer was changed.

## Independent Sol audit and verification

One authorized `gpt-6.1-sol` audit used **one request**, with 2,163 input, 1,720
output and 3,883 total tokens. It supported the observed October 8, 2025 cutoff
but remained inconclusive about whole-notice scope under missing material. It
created no human gold or source edits. Reopening the durable ledger returned the
same receipt with provider construction forbidden and zero new calls. Dollar
cost is unknown; SDK catalog zero is not a billing invoice.

Backend full regression: **598 passed, four opt-in skips**. Subsequent final
funnel/cache/Flow regression: **57 passed, two opt-in skips**. All four real-OS
sandbox opt-in scenarios passed separately. Native init/build/repair restored
across independent real-bubblewrap processes using synthetic provider output.
Ruff passes. No frontend changes, service restarts, deployment or upload occurred.

## Artifacts and commands

Private root: `server/.artifacts/sol-flow-20261024/`.

- `source-accuracy-template-current.json`: current candidate outputs and cache/execution provenance.
- `source-accuracy-template-current-funnel-validated.json`: actual read-only CLI validation.
- `source-accuracy-template.json`: preserved pre-template-fix full replay.
- `single-audit-summary.json`, `single-audit-dedup.json`: real audit accounting.
- `native-cross-process.json`: native restore/capability lifecycle verification.

See `SOURCE_DATA_FUNNEL.md` for commands/count definitions and
`SOL_FLOW_INTEGRATION.md` for bounded audits and explicit rollout boundaries.
