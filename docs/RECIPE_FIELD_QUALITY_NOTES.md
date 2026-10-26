# Recipe Field Quality Findings

## Scope and status

Read-only diagnosis of the saved USTC public-candidate projection on October 26,
2026. This note records requirements and defects, not implemented repairs. No paid
model calls, Recipe edits, reprocessing, service restarts or deployment occurred.
Diagnostic artifacts are in
`server/.artifacts/field-quality-diagnosis-20261026/`.

## Confirmed product requirements

- Extract available job facts, including headcount and work location, rather than
  treating schema-valid nulls as evidence of complete extraction.
- Recognizable requirements belong in source-neutral typed conditions. `other`
  is at most one complete residual text per position, determined during Recipe
  generation and repair, not frontend concatenation.
- Preserve mandatory versus preferred requirements, alternatives, exceptions and
  explicit uncertainty. Do not turn preferences into eligibility restrictions or
  invent experience years. Education and degree remain separate.
- Missing material must not erase readable facts. Unknown optional fields and
  legitimate residual conditions must not automatically mark the whole job for
  verification. Actual conflicts must remain visible at their proper scope.
- Retain field provenance: `source`, `ai_inferred`, or `unknown`.

## Measured condition-structure defects

The current projection contains 336 jobs:

| Condition type | Condition entries | Jobs with a non-null value |
| --- | ---: | ---: |
| Other | 2,196 | 336 |
| Education | 218 | 218 |
| Major | 214 | 206 |
| Degree | 26 | 26 |
| Study form | 3 | 3 |
| Age, work experience, graduation status/year, political status, license, language | 0 | 0 |

Eight major conditions have null values. There are 268 jobs with multiple `other`
conditions, contrary to the new generation requirement. Existing data was not
rewritten when that requirement was added.

Keyword diagnostics flag 277 jobs with potentially structured information inside
`other`. This is not an accuracy estimate: phrases about teaching undergraduate
students are not necessarily education requirements; a preferred major is not a
mandatory major restriction. Some typed facts may also already be present.

A definite missed extraction is `40周岁以下`, currently an `other` condition rather
than a typed age requirement. Other saved prose mixes age, degree and teaching
requirements. `一般不超过50岁，特别优秀的年龄可适当放宽` cannot safely become an
unconditional maximum. `不限，生命科学相关专业优先` must preserve unrestricted
eligibility and preference separately.

The contract provides typed age and experience values, but typing support alone
has not made generated Recipes populate them. It also needs an explicit policy
for requirements whose preference/exception semantics exceed the current schema.
Do not force such text into a misleading rule merely to increase typed counts.

## Non-expired jobs: missing headcount and location

Six jobs across five announcements have a source deadline not yet passed. All six
have null headcount and work location. Five jobs have `execution.model_used=true`;
one engineering-assistant job has `path=detailed` but `model_used=false`.

Four job titles explicitly contain `(1人)`; none populated numeric headcount.
This is a direct extraction omission. The Suzhou notice recruits two staff across
two roles: its total must not be copied to each role or arbitrarily split. A
contact address is not automatically a workplace. Contextual location inference
must remain tagged `ai_inferred`.

The active workflow's AI guard requires unsupported layout, `needs_detailed=true`,
or zero positions in addition to detailed routing. The engineering-assistant
bootstrap returned `supported=true`, `complete=false`, `needs_detailed=false` and
one row, so AI was skipped. A detailed route does not guarantee AI execution.
The shared table parser reads headcount columns but misses count text in titles.

Successful raw model-stage drafts are not retained in the saved audit result.
For the other five jobs, model execution is confirmed, but available receipts do
not establish whether omissions originated in model output or a later stage.

## Overbroad verification labels

Of 336 jobs, 296 have `review_required=true`; 141 of these are expired. Across
reviewed jobs, 252 carry `UNRESOLVED_CONDITION`, 28 carry
`UNRESOLVED_COMMON_CONDITION`, and 46 lack an admission receipt. Counts overlap.
Expiry and extraction certainty are separate axes; expiry does not clear an actual
conflict, but ordinary residual prose must not create a false whole-job warning.

Current parsing uses broad keywords in residual text; common/unscoped field issues
can propagate to every job in an announcement. Distinguish missed typed facts,
legitimate residual requirements, material completeness, execution failures and
actual conflicts. Do not solve this merely by hiding tags.

## Employment mechanism versus staffing identity

Observed pairs are 196 `labor_dispatch`/unknown staffing and 140 unknown/unknown;
no saved dispatch-plus-established contradiction was found.

The conceptual axes differ, but `staffing_status` currently mixes establishment
status with management systems such as filing/quota management. Synthetic helper
checks show that `实行备案制管理，不纳入事业编制` produces a false conflict between
coexisting facts, while `劳务派遣，纳入事业编制` produces no cross-axis warning.
These are synthetic diagnostics, not findings about an actual employer.

Proposed, not implemented: separate establishment entitlement from personnel
management systems, and validate apparent cross-field contradictions within the
same position scope. Do not infer establishment from institution identity or
classify generic labor-contract wording as a distinct hiring mechanism.

## Responsibility boundaries and repair acceptance

1. **Shared position domain:** own source-neutral types, provenance, logic,
   semantic limits and scoped conflict validation. Schema validity is not field
   completeness. Decide preference/exception representation here, not per source.
2. **RecipeService Agent:** repair generated scripts and native Flow graphs using
   saved materials and diagnostic feedback. No host-assistant edits to generated
   source scripts. Keep the single residual-text rule from initial generation.
3. **Flow:** make routing, semantic-completion decisions and model execution
   explicit. Cheap parsing may satisfy explicit facts; non-expired jobs must not
   silently bypass needed semantic extraction merely because one row exists.
4. **Runtime:** normalize, persist and report stage-level diagnostic receipts and
   scoped issues. Do not reinterpret notice cost routing as job-date truth, merge
   residual text to conceal Recipe defects, or add source-specific patch rules.
5. **Acceptance:** replay saved examples for title headcount, shared totals,
   location provenance, age/degree/experience, preference/exception logic, one
   residual condition, and staffing scope. Report per-field recovery and remaining
   unknowns; keep independent semantic checks separate from the generator's own
   claimed success. Reuse saved material instead of downloading it again.

A standard repair, approved activation and re-audit are still required. This note
does not establish extraction completeness or propose hiding unresolved facts.
