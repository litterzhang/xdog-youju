# Archived Admission and Recipe Diagnostics

Status: implemented locally on October 23, 2026; **not deployed**.
All experiments described here use synthetic fixtures or saved public materials;
no paid provider, production migration, service restart or client upload is implied.

## Archived-only admission

`admission_quality()` separates complete successful full/unversioned extraction from
validated coarse archives. A source whose **currently identified saved notices** all
passed host checks and are explicitly `coarse_eligible`, expired and error-free may
finish onboarding without inventing a nonempty full extraction sample. Its admission
mode is `saved_announcements_archived_only`, with zero successful full announcements
and no public Position rows. This is not a claim that every web page was classified
or that the institution has no vacancies.

An unknown, failed, pending, unvalidated or mixed-quality notice cannot use this
exception. Existing inventory, acquisition, source-error, semantic and final
activation fences remain. Same-day status reassessment supports these archived
rules without consulting the old deadline helper as the sole oracle.

Reopening needs a new material version, full parsing and a new source audit; old
coarse rows cannot become full by relabeling. Without a supported full script or
detailed model, reopening stays honestly unfinished. Frozen onboarding history and
the separately opt-in incremental switch remain unchanged.

Incremental projections carry the quality tier, exclude coarse positions and honor
new independent semantic quarantine. The client labels coarse results as archive
parsing, never "complete extraction"; no visual redesign was introduced.

## Preserve what actually failed

`onboarding/diagnostics.py` records rejected output independently of failure and
publication envelopes. Records pin candidate/artifact identity, material and case
versions, recipe/generation when applicable, checker, clock and model profile.
They identify origins including period adapter, script capability, script before
failed detailed fallback, model submission and independent semantic audit.

- A missing result is not a returned empty position list.
- A host failure envelope may retain structurally readable rejected positions for
  diagnosis, but its failed status and errors cannot create public rows.
- Independent semantic checks use the actual structurally interpretable rejection,
  not invented `positions=[]` placeholders. If no interpretable output exists, the
  semantic check is explicitly not evaluated, rather than claiming model omissions.
- An exhaustive independently correct rejection that fails a host coverage check
  is diagnosed as a host-validation conflict, not blindly sent to source regex repair.
- Bounded model submissions are retained in private exception attributes, never
  generic `as_dict()` output. Schema feedback includes a sanitized field/rule path;
  unknown material keys and actual values are not echoed into generic logs.
- Invalid period output is untrusted diagnostic data, not `validated_validity`.

Source-side evidence is stored in the existing content-addressed blob store. Author
validation evidence lives under read-only `/history/rejected/<digest>.json`.
Canonical file checks, immutable writes, descriptor/no-symlink protections and
sandbox isolation remain in force.

## Bounded inspection

A case test returns a small receipt. Inspect its diagnostic list, then the original:

```text
inspect_failure(test_id="<test digest>")
inspect_failure(test_id="failure:<failure digest>", section="rejected_output", cursor=0)
inspect_failure(test_id="failure:<failure digest>", section="issue", issue_index=0, cursor=0)
```

Follow `next_cursor`. Issue-list cursors count issues; single-issue/output cursors
count characters. The issue limit is 1–20, with an additional byte bound. Large
individual issues get an explicit pointer to the single-issue view, not silent cuts.
`list_cases` exposes assigned saved-source failures as `source-failure:<digest>`.
Those records are copied into RecipeService immutable storage, bound to catalog
material/source/base recipe, and verified by InputCompleted. No arbitrary blob,
URL, production DB, another task's evidence or writable gold access is granted.

Limits are explicit: 12,000 characters per text page, about 64 KiB per issue page,
8 MiB per retained component and 20 MiB per diagnostic record. Over-limit components
report their byte count, digest, bounded preview and truncation; they are not called
complete retained output. A frozen task can import at most 128 MiB of source failure
records. The existing small manifest/input and canonical-file limits are unchanged.

Small attempt/tool summaries retain original error counts and failure references.
Full errors/outputs remain separately available within the declared retention limits.
Feedback and rejected material are untrusted data, never system instructions.

## Native validation reuse and progress

Submissions persist immutable receipts against exact candidate files, frozen input
revision/material versions, checker implementation, clock and model configuration
(including extraction limits). Restoring a native runtime does not buy another
expensive validation of identical rejected files. File edits invalidate the receipt;
final file-digest checks and source-wide admission remain independent.

Progress state persists per frozen validation context. Repeated confirmed defect
and available-row states, including observed cycles, end the task with
`AUTHOR_NO_IMPROVEMENT`. Changing comments or unrelated fields alone is not proof of
progress. More recognized rows and newly exposed concrete diagnostics remain valid
work. Syntax feedback includes line/offset/detail. Volatile timing/provider metadata
is excluded from defect identity.

The bounded 32-state recent-history window is not an author turn/candidate budget.
Tests exercise more than 32 improving observations. No author lifetime turn, token
or wall-clock cap was added; provider context limits, native auto compaction,
cancellation, extraction budgets and per-tool OS limits still apply.

## Verification and activation boundary

Targeted tests cover archived-only onboarding, zero-position admission, archived
incremental results, saved-output reassessment, reopening/full audit, private
schema-invalid model output, huge field pagination, task/case-bound reads, copied
source failure inputs, unchanged/cyclic progress and cross-runtime receipt reuse.
Real provider and source-author repairs remain separately authorized experiments.

The legacy author guide was corrected to remove the contradictory mandatory
complete-fast-probe instruction. Updated guides change checkpoint guidance identity;
existing sessions require the established explicit guidance-update decision where
supported. SDK/skill/provider/source identity fences are not relaxed, especially
for 2.4.1 -> 2.4.9. Copy-only native cutover is still part of the active goal.

Logs and replay evidence are stored under
`server/.artifacts/recipe-redesign-20261023/`. See
`RECIPE_ACCURACY_IMPLEMENTATION.md` for measured results and remaining goal work.

Recorded verification for this increment: **493 backend tests passed, two optional
sandbox tests skipped**; both real sandbox tests separately enabled and passed.
The final focused suite passes **55 tests**. Client type checking and **112 tests**
pass; H5/weapp compile to isolated artifact directories, not the served static root.
H5 retains the existing webpack performance warning.

The same 40 frozen USTC cases were replayed through real sandboxed HTML parsing and
the official Flow: all retain their original evidence identity, 18 complete current
script/contract checks, three remain unfinished without a detailed model, and 19
are not resolved recruitment articles in the rules-only run. These remain unlabeled
single-source observations, not accuracy or a source-held-out benchmark. No network
requests or model calls occurred in this replay. Receipt:
`server/.artifacts/recipe-redesign-20261023/diagnostics-admission-verification.json`.
