# Nano Flow verification — October 25, 2026

## Scope and configuration

Native Recipe Flow extraction now uses independent `flow_model`, defaulting to
`gpt-5.4-nano`. Managed workers accept `YOUJU_FLOW_MODEL`; source/Recipe CLIs
accept `--flow-model`. Classifier small/large tiers and the author Agent remain
independent. This experiment enabled live calls explicitly, using official
xdog 2.4.9 and Copilot's exact `copilot/gpt-5.4-nano` catalog entry; no fallback.
No live service was restarted or deployed.

The same **55-case saved USTC cohort** was replayed using the current native
three-subflow graph and unchanged earlier Agent-authored source scripts. This is
not a newly Agent-authored/admitted Recipe or a full-source accuracy benchmark.
The evaluation clock is **October 24, 2026, 13:55:03 +08:00**, intentionally frozen
for comparison, not a claim about current live recruitment status.

## Existing inventory reference — not rerun

The earlier immutable snapshot contains 1,335 resources: 1,314 have a latest saved
bundle and 21 do not. Its classification report has 165 notice-list resources and
183 recruitment-article candidates, including one overlapping list/article
candidate; 182 were resolved recruitment articles. These are earlier observations,
not newly measured classification accuracy. This run selected only the 55-case
cohort; no new page or attachment was downloaded.

## Current execution funnel

```text
55 notices attempted
  ├─ 33 script-only: 63 position entries, identical to previous replay
  └─ 22 native model sessions: 50 provider-stream dispatches
       ├─ 14 workflows completed: 47 position entries
       │    ├─ 10 notices with positions
       │    └─  4 empty/review notices
       └─  8 unfinished workflows

Overall: 47 completed, 8 unfinished
         41 nonempty notices, 110 normalized position entries
Result states among completed workflows: 3 success, 38 partial, 6 review
```

| Cost-routing category | Notices | Completed | Model sessions | Position entries |
| --- | ---: | ---: | ---: | ---: |
| Fixed deadline / active (cost hint) | 1 | 1 | 0 | 1 |
| Fixed deadline / expired (cost hint) | 24 | 24 | 0 | 29 |
| No fixed deadline / high priority | 25 | 17 | 22 | 50 |
| No fixed deadline / low priority | 5 | 5 | 0 | 30 |
| Unknown | 0 | 0 | 0 | 0 |

Quick paths (24 fixed-expired and 5 undated-low notices) made **zero model calls**.
Three other detailed-eligible notices completed with scripts without using a model,
and the single fixed-active notice also remained script-only.

Cost routing is approximate. It never fills an exact position deadline or proves
that a job is open. Of the 110 position entries, 29 have a structured source-tagged
deadline and 81 remain unknown. Against the frozen clock, 28 are expired, one is
not expired, and 81 have unknown calendar status. Unknown dates were not fabricated
from publication/update/capture timestamps. These counts do not establish whether
all explicit dates in the materials were successfully found.

## Failures and quality boundaries

- **3 DRAFT_INVALID:** one unknown condition reference, two non-null logic objects
  attached to empty condition arrays. The domain rejected them; no silent repair.
- **3 MODEL_ABORTED:** approximately 600-second experiment runtime limits cancelled
  active model work. This limit belongs to this verification harness, not a new
  author Agent or production default.
- **2 MODEL_PROVIDER_ERROR:** provider failures; their detailed upstream cause was
  not established by the redacted receipts. They are not website download blocks.

`partial`/`review` are retained, not counted as verified correctness. Model-session
completion is not equivalent to source admission, exact semantic extraction or
public job publication. Entries are not deduplicated real vacancies or headcount.
Incomplete captures did not veto readable positions; failed/unknown outputs were
not padded with guessed facts. Source scripts were not manually repaired.

The provider reported **1,626,544 total tokens across 20 sessions**. Two failed
sessions have no usage report, so this is not complete billing. The 50 dispatches
exclude provider-internal HTTP retries; no money cost is estimated.

## Verification and receipts

- Backend: **1,403 passed; 4 explicit opt-in sandbox tests skipped by default**.
- Those four real Linux sandbox checks, separately enabled: **4 passed**.
- Ruff lint and format: passed; **238 Python files already formatted**.
- Root AGENTS.md, both source script hashes and the immutable snapshot are unchanged.
- No source database writes, Recipe activation, deployment, upload or callbacks.

Results: `server/.artifacts/nano-flow-20261025/ustc-55-nano.json`.
Per-notice inputs/receipts are referenced in `records/`; run instructions and scope
are in that artifact directory's README. Cached receipt aggregation does not call
inference again. Runtime fingerprint: `3e070a00eefd017635bbf8164f467cfaa372458785987516feddadb50d08a607`.
