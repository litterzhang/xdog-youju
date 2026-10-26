# USTC Validity and Partial-Material Follow-up

This report preserves the earlier validity repair. The later status-only
reassessment and separation of material warnings are recorded in `AUDIT_STATUS.md`.

## Verified outcome

The real reanalysis loop passed using the saved source. It ended at `active / ready`
with all 184 current notices audited, no pending acquisition tasks and a new
validated source recipe. The persisted audit independently matches the local date
replay: **137 expired by observed cutoff, four not expired, 43 unknown**. All
137 expired notices used script extraction with **zero extraction-model calls**.

Database comparison found **zero HTTP requests during reanalysis**, one successful
approved-base request during final incremental verification, and zero new original
content versions. The existing baseline was not downloaded again.

This did not turn partial data into complete success. Final audit outcomes are
16 success, 141 review and 27 failed. There are 126 current accepted positions and
171 candidate positions retained with warnings in audit JSON; the latter are not
published/current rows. Historical `positions` now has 252 rows across recipe
versions, not 252 distinct current jobs. All 126 fully admitted positions remain
unknown-validity because their source notices have no fixed cutoff or are rolling.
The newly recovered fixed deadlines mostly belong to partial notices.

Three position-extraction failures retained a known `not_expired` cutoff rather
than losing it. Thus temporal evidence can be inspected even when job extraction
is not ready. Twenty-seven extraction failures still require parser/model/evidence
repair; the run is not a claim of improved complete-parse success rate or live
application availability.

## Findings from saved evidence

The previous 184-notice audit had 183 unknown validity results and one expired
result. This was not primarily missing date text or a model limitation:

- **112 notices already had a recognized explicit deadline**, but missing
  attachments invalidated the whole date decision.
- HTML whitespace could split digits, such as `202 6`; the old date regex missed it.
- Short application headings and their following date paragraphs were not linked.
- A notification *after* the application cutoff could invalidate the actual cutoff.
- Position-extraction exceptions replaced an already validated script result with
  unknown validity.
- The 16 complete successful notices were a biased subset: 14 had no stated cutoff
  and two stated open-ended recruitment. Their 126 accepted positions therefore
  did not represent the date availability of the excluded notices.

The 154 attachment-origin failures were local policy decisions, not remote HTTP
rejections: 153 notices referenced `hr.ustc.edu.cn`, one referenced
`wvpn.ustc.edu.cn`. The corresponding blocked downloads were not attempted. This
follow-up does not grant new origin permissions, bypass a VPN/login, or claim the
remote files are accessible.

## Date evidence is separate from material completeness

`check_validity` still receives an explicit clock and runs inside the generated
validity script. It now reports:

- `status`: `expired`, `not_expired`, or `unknown` against obtained date evidence.
- `deadline` and original `evidence_ids`.
- `basis`: explicit deadline, rolling recruitment, per-position periods, unstated,
  unreadable, conflicting, event-related, or unsupported evidence.
- `scope`: `complete_materials` or `available_materials`.
- `material_issues`: completeness warnings, never silently erased.

An unavailable attachment no longer erases a readable page's explicit cutoff.
`available_materials` does **not** prove that no unseen extension/correction exists.
The independent host validates and preserves the scope as well as the date. A
failed position extraction keeps the script's validated validity instead of
substituting unknown. Unknown does not mean open or expired.

Date-only deadlines include the whole Shanghai calendar day; explicit `24:00`
is the next midnight. Other exact-time forms remain conservatively unsupported.
Whitespace-separated digits and a short application heading followed immediately
by its date paragraph are recognized without rewriting original evidence. Both
heading and date references survive. Age, publication, arrival and capture dates
are not substituted. Conflicts, unreadable pages and per-position schedules do
not establish whole-notice expiry. Rolling/until-filled notices retain unknown
fill status with an explicit `rolling` basis.

At the same saved evaluation clock, the deterministic local replay changed:

| State | Before | After local repair |
|---|---:|---:|
| Expired | 1 | 137 |
| Not expired by observed cutoff | 0 | 4 |
| Unknown | 183 | 43 |

The 43 unknown cases comprise 19 without a recognized cutoff, 14 rolling/until-filled,
eight per-position schedules and two incomplete pages. This replay used no new
HTTP requests or model calls. These are saved-material observations, not a live
confirmation of application availability.

## Continue parsing incomplete material

Readable partial bundles can reach the configured detailed model rather than
being unconditionally routed to a table-only fallback. Expired material still
uses the generated fast parser. Unreadable material does not trigger a pointless
model request. The host retains missing URLs/errors and forces partial/review
status; it does not turn incomplete input into full success.

Evidence-valid partial position candidates remain in SQLite audit JSON. Default
current-position queries continue to exclude them; this change does not quietly
publish uncertain rows or weaken field/evidence checks. Policy-only missing
attachments with valid candidate rows do not justify rerendering their parent
page. Other execution or evidence errors are not treated as harmless warnings.

Source-authoring selection now covers prose and table structure. It may include
one confidently classified, readable prose notice with missing attachments.
Such a sample must return nonempty, evidence-valid partial results and only known
material warnings. Complete samples still require complete success; unsupported
fields, omitted known rows and empty results still fail preflight. The isolated
local check and the host submission check share that rule.

## Reanalysis without downloading the baseline again

From `server/`:

```sh
uv run flask --app youju source-reanalyze ustc-employment \
  --database .artifacts/ustc-live-20261011/source.sqlite3 \
  --storage .artifacts/ustc-live-20261011/blobs
```

This only schedules saved-material classification, fresh authoring and audit. It
preserves captures, documents, fetch history, timestamps, acquisition failures and
old recipe/audit generations. It refuses an active operation lease or pending
acquisition work. The old summary is linked through an immutable blob reference.
The previous recipe is not retired until its validated replacement activates.
No origin is approved and no URL is fetched by this command.

The verified full-loop rerun used the persistent `gpt-6-astra` author, full
installed xdog-flow skill, official isolated tools and 30 turns per run. The first follow-up author run exhausted 30 turns: repeated detailed extraction
inferred a contract employer from an institutional recruitment announcement. No
validator was relaxed. The host extraction prompt was made explicit about the
existing contract-evidence rule, after which the resumed Agent completed in
**12 turns with one accepted submission**. Both runs and their usage are retained.
The successful run performed fresh authoring, all 184 announcement audits and the
single actual incremental recheck.

## Evidence and verification

Artifacts: `server/.artifacts/ustc-validity-followup-20261012/`.

- `before.sqlite3`, `toolkit-before.py`: preserved pre-change state and date logic.
- `validity-before.json`, `validity-after-local.json`: all 184 saved notice IDs,
  original body references, reasons and before/after date findings.
- `reanalysis-start.json`: analysis-only transition.
- `live/`: the failed initial author run, retained rather than overwritten.
- `live-prompt-repair/`: successful real authoring, full audit and incremental run.
- `accepted-candidate.json`, `accepted-author-report.json`: actual validated recipe.
- `notice-results.json`: all 184 audit outcomes, date basis/scope and errors.
- `final-evidence/`: database report plus verified acquisition/model deltas.
- `after.sqlite3`: final snapshot referencing the existing blob directory.
- `expiry-traces/`: actual generated workflow + sandbox controls, detailed model
  boundary explicitly stubbed; all five controls passed.

The full offline suite with explicit real author-sandbox opt-in passed 79 tests;
Ruff and formatting checks passed. These tests are separate from the real-source
rerun. No OCR, automatic amendment linkage, unseen-file interpretation, frontend
or deployment validation is claimed.

Final checks: 79 tests passed with the author sandbox opt-in; Ruff lint/format and
`uv lock --check` passed. Actual source model ledgers contain 68 detailed calls
across the failed and successful author/audit runs, and two author runs; no
classification or date-model calls were added. Completion of a provider response
is not an audit pass. All diagnostic workers exited, and no service was deployed.

Subsequent failure inspection found at least one unhandled `报名时限` heading/date
pattern among the 19 not-recognized cases. `not_stated` is a parser finding, not
proof that the original contains no deadline. See `USTC_EXTRACTION_FAILURES.md`;
the status-only reassessment did not change temporal evidence or claim this date
pattern had been repaired.
