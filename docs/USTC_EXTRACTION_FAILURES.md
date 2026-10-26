# USTC Extraction Failure Analysis — 2026-10-12

## Scope and reproducible evidence

This is an independent, offline investigation, not a production repair or a new
model audit. It reads `server/.artifacts/ustc-validity-followup-20261012/after.sqlite3`
in SQLite read-only/immutable mode and the existing
`server/.artifacts/ustc-live-20261011/blobs`. No model calls, network requests,
downloads, database writes, or production/test-file edits were performed.

The query joins the current `resource_scans.bundle_id`, the active source recipe,
the pipeline's configured audit generation, and its recorded classification key:

- Recipe: `b8f393fad6f147bc9674652e3b38130b`.
- Generation: **6**; evaluation day: **2026-10-12**.
- Classifier: `311b107aa2f9c396f60a21676eef250ebdc7dae2a6b594a6a05be6a7e304135b`.
- Only current article assessments classified as recruitment/correction/cancellation.
- Result: **184 audits = 16 success + 141 review + exactly 27 failed**.

Artifacts are in `server/.artifacts/ustc-review-fix-20261012/failures/`:

- **`failures.jsonl` has exactly 27 rows**, one per URL/current bundle, with audit
  ID, original/bundle blob references, complete persisted errors and field paths,
  unchanged validity, original evidence, dominant diagnostic cause, and uncertainty.
- `summary.json`: notice-level counts, selection keys, database/toolkit SHA-256.
- `probes.json`: six real Sandbox fast-parser replays and labeled control inputs.
- `inspect_failures.py`: the standalone reproducer, including curated source observations.

From `server/`, using the existing environment:

```sh
PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync python \
  .artifacts/ustc-review-fix-20261012/failures/inspect_failures.py
```

The script verifies original blob hashes and resolves stored block locators against
all 27 original HTML pages. Inline-link fragments may be substrings of their parent
DOM node; these are recorded separately, not called parsing failures. Generated
code executes only through the actual `Sandbox`, with no unsafe fallback. Its
host temporary directories and reports remain inside the allowed artifact directory.
Replays use the current toolkit; its hash is recorded, so a later toolkit repair
may intentionally change replay results. The historical database is never updated.

## Counts: affected notices, not error occurrences

All 27 initial failures have `failure_code=RESULT_INVALID`: independent result
validation rejected an extraction. The original error paths **do not store the
rejected values or their cited field evidence IDs**.

| Persisted code | Notices | Present in original rejection |
|---|---:|---:|
| `UNSUPPORTED_FIELD` | 17 | 17 |
| `CROSS_POSITION_EVIDENCE` | 13 | 13 |
| `TRUNCATED_LITERAL_FIELD` | 13 | 13 |
| `FALSE_COMPLETE` | 3 | 3 |
| `UNCHECKED_ROWS` | 2 | 2 |
| `SUSPICIOUS_ZERO_POSITIONS` | 17 | **2** |
| `attachment_outside_approved_scope` | 17 | 0 |
| `INCOMPLETE_MATERIAL` | 17 | 0 |
| `INCOMPLETE_RESOURCE_BUNDLE` | 17 | 0 |

These columns are not additive. Material warnings coexist with extraction failures.
The extra **15** zero-position diagnoses are a historical failure-handling artifact.
In the audited run, `RecipeRunner.run` raised without retaining the rejected result,
`_audit` replaced positions with `[]`, and `AuditLedger.record` validated that
replacement again.
They are **not evidence that the model returned no jobs**. Even `FALSE_COMPLETE`
alone does not reveal whether an original result was empty or contained errors.

### Mutually exclusive dominant diagnostic categories

These are evidence-based triage groups, **not proven historical model mistakes**.
Where causes overlap, title-anchor failures take precedence, then remarks dates,
then prose counts. The full per-row record retains the secondary findings.

| Dominant category | Notices | Evidence strength |
|---|---:|---|
| Table title/row-anchor contract mismatch | **13** | Consistent source/error pattern; mechanism reproduced on the three priority notices |
| Remarks-column date validator gap | **2** | Valid source dates demonstrably rejected; six additional overlapping title cases |
| Prose headcount validator gap | **7** | Correct source-based integer controls rejected on all seven |
| Other prose failures; response unavailable | **2** | Cannot attribute exact output failures; one additional missed-date observation |
| Forum news misclassified as recruitment | **1** | Original article and stored rule rationale establish mismatch |
| Empty display tables treated as vacancies | **2** | Original child tables contain only a zero-count display row |
| **Total** | **27** | |

## Priority: the three not-expired failures

Dates below are the **saved observations on 2026-10-12**, not live availability.
All three retain `scope=available_materials` and their missing-attachment warnings.
The date block is `b15` in each original page; full evidence IDs are in the JSONL.

| URL `sign` | Unit; literal position cell | Saved cutoff | Bundle |
|---|---|---|---|
| `639251699201434011` | Hospital; `大内科临床医师 （ 1 人）` | **2026-10-15** | `221ef149822244f992b7247508c53b45` |
| `639255996340592006` | Staff union; `管理助理 （ 1 人）` | **2026-10-31** | `b3ff8b3ea5fc42c3ba85f35de81976d2` |
| `639255997010035012` | Finance department; `管理助理 （ 1 人）` | **2026-10-31** | `94a2c1e0c3c74b21a0c48b00f6eff333` |

Each original has one five-column job row, with readable title, major, education,
duties, and requirements. The stored recipe's **fast parser produced one position
for each**, with **zero host validation errors** and only material warnings.
This was a direct fast-parser probe, not a replay of the original model response
or a full production workflow, and it published nothing.

Controlled removal of only the parenthesized headcount from the returned title,
without changing any evidence or other field, reproduced the **exact original
code/path set in all three**: six `CROSS_POSITION_EVIDENCE` errors on title,
headcount, education, major, requirements and duties, plus one
`TRUNCATED_LITERAL_FIELD` on title.

`toolkit._table_field_errors` anchors a row only when title text equals the whole
cited cell. A shortened title leaves no anchor, making even that same row's valid
conditions appear cross-position. The full-cell rule is explicit in the extraction
prompt; the evidence does **not** justify disabling it or assuming actual job mixing.
Keep the literal title, use the existing deterministic table path where valid,
and report a missing title anchor without misleading cascades. Whether the original
model actually removed this suffix remains unprovable without response capture.

## Concrete validator and classification findings

### 1. Prose counts have valid evidence but no accepted representation

Affected signs: `638346140958191006`, `638712526917179100`,
`638790576275594006`, `638797227516794012`, `639104663982348028`,
`639112620144132006`, `639113412864720006`.

Examples from originals include deputy directors `招聘副主任 3 名。` (block b5),
professors `公开招聘教授一名。` (b3), and engineer titles ending `岗 1 人` (b11).
`_count` accepts only an entire numeric cell; `_embedded_count` requires a terminal
parenthesized Arabic count. **No evidence block in any of these seven bundles**
matches either accepted grammar. Correct integer controls therefore fail even with
real, role-bearing source references. The planetary-physics and professor notices
have only a headcount error in their original rejection.

Add narrowly scoped, role-linked normalization for explicit prose counts, including
unambiguous Chinese numerals. Do not extract arbitrary numbers from ages, benefits,
staffing background, ranges, or combined multi-role sentences. Keep `1-2`/unspecified
counts unresolved. Other failed fields on these notices still require raw response
capture; the controls do not prove what the original model returned.

### 2. Per-job dates in remarks are recognized, then rejected

Eight signs have this gap: `638808470305094026`, `638948471906151011`,
`638975285074640011`, `638980167898333102`, `639004585012881876`,
`639060821188548013`, `639114316983559006`, `639154124306312006`.
Six also have title-anchor failures, explaining the dominant-category count of two.

`_deadline_analysis` explicitly recognizes application cutoffs embedded in other
position-table cells and records their IDs as `per_position`. But
`_supported_deadline` accepts a table date only from a column mapped to
`deadline_text`; the actual header here is **`备注`**. Its global-date branch is
also disabled by the per-position reason, so correct row dates cannot pass.

For `639154124306312006`, both original rows say **2026-06-30**. The sandboxed fast
parser returns two evidence-valid rows but leaves normalized deadlines null.
Adding precisely that date with each row's own remarks evidence produces only the
two `UNSUPPORTED_FIELD` errors on `deadline_on`. This is a demonstrated validator
false negative, not proof of a hallucinated model date.

Support explicit application-date clauses in the **anchored row**, preserving
conflict/rolling checks. Do not globally alias all remarks to deadlines or promote
row dates to whole-notice expiry. All eight persisted validity objects stay unknown.

### 3. A forum report and two empty tables are not ordinary extraction failures

`636310024125268039` is the opening report of the first Mozi Forum. Stored rules
combine the personnel-policy discussion at b6 with **forum attendance registration**
at b9 into an article/recruitment assessment with `requires_review=false`.
The original does not provide a vacancy call. The broad recruitment/application
co-occurrence rule in `classification.py` needs context, not another model retry
trying to invent positions.

`jobjs.aspx?sign=12070` and `12130` have the six-column vacancy header followed only
by **`["0", "", "", "", "", ""]`**. Raw HTML confirms the zero display row and
empty job cells; it is not a missing extraction of a readable job. Classification
uses header/data-row presence as positive evidence, while `_display_count_row`
requires subsequent nonempty rows and the zero-position check treats any post-header
row as suspicious. The `12070` sandbox replay consequently returns review, no jobs,
`ROW_SKIPPED`, `NO_POSITIONS`, and `SUSPICIOUS_ZERO_POSITIONS`.

Recognize an evidence-proved empty display table separately from unsupported vacancy
rows. This does not prove that vacancies never existed, authorize login, or justify
inventing a role from the surrounding school heading.

## Remaining uncertainty and unchanged validity

The remaining two prose notices are not safely attributable:

- `635364785783906109`: nine explicit dean roles and shared duties/conditions;
  rejected requirements, duties, arrangement and date values were discarded.
  Additionally, b45 `七、报名时限` followed by b46
  `自公告发布之日起，至2014年6月30日止。` exposes a heading-pairing gap in
  `_deadline_blocks` (neither its heading predicate nor next-paragraph prefix
  accepts this pair). **The saved unknown/not_stated result is preserved**, not
  silently changed to expired by this extraction-only analysis.
- `638731548331617007`: explicit deputy-director recruitment with a one-to-two
  range; title and employment-arrangement errors have no retained values. Neither
  a staffing guarantee nor a contract-employer inference is warranted.

The 27 persisted validity objects remain **3 not_expired, 24 unknown, 0 expired**;
unknown bases are 11 rolling, eight per-position, and five not-stated. A past
round-specific date alongside rolling recruitment is not silently substituted for
whole-notice validity.

Future diagnostics should preserve the bounded rejected extraction and its evidence
references separately from publication, plus the failure stage and validator
version. The main task is handling that change; this report does not verify it.
Avoid credentials and
unnecessary personal data. Without this capture **all 27 exact historical rejected
responses are unavailable**; even a matching control cannot establish the historical
value. The source/validator defects above are independently demonstrated and must
not be flattened into either “all model hallucinations” or “all validator bugs.”

## Verification and limits

The local reproducer completed six real isolated fast-parser examples, seven
source-based count controls, the remarks-date control, and the three exact
code/path cascade comparisons. It checks 27 unique failed bundles, current selection
counts, original hashes/locators, and unchanged snapshot SHA-256. Ruff lint and
format checks passed for this artifact script. No new tests were added or repository
suite/model/browser/WeChat/deployment verification claimed. Production changes,
response capture, and any future re-audit belong to the main task, not this report.
