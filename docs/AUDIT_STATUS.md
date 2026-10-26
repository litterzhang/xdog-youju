# Audit Status, Material Completeness and Validity

## Independent dimensions

A missing attachment is not automatically a human-review task. The audit policy
`available-material-v1` separates:

- **Extraction:** `parsed`, `needs_review`, `failed`, or `cancelled`.
- **Material:** the immutable bundle's `complete` or `partial` state.
- **Validity:** the script's existing status, date, basis, scope and material issues.

The persisted audit status remains `success` for fully successful extraction and
`partial` for successfully parsed available evidence with known acquisition gaps.
`review` means a content/classification problem needs attention; `failed` means
repair is required. Neither parsed status nor unknown validity guarantees eligibility
or open hiring.

`AuditResult.errors` contains blocking problems. Exact known material warnings are
kept in the original-schema `result.errors` and exposed separately as `warnings` in
summaries/queries. A code-name allowlist alone is not enough: the host requires an
intact readable page, known empty failed attachments, matching input warnings,
nonempty positions, a partial result and successful independent field/evidence
validation. Forged warning messages, damaged pages, unsupported rows/fields and
classification uncertainty cannot become parsed just by using a familiar code.

## Available-material positions

Evidence-valid partial results now have normal SQLite `Position` rows, tagged
`material_incomplete`, without claiming missing material was acquired. Default
queries retain the stricter complete-material behavior. Explicit operator queries
can include partial results with their warnings:

```sh
# From server/. Keep the real database and blob paths together.
uv run flask --app youju source-positions ustc-employment \
  --database .artifacts/ustc-live-20261011/source.sqlite3 \
  --storage .artifacts/ustc-live-20261011/blobs --include-partial
```

Add `--include-expired` only when intentionally inspecting historical cutoffs.
Returned rows include `extraction_status`, `material_status`, `review_required`,
`repair_required`, `warnings` and the existing `validity`. Opt-in still rejects
pending acquisition, stale page versions, changed/pending dependencies, unresolved
classification and blocking evidence errors. A newly downloaded attachment invalidates
an old missing-attachment view until the new bundle is processed.

This is not a frontend publication or a policy that every missing attachment is
unimportant. Qualifications/extra positions in an unavailable attachment remain
unknown. Source activation still requires the existing validated recipe and at
least one fully successful evidence-backed announcement.

## Reassess saved outputs without fetching or calling models

```sh
uv run flask --app youju source-reassess ustc-employment \
  --database .artifacts/ustc-live-20261011/source.sqlite3 \
  --storage .artifacts/ustc-live-20261011/blobs
```

The command requires an active/ready source, unchanged validated toolkit/recipe,
settled acquisitions and saved audits for every current notice on the current
Shanghai evaluation day. It revalidates those outputs in a new audit generation,
then atomically switches the current pointer. It preserves the old audits, rows,
summary snapshot and acquisition timestamps. No authoring, model call, browser or
network access occurs. An interrupted attempt can leave noncurrent records, not
an apparently completed generation. Use `source-reanalyze` for changed recipes or
material/date analysis, rather than treating status replay as new extraction.

## USTC verification on October 12, 2026

The counts below preserve the status-only reassessment. The subsequent real
Agent-authored source repair and full re-audit are recorded in `SOURCE_REPAIR.md`.

The real saved-output reassessment produced:

| Audit disposition | Before | After |
|---|---:|---:|
| Fully successful | 16 | 16 |
| Parsed available material, incomplete attachments | 0 | **133** |
| Needs content/classification review | 141 | **8** |
| Extraction failed | 27 | **27** |

There are 149 parsed announcements, with 28 complete and 156 partial material
bundles across all 184 announcements. These denominators describe different
properties and must not be conflated.

- Complete-material current positions: **126**.
- Available-material positions stored in the current audit scope: **169**.
- Default query: **126**; with partial opt-in: **128**; also including expired: **295**.
- Two additional candidate rows remain under content review, not accepted partial rows.
- Validity is unchanged: **137 expired by observed cutoff, 4 not expired, 43 unknown**.
- Original fetch attempts, captures, versions, documents, bundles and acquisition
  queue rows compare exactly equal before/after. Model calls: **0**. HTTP calls: **0**.
- Source remains `active / ready`; all 184 current audits are present.

The database now contains 547 historical position rows across generations; this
is not a count of distinct current vacancies. Evidence is under
`server/.artifacts/ustc-review-fix-20261012/`: before/after SQLite snapshots,
`reassessed-status.json` and `final-evidence/` assertions.

## Failed-output diagnostics

The separate investigation of the 27 failures is in `USTC_EXTRACTION_FAILURES.md`.
Those failures were not silently repaired by this status reassessment.

The runner now retains a bounded, schema-valid rejected extraction in failed audit
JSON for future diagnostics, while preserving the independently validated script
validity. It never writes rejected fields to `Position` rows or exposes them through
current-position queries. Generic exception logs do not serialize that rejected
result. Previously discarded responses cannot be recovered by this change.

A failed empty placeholder no longer creates a secondary zero-position accusation
when the original rejection concerns other fields. Genuine original empty-result
errors remain. This removes misleading secondary diagnoses, not failed statuses.

Verification: 81 offline tests passed with explicit author-sandbox opt-in; Ruff and
lock checks passed. The 27-case investigation additionally used six real isolated
fast-parser probes and local controlled mutations, not historical model-response
replays. No paid model or new source download was used in this task.
