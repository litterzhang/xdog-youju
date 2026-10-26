# Nano replay: failure diagnosis and repair ownership

## Scope

This analysis reviews the October 25, 2026 nano experiment: eight unfinished
workflows and six zero-position `review` results. It also checks the distinction
between runtime `partial` and source-audit admission. The saved evaluation clock
remains October 24. No inference, downloads, production writes, Repair RPC calls,
source-script edits, activation or deployment were performed in this analysis.

Evidence:

- Original results: `server/.artifacts/nano-flow-20261025/ustc-55-nano.json`.
- Offline diagnostic projection and source-script replays:
  `server/.artifacts/nano-repair-analysis-20261025/analysis.json`.
- The projection calls the current issue-splitting/validation/diagnosis helpers
  with no independent semantic labels in an isolated in-memory DB. It is **not**
  persisted `AuditLedger` output, source admission or independent accuracy gold.
- Source scripts were replayed for the 14 affected cases in the real sandbox.
  Original live **model drafts were not retained by that experiment harness**.
  Model failure paths are known, but raw live model outputs cannot be reconstructed.

## Eight unfinished workflows

| Saved page / query sign | Outcome | Script-only normalized candidates | Primary next action |
| --- | --- | ---: | --- |
| Humanities and Social Sciences / 12110 | DRAFT_INVALID: unknown condition reference | 19 | Recipe workflow/schema feedback; preserve valid script facts |
| Mathematics / 10010 | MODEL_ABORTED at experiment timeout | 6 | Runtime cost/timeout handling, then selective enrichment retry |
| State Key Laboratory of Fire Safety / 12320 | MODEL_ABORTED at experiment timeout | 14 | Same; do not regenerate a Recipe solely because a request timed out |
| Physics / 12030 | MODEL_ABORTED at experiment timeout | 12 | Same |
| Immunity/Immunotherapy deputy director / 638731548331617007 | DRAFT_INVALID: empty conditions with non-null logic | 0 | Workflow output contract and text-layout parser |
| Management / 12040 | MODEL_PROVIDER_ERROR | 9 | Provider diagnosis/retry; source authoring only for separately attributed defects |
| Hefei National Research Center deputy director / 638346140958191006 | DRAFT_INVALID: empty conditions with non-null logic | 0 | Workflow output contract and text-layout parser |
| Special research appointments/postdocs / 635487875427187007 | MODEL_PROVIDER_ERROR | 0 | Diagnose large payload and classification ambiguity first |

### The important loss is after script extraction

Five failed workflows already have **60 structurally valid script position
candidates**. These are incomplete, unreviewed candidates, not 60 newly accepted
jobs. They are excluded from the earlier 110-position result total.

`recipe_runtime/runner.py` currently raises on detailed-stage failure, so no final
normalized result reaches the source audit. The normal production pipeline stores
failure evidence but constructs a failed, empty extraction envelope. A model
refinement failure is therefore conflated with loss of the entire notice result.

A future host/runtime change should retain valid script facts and record detailed
attempt failure separately. It must remain explicit about unresolved conditions,
failed enrichment and publication policy: not swallow the error, report complete
coverage, or automatically admit those candidates. This is **not** something a
source coding Agent should bypass inside parser.py.

### Model validation feedback arrives too late

Native Flow's structured submission passes a JSON Schema gate. Cross-reference
requirements such as condition IDs, empty-array/null-logic consistency and usable
inherited/local position titles are checked later by shared domain validation.
The three malformed drafts fail after model submission rather than receiving
useful in-run correction feedback.

Recipe authors can improve Flow prompts and source adapters. Shared submission
validation and structured retry feedback belong to the host integration, using
the existing canonical validators. Do not relax those validators or make a
second Agent engine. Changing only the prompt may reduce mistakes but cannot
prove the contract issue solved.

### Large input and weak provider evidence

The largest failed session has a **1,954,414-byte model payload**. Its saved page
contains 20 tables/338 table rows; `blocks` serialize to about 1.42 MB and `tables`
to about 0.51 MB. These representations repeat content, including 19 exactly
duplicate rows. The saved classification is both article/list and requires review.

This is a real cost/input preparation concern, **not proof** of a provider context
limit: bytes are not tokens. The upstream failure reason was redacted to a generic
provider error, so context overflow, authentication, throttling or another cause
cannot be asserted. Keep safe provider status/request metadata in future receipts.

Generic, loss-accounted model-input preparation is a runtime responsibility;
source table/body selection belongs to the Recipe adapter. Large-case refinement
should be split inside the detailed subflow using explicitly supported ports and
node capabilities, not by calling models from source Python. No silent truncation
or automatic switch to a more expensive model is justified by these receipts.

## Six review results: two different problems

| Page | What the saved material actually contains | Diagnosis |
| --- | --- | --- |
| Suzhou public experiment center engineers / 639104663982348028 | Three explicitly named engineering positions, one hire each; no table | Text-layout parser missed jobs; quick/expired path cannot spend on a model |
| Archaeology professors / 638712526917179100 | Professor and associate professor, one hire each; no table | Same source text-layout family, not absence of positions |
| English teachers / 639014112026147614 | Explicit English-teacher recruitment and readable requirements; missing application-form attachment | Both script and final model result lack usable position output; missing form does not excuse this |
| Earth/Space Sciences professor / 639113412864720006 | Explicit professor opening, one hire, until filled | Final normalized output has MISSING_POSITION_TITLE; incomplete process/contract details do not justify losing this job |
| Nuclear Science / jobjs 12130 | A header and placeholder row `0` with every business cell empty | No usable job in this saved capture; distinguish acquisition completeness from actual absence |
| Earth/Space Sciences / jobjs 12070 | Same empty-table pattern | Same; do not manufacture a job from the page headline |

Thus **four are evidenced missing-position cases**. The other two are empty
captures/templates, not confirmed live zero-vacancy declarations. Check whether
acquisition/rendering is sufficient before asserting absence. The source adapter
should ignore placeholder rows, and the host needs an explicit, supported empty
outcome separate from parser failure. Currently normalization returns `review`
whenever positions is empty, even if a parser claims supported/complete coverage.
An Agent cannot resolve that domain distinction by changing a status string.

The two empty-table pages alone consumed five provider dispatches and 148,802
reported tokens. Deterministic recognition of a sufficiently acquired empty
layout should avoid that spend; zero extracted rows alone is not such proof.

## `partial` is not equivalent to an admitted result

The no-label audit projection produces:

- 3 success, 29 partial, 15 review, 8 failed;
- nine of the runtime's 38 partial results become audit review because of remaining
  blocking errors, especially MISSING_POSITION_TITLE from blank table rows;
- those nine still contain 44 normalized position entries. They are not empty.

Do not turn the previous 47 completed workflows into 47 admitted notices. Preserve
field uncertainty, acquisition warnings, extraction coverage and blocking audit
failures separately. The projection does not establish publication eligibility.

## What the existing repair chain already does

```text
Source runtime incremental audit
  -> diagnose_row + new/changed material + detailed-cost/classification fences
  -> RepairIncident deduplication + coalesced queued RepairRequest
  -> freeze base digest/revision, inputs, affected cases and regression baselines
  -> GetWorkspace -> InputCompleted -> StartRepair
  -> RecipeService restores Agent checkpoint/history and frozen task inputs
  -> official Flow skill/capability + sandboxed bash/filesystem + optional runtime queries
  -> Agent changes actual scripts/workflow; host validates submitted files/samples
  -> candidate_ready -> import pinned candidate -> saved-material source audit
  -> compare targets and baseline facts/counts, semantic/context fences
  -> accept and switch binding, or reject with retained feedback
```

Only the Recipe Agent should edit source artifacts. Runtime querying tools are
optional configured extensions; their implementation does not mean this replay
was connected to them. The `inspect_failure` case tool can read scoped, frozen
original rejection evidence in the standard path.

A rejected external audit is not an automatic infinite retry loop. Current code
records rejection/terminal failure; an explicit feedback retry can create a child
request. Agent-internal validation iteration and external full-audit retry are
separate mechanisms.

## Verified gaps in repair routing

1. **Review cases are not automatically repairable.** Current trusted adapter codes
   are SCRIPT_PARSE_FAILED and DRAFT_INVALID. All six review cases above diagnose
   as unresolved without independent semantic confirmation. A review badge is not
   a diagnosis. Broadly whitelisting MISSING_POSITION_TITLE would incorrectly mix
   empty templates with missed real positions.
2. **The automatic and manual sample gates differ.** Automatic enqueue calls
   diagnose_row before selecting samples and correctly excludes the five provider/
   budget failures. `select_repair_samples` checks diagnosis for review rows, but
   not for failed rows. An offline probe reproduced three timeouts and the
   Management provider error being selected as manual code-repair samples. The
   large aggregate was excluded by its classification fence, not its provider
   failure. Manual and automatic entry points need the same attribution policy.
3. **Failed facts and failure origin are insufficiently actionable.** Standard
   diagnostics can preserve rejected drafts and their origin, but routing mostly
   looks at error codes. Use trusted stage/origin plus independent evidence to
   distinguish source adapter, Recipe workflow, domain, acquisition and provider
   ownership. Model-authored error prose must not grant repair authority.
4. **Repair regressions need explicit partial-fact coverage.** Existing baselines
   protect rows with no blocking errors and compare facts as well as counts. A
   blocked/partial row with healthy fields can otherwise be absent from those
   baselines. Define protected known facts without certifying unresolved semantics.
5. **This experiment did not enter repair.** It uses a native topology fixture with
   preserved earlier Agent-authored scripts, not a newly admitted/pinned v3 service
   revision. It wrote no AuditResult/RepairRequest records. A same-material failure
   also is not a new incremental incident. Do not claim that these artifacts alone
   caused an automatic repair task.

## Recommended implementation order — not implemented by this analysis

1. **Shared host/runtime:** preserve valid script candidates on enrichment failure;
   persist original model/script/merged rejection stages; report safe provider and
   timeout causes; provide canonical semantic-validation feedback without weakening
   the schema. Introduce explicit empty-result meaning with acquisition safeguards.
2. **One trusted repair diagnosis:** apply it to automatic, manual and retry entry
   points. Provider/budget incidents go to bounded execution retry, not source-code
   generation. Confirmed layout/workflow defects go to RecipeService. Acquisition,
   classification and domain defects retain their respective owners. Do not require
   human labels for every source: use sound structural evidence where possible and
   only small independent audits for ambiguous semantics.
3. **RecipeService task:** pin a current-contract service revision/audit first. Group
   source defects by layout/root cause and provide at most three representative
   inputs plus the frozen case catalog. Have its coding Agent fix text-only job
   sections, blank/header rows and source condition mapping; let it improve native
   subflow prompts for title and condition-logic obligations. Do not hand-edit these
   source scripts in the application repository.
4. **Regression:** replay the same 55 saved inputs through the standard audit path;
   protect all previously known facts, report the fate of the 60 script candidates,
   and separately count real empty captures, missing-position fixes, enrichment
   failures, model dispatches and date/condition uncertainty. Repair acceptance is
   not a measured semantic accuracy claim or authorization for production rollout.

## Verification

Existing targeted repair, source repair, diagnostic and reverse-query tests:
**125 passed**. The 14 affected source scripts were replayed in the real sandbox;
manual selector behavior was checked using saved bundles and an isolated in-memory
DB. No new paid model call or standard-service repair task was executed. This turn
adds analysis artifacts and this document, not a repair implementation.
