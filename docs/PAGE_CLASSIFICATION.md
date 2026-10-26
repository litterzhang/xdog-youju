# Page Classification: Coverage, Evidence, and Independent Review

## Shared classifier implementation — October 15, 2026

The following USTC v2/v5 sections are **historical experiments**, not current shared
model quality claims. The new runtime supports one shared two-head JSON format:
page form plus recruitment/guide/report/event intent. Old binary USTC artifacts
are rejected by source CLI/daemon loading. Global training/calibration/test tools
require source/template/content separation and disclose label provenance, supports,
abstentions and deduplicated metrics. The combined evaluator replays rules and
host guards separately from model-only metrics, without external calls.

Cadence is recomputed by the host, not learned; uncertain lists retain daily
monitoring. Rules/local inference still precede the existing durable budgeted
LLM path. Nothing here resets that budget or edits generated source recipes.
See `SHARED_PAGE_CLASSIFIER.md` for commands and unverified real-data boundaries.

## Four different claims

1. **Coverage:** the fraction of saved pages accepted by a classification path.
   It does not measure whether accepted labels are correct or whether all vacancy
   announcements were found.
2. **Silver agreement:** agreement with historical model labels. Content-group
   separation does not make those labels human ground truth. Report acceptance,
   abstention, and denominators alongside agreement.
3. **Assistant evidence spot-check:** an assistant's independent reading of saved
   source evidence. This review was unblinded and risk-stratified, not human gold
   labeling or an unbiased population sample.
4. **Accuracy/recall:** requires independent, adjudicated target labels and a defined
   population. Vacancy-role accuracy, false-exclusion rate, and cross-source recall
   have not been established here.

**Recruitment-related content is not necessarily a vacancy announcement. Complete
material is not proof of positions. Accepted classification is not proof that
applications are still open.** Authority, attachments, extraction, validity, and
eligibility remain separate checks.

## Implemented classification path

The implementation is under `server/src/youju/onboarding/`:

- `classification_features.py`: page completeness, structural shape, and text
  features. Some navigation and duplicated DOM descendant text are removed from
  features without rewriting original evidence.
- `classification.py`: rules, then local inference, then explicitly configured and
  budgeted small/large external models. Otherwise retain unknown/review.
- `local_classifier.py`: validated numerical JSON artifacts, not executable pickles.
- `classification_budget.py`: durable reservations and coverage accounting.
- `pipeline.py`: downstream routing. `article + recruitment` enters notice handling,
  so a wrong role is operationally important.

| Field | Meaning and limitations |
|---|---|
| `page_roles` | `notice_list`, `article`, `navigation`, `attachment`, `other`, `unknown`. Recruitment articles need standalone-notice evidence, not merely long prose. |
| `business_type` | `recruitment`, `correction`, `cancellation`, `result`, `unrelated`, `unknown`. Guides and reports can be recruitment-related without offering positions. |
| `revisit` | `none`, `daily`, `weekly`. Unknown/incomplete material must not silently stop monitoring; lists retain monitoring. |
| `evidence_ids` | Original block/table-cell identifiers. Existence and keyword support are necessary, not sufficient for correct semantics. |
| `requires_review` | Uncertainty that must remain visible after processing. |
| `method` | rules, local, small, large, unknown. Count accepted and review-required outcomes separately. |

Host validation checks schema, evidence, event context, and unsafe negative
exclusion. Page type may be clear despite an unavailable attachment, but that does
not make the attachment usable. Instructions embedded in source material are data,
not authority to execute actions.

### What the historical USTC local model learned

The USTC v2 artifact learns only **binary business type**: `recruitment` versus
`unrelated`. It does not learn vacancy, guide, report, and list roles. Features
include structural shape, repeated title, up to 24,000 characters of prose and
6,000 of link labels, with URLs/emails and digits normalized. It uses character
TF-IDF 2–4 grams and LogisticRegression (`C=4.0`, balanced class weights).

The probability scores are uncalibrated. Thresholds are selected on calibration
data, not independently estimated accuracy. A business label can agree with silver
while an inherited article role wrongly routes an employee guide into vacancy
extraction.

## Cumulative source classification budget

**Changing a rule/model profile must not refill the source budget.** The main
agent changed reservations to accumulate across profile ledgers during the guide
fix. These are inspected implementation semantics, not authorization for new calls:

- Reserve under the source lock and a database transaction before calling a model.
- Default fraction: 0.05. Limit: `floor(denominator × fraction)`.
- The reservation denominator is the distinct union of current bundles and
  previously assessed bundles for the source, no longer restricted to the current
  classifier key. New materials/versions can change it; 65 is not a permanent limit.
- Used capacity is the sum of reservation entries across the source's profile
  ledgers, not distinct URLs. Small followed by large consumes two units. Failed or
  cancelled reservations are not automatically refunded.
- `llm_reserved_calls`: **source-cumulative reservations across persisted profiles**.
- `llm_profile_reserved_calls`: reservations within the current profile, for
  provenance. Zero here does not imply the source has a fresh allowance.
- When exhausted, continue cheap classification and retain unresolved pages for
  review. Restarting or changing models is not another 5% allowance.

“Cumulative” refers to the durable `classification_budgets` ledgers. It does not
automatically backfill calls made before those ledgers existed, independent
diagnostics, or other model tasks. Reservations, call logs, and provider bills
remain different measures.

For the historical 1,312-page run, the limit and used ledger were both 65. With
that denominator and fraction unchanged, reclassification cannot approve more
calls even if the new profile has zero reservations. Pending acquisitions are reported separately; the coverage target is not marked
met while such work is outstanding. The budget denominator and
the coverage denominator `total_current_pages` need not remain identical in all
future versioning scenarios.

## Historical pre-fix run: 95.579% was not accuracy

Artifacts: `server/.artifacts/ustc-full-poc-20261012/classification/`.
These are historical `run-report.json` values, not the final state of later
profiles. Original call timestamps are October 11, 2026 UTC; directory labels use
the corresponding date in China standard time.

| Measure | Historical completed report |
|---|---:|
| Processed current pages | 1,312 / 1,312 |
| Rules: total / covered | 1,217 / 1,214 |
| Local: total / covered | 40 / 40 |
| Small: total / covered | 8 / 7 |
| Large: total / covered | 10 / 1 |
| Unknown method | 37 |
| Review required | 50 |
| Cheap coverage | (1,214 + 40) / 1,312 = **95.579268%** |
| Recorded calls / reservations | 65 / 65 |

The 65 successful ledger entries concern **36 pages**: 36 small and 29 large
calls. Their `(bundle_id, tier)` multiset matches the reservation ledger. This is
not provider billing verification.

An independent offline replay used only `before.sqlite3`, its saved blobs,
frozen pre-fix code, and the same model. It reproduced rules 1,214 covered plus
local 40 covered. Without external models it retained 55 unknown outcomes and
58 review cases. The difference from the historical 50 review cases is the eight
covered LLM outcomes not replayed; it is not evidence of a current-code regression.
The live DB and individual final LLM responses were not inspected.

## Silver evaluation and role review answer different questions

Model: `server/.artifacts/ustc-local-classifier-20261012-v2/model.json`.
Payload key:
`59e5bfef4eca1c676ec9da8b3ef9b016e1b68b304e9fd635a8e68fa3852231b1`.

Training has 202 records (28 recruitment / 174 unrelated), calibration 67 (7 / 60),
and test 54 (12 / 42). Recomputed cross-split ID/group/normalized-text intersections
are all zero. All 323 feature texts, groups, and cited evidence match the baseline;
training/calibration fingerprints match model metadata. Near-duplicate content,
shared templates, and source-specific style can still make evaluation optimistic.

| Evaluation against 54 silver business labels | Accepted | Agreed | Abstained |
|---|---:|---:|---:|
| Original v2 saved combination | 51 | 51 | 3 |
| Fixed model alone | 51 | 51 | 3 |
| Frozen combination after the earlier reporting-rule repair | 54 | 54 | 0 |

These are **silver-agreement** results. Original 51/51 covers only 51/54 records;
all three abstentions have recruitment silver labels. The calibration recruitment
gate has only six accepted observations; the unrelated gate agrees on 60/61.
An observed-precision target of 0.98 is not a precision guarantee. The 54/54
combination result still evaluates business type, not role, and is not a fresh
untouched gold test.

### Four independently identified role errors

A fixed seed/hash sample reviewed 26 pages: all four local recruitment positives,
six local negatives, four lists, four recruitment-related reports, four rule
negatives, and four unknown outcomes. Selection was fixed before body inspection;
errors were not replaced with easier cases.

| Page / bundle | Saved body evidence | Pre-fix role disagreement |
|---|---|---|
| `cn/guide.aspx` / `c367bf811f97409681420602b10d7861` | “进校后常规工作与生活信息”, “《新进指南》”, procedural work/life tables | Supporting employee guide, not a vacancy |
| `sign=449632094062500002` / `2a146825e10c4b38a9feeebe41f2f6ed` | Talent-base designation, ceremony, prior hiring-policy retrospective | Policy news, not an offer |
| `sign=637228065205636054` / `0486b2389c2a4839adf40c2c64ff98e8` | Participation submissions and cloud-discussion invitations; hiring information points elsewhere | Forum participation, not job application |
| `sign=637238615826657829` / `95e9a3b3eaa64136b185ce2bef288ca3` | Completed forum; recruitment positions/benefits describe presentation topics | Event reporting, not openings |

The three `sign` pages use `cn/indexnews.aspx`. All four were local
`article + recruitment`; their recruitment context can remain without admitting
them as standalone notices. All four belong to the **training split**. Their
binary business labels can agree with silver even while their roles are wrong.

The spot-check found no definite disagreement in 17 cases, four role disagreements,
and five abstained/mixed-content cases. **No spot-check accuracy is claimed.**
One tea-meeting page ends with “现急需引进核安全科学与技术方向的人才” and has a failed
attachment: its title cannot establish that the whole material contains no offer.
Other risks include scientific “PRC2 recruitment” triggering conservative
abstention, an INVALID_LINK page, and an image page with zero body blocks. These
were retained rather than removed to improve metrics. Ten sampled negative
articles without observed omissions do not establish vacancy recall.

## Version-separated follow-up

The guide-only v3 four-case observation is retained in `current-sample.json` with
source hashes: the guide became rules `other + recruitment / weekly`, while the
other three remained local recruitment articles at that snapshot. This is a
historical observation, not a claim about the latest production code.

The main agent then supplied v4: local binary recruitment plus article structure
alone no longer establishes a vacancy without position-header evidence, and the
reporting rule treats explicit urgent hiring language as a reason not to accept a
confident supporting-only role. Rules still handle grounded vacancy intent.
The separately measured v4 offline replay covers **1,250 / 1,312 = 95.274390%**:
1,214 confident rules and 36 confident local decisions (all unrelated articles),
with 59 unknown outcomes and 62 total review cases. The 95% coverage target remains
met; **accuracy is still not established**. Exactly five page decisions changed:
the guide becomes `other + recruitment`; the other three former local positives
and the mixed tea-meeting page become `unknown / weekly / review`. Coverage falls
by four pages because uncertainty is retained, not because those four pages were
semantically resolved. All five are kept out of confident vacancy routing.

There were zero external calls, the original DB hash was unchanged, and production
source hashes matched the v4 snapshot before/after the run. Results are separate
in `v4-audit.json` and `v4-predictions.json`; they do not replace pre-fix evidence
or establish live source-audit completion. The conservative gate may also abstain
on genuine future notices that the rules do not recognize. This work does not validate the ongoing real authoring, audit completion,
incremental transitions, or source activation.

The final inspected production revision is **v5**. It additionally excludes
explicit opening language from both guide-rule branches, preventing a generic
hiring-policy match from preceding the mixed-offer safeguard. A fresh, separate
v5 replay measured **1,250 / 1,312 = 95.274390%**, with rules/local covered counts
**1,214 / 36**, **59 unknown**, **62 review**, and **zero external calls**. All
five comparison cases retain the safe v4 outcomes described above; full saved
v4/v5 prediction arrays compare equal on this baseline. The source hashes differ
and are recorded separately in `v5-audit.json`. Production hashes matched before
and after the v5 run; the original DB hash was unchanged. This does not verify
the main agent's ongoing live persisted-key or incremental-audit transition.

The main agent's real `NO_POSITIONS` observation for the guide is attributed to
that separate run. No author or parser execution was repeated by this review.

## Audit artifacts and reproduction

Detailed report:
`server/.artifacts/ustc-full-poc-20261012/classification-review/REPORT.md`.

- `sample-plan.json`, `assistant-notes.json`: fixed sampling and assistant-authored
  judgments, with Chinese source quotations retained verbatim.
- `spot-check.json`: full URLs, bundle/version/blob hashes, original evidence IDs,
  DOM locators, short literal quotations, and predictions. Unreadable pages have
  explicitly missing evidence, not invented references.
- `audit.json`: input hashes, split checks, detailed silver confusion/abstentions,
  historical coverage, and call reconciliation.
- `baseline-manifest.json`, `replay-predictions.json`: complete baseline mapping
  and frozen pre-fix offline results.
- `frozen/`, `source-snapshot.json`: historical audit-only source copies, not
  production imports.
- `current-sample.json`: four-case guide-only v3 observation at recorded source hashes.
- `v4_frozen/`, `review-v4.py`, `v4-audit.json`, `v4-predictions.json`: separate
  v4 snapshot, offline full-inventory coverage, and five-case comparison.
- `v5_frozen/`, `review-v5.py`, `v5-audit.json`, `v5-predictions.json`: final
  v5 snapshot and freshly measured full offline/five-case results.

From the repository root, using installed dependencies only:

```sh
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="$PWD/server/.artifacts/ustc-full-poc-20261012/classification-review/.uv-cache"
REVIEW=server/.artifacts/ustc-full-poc-20261012/classification-review
uv run --offline --no-sync --project server python -B "$REVIEW/review.py" --check
uv run --offline --no-sync --project server python -B "$REVIEW/review.py" --show 4
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline --no-sync --project server \
  python -B -m pytest -p no:cacheprovider "$REVIEW/test_review.py" -q
```

The script uses read-only SQLite and verifies content hashes. Network and child
processes are blocked within the review process. `--check` recomputes and compares
saved outputs without advancing the pipeline or writing the original DB. It does
not call the service CLI that initializes databases and acquires write locks.
Final verification passed seven audit-integrity/regression tests, Ruff, and the
frozen pre-fix output replay check; v4/v5 full offline replays are separately
recorded. Full commands and verification logs are in the report. There was no browser,
paid-model, WeChat-device, deployment, or current-source-availability validation.

## Subsequent live verification

The main source worker subsequently persisted the v5 profile and completed its
current-announcement audit and real incremental passes. The final database report
independently measures 1,250/1,312 cheap coverage, 62 review cases, zero unassessed
pages and zero pending acquisitions. Cumulative classifier dispatches/reservations
are 74/74 against 1,487 distinct material versions (limit 74); final v5 replay added
no calls. See `USTC_FULL_POC.md` and `final-evidence/` in the experiment directory.
These are later live observations, not findings of the offline reviewer above.


## Read-only local inference demo — October 14, 2026

The deployed JSON model uses 20,000 character TF-IDF features and two business
classes. Current score gates are recruitment 0.7992340837311727 and unrelated
0.6150735224968983; they are calibration selections, not calibrated probabilities.
A six-bundle read-only demo of the current USTC inventory is recorded in
`server/.artifacts/depth-recovery-20261014/local-classifier-demo.json`.
The saved 最新招聘公告 list scored recruitment 0.944903984861987 in the direct model
probe; normal combined classification accepted it through rules as notice_list,
with daily revisit and no review flag. Several appointment-guide pages abstained
in the direct local probe but received rules-owned other/recruitment/weekly labels.
These are program outputs, not human gold verdicts. The demo called no LLM and did
not persist source-wide assessments or replace the live pipeline's work.
