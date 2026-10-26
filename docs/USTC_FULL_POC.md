# USTC Source POC Verification

Recorded on October 11, 2026 UTC (October 12 in Asia/Shanghai). Artifact directory
names use the Shanghai date. Capture/evaluation times are not publication dates.

This records the original POC. The later saved-material reanalysis and corrected
validity results are documented in `USTC_VALIDITY_FOLLOWUP.md`; historical counts
below are preserved, not overwritten.

## Result

The POC passed: the real approved baseline was classified, an Agent-authored recipe
was validated, all identified announcements were audited, accepted positions were
stored, the source was activated, and real incremental acquisition/discovery ran.
The final persisted v5 profile also passed the 95% rules/local coverage target.
This is a bounded POC with quarantined failures, not production readiness.

## Scope and observed baseline

The real source is `ustc-employment`, published by the registered USTC organization.
The approved page origin is `https://employment.ustc.edu.cn`; discovery remains
inside `ustc.edu.cn`. Other same-root origins remain candidates, not automatically
approved fetch targets. Existing explicit permission for anonymous same-host HTTP
attachments does not extend to HTTP pages or other origins.

The bounded saved inventory contains 1,312 webpage bundles and 1,338 completed
resource tasks, with two retained attachment acquisition failures. The 5,106 URL
registry entries also include unfetched candidates and assets. This is not a crawl
of every USTC subdomain or a claim that every possible website URL was discovered.

Classification, generation and extraction use the installed official PyPI xdog
2.2.0 packages, not neighboring checkouts. The coding Agent uses `gpt-6-astra`, the
complete installed xdog-flow skill in its system prompt, isolated official Bash
and filesystem tools, persistent work/history, and at most 30 model turns per run.

## Real authoring and baseline audit

An initial full-source authoring run exhausted 30 turns after two rejected
submissions: a staff onboarding handbook had been misclassified as a vacancy.
The Agent correctly refused to invent positions. Those failures and their actual
model usage remain in `onboarding/`; they are not erased from cost accounting.

After fixing that upstream role, the Agent resumed the same workspace, used seven
turns and submitted one accepted candidate. Its three actual announcement samples
produced 6, 12 and 2 positions during validation. All were complete HTML samples;
missing attachment formats/templates remain explicitly uncovered. This is not
real-source PDF/Excel/Word extraction success.

The **actual source-wide baseline** then audited 187 identified announcement
bundles under the existing `quarantine-v1` policy:

| Outcome | Count |
|---|---:|
| Successful announcements | 14 |
| Review outcomes | 157 |
| Failed outcomes | 16 |
| Unaudited identified announcements | 0 |
| Accepted positions stored in SQLite | 104 |
| Candidate positions, including quarantined results | 304 |

The validated recipe was activated only after that audit passed. Individual
material/output errors remained quarantined. Errors included missing attachments,
unsupported tables/fields and false-completeness findings. The validity summary
was 186 unknown and one expired; extraction success is **not** proof that hiring
is open or that an applicant qualifies. The 104 accepted records are distinct
from the 304 candidate count.

## Classification quality and cost

The first completed classifier reported 1,254/1,312 = 95.58% confident rules/local
coverage, with 65 classifier provider dispatches. Independent assistant evidence
review found four local recruitment-role mistakes: the handbook, a forum invitation,
and two talent reports. Binary business relevance did not establish vacancy intent.
These findings are preserved, not hidden behind silver-label agreement.

Rules now distinguish onboarding guides; a binary local recruitment prediction
cannot independently turn article-shaped prose into a vacancy. Mixed reports with
an explicit urgent hiring statement remain uncertain rather than confidently
excluded. LLM reservations accumulate across classifier revisions and both tiers;
changing a rule/model profile does not replenish the source budget. See
`PAGE_CLASSIFICATION.md` for split provenance, abstention and evidence review.

The final v5 offline replay covers **1,250/1,312 = 95.274390%** of the saved
baseline: 1,214 confident rule decisions plus 36 local decisions. The 62 review
cases (including 59 unknown-method outcomes) are excluded. The fixed 26-page
assistant spot-check and separate silver-label evaluation are preserved in the
review artifacts. The revised replay made zero external model calls.
The final live persisted profile independently reproduces **1,250/1,312 = 95.27%**,
with 62 review cases, zero unassessed pages and zero pending acquisitions. Its key
is `311b107aa2f9c396f60a21676eef250ebdc7dae2a6b594a6a05be6a7e304135b`.
The revised classification and current identified-announcement audit both finished;
no old profile's coverage number was substituted for the final measurement.
Coverage, agreement with historical model labels, human-reviewed accuracy and
monetary cost are different measurements. No accuracy or recall guarantee is made.

## Expiry and incremental evidence

The accepted generated workflow starts at the isolated `validity:check` script.
Five real-flow/bubblewrap control cases passed: expired, not expired, unknown,
last microsecond of a date-only deadline and the following midnight. Expired cases
use the fast parser with zero model calls. Detailed branches in these controls
use an explicit stub; the source audit above used real models when required.
The three untouched real samples return unknown validity and no explicit deadline.

The post-activation passes performed **203 successful real acquisitions**:
29 static and 174 dynamic, with zero failed post-activation requests. Dynamic
retries ran on later passes, not inside the static attempt. The approved-base
rechecks did not fake due or last-success timestamps. Link extraction wrote
**8,172 new version-specific discovery records**, but found **zero new distinct
URLs** relative to the pre-run registry. A real unchanged-site check is not proof
that new announcements appeared.

After the final v5 classification, all **184 currently identified announcements**
have audit outcomes: 16 success, 157 review and 11 failed. The different denominator
from the original 187 follows reviewed role corrections, not deletion of failures.
The database now holds **126 accepted/current position records**; all have unknown
validity and remain marked for review. None is advertised as confirmed open hiring.
The 200 candidate rows in quarantined results are not included in that 126 count.

The queue ends at 1,338 done and two historical attachment review outcomes, with
no ready/running work. The source remains `active / ready`. Stored discovery
targets and attempted/final fetch URLs have zero root/origin violations. Two
historical failed attempts lack final URLs; no final destination is invented.
This database check supplements the actual transport's DNS/redirect/robots checks.
No scheduler service, notification, upload, publication or deployment was installed.

### Observed model dispatches

The recorded cycle used 74 classifier dispatches: 65 during baseline classification
and nine after new material versions appeared. Cumulative capacity is
`floor(1487 × 0.05) = 74`; all 74 reservations reconcile with the call ledgers.
The final v5 reclassification added zero model calls. These ledgers do not include
experiments preceding the pre-run snapshot.

Separately, there were 50 detailed extraction calls and two author runs using
30 failed + 7 successful turns: 161 observed provider dispatches across these
verification ledgers. The classifier budget is not a limit on authoring or
extraction, and dispatch/token counts are not a monetary billing statement.

## Artifacts and reproduction

Canonical state and immutable materials:

- `server/.artifacts/ustc-live-20261011/source.sqlite3`
- `server/.artifacts/ustc-live-20261011/blobs/`

New verification evidence under `server/.artifacts/ustc-full-poc-20261012/`:

- `before.sqlite3`: pre-run database snapshot.
- `classification/`: original classification ledger and denominator.
- `classification-review/`: fixed evidence sample, historical replay and review.
- `onboarding/`: rejected handbook authoring run.
- `onboarding-v3/`: successful authoring, full baseline audit and first real recheck.
- `baseline-audit.json`: preserved completed onboarding audit.
- `accepted-candidate.json`, `accepted-author-report.json`: actual accepted recipe.
- `expiry-final/`: generated-workflow branch traces, with no paid model calls.
- `incremental-v4/`, `incremental-v5/`: real capture, profile and audit progress.
- `final-evidence/evidence.json`: read-only database report after workers exited.
- `final-evidence/verification.json`: reconciled dispatches and explicit POC checks.
- `after.sqlite3`: final SQLite snapshot; retains references to the original blobs.

After stopping the worker, export a read-only SQLite snapshot report from `server/`:

```sh
uv run python scripts/report-source-poc.py ustc-employment \
  --database .artifacts/ustc-live-20261011/source.sqlite3 \
  --storage .artifacts/ustc-live-20261011/blobs \
  --phase final --output .artifacts/new-final-evidence
```

The output location must be new. `final` is an operator label, not a success flag;
inspect pipeline, classification, audit, position and boundary fields. The utility
uses SQLite's online backup API with a read-only source connection and never
initializes the original database. Normal SQLite read locks/WAL sidecars may occur.

The separate `.artifacts/onboarding-full-poc-regression-20261012/` regression used
synthetic HTTP, real Chromium/bubblewrap/xdog-flow and model stubs. It verified a
new link, attachment-only change and later-pass dynamic retry, producing three
current synthetic positions. It is not substituted for the real-source evidence.

## Verification

- Final offline suite with real author-sandbox opt-in: **49 tests passed**.
- Ruff lint and formatting passed; **76 files** checked for formatting.
- `uv lock --check` passed, resolving the existing **69 packages**.
- Five final generated-workflow expiry controls passed with zero paid calls.
- Seven separate artifact-review tests passed; these are not part of the 49.
- Synthetic HTTP/Chromium regression and the real-source run are reported separately.
- All diagnostic workers exited. No frontend or WeChat verification was performed.

## Remaining limitations

No claim of 100% parsing, announcement recall, cross-source model generalization,
OCR/scanned-PDF recovery, automatic correction linkage, eligibility matching,
frontend integration, authentication, WeChat device validation or deployment.
Missing/unapproved attachments are not bypassed. Current-position queries retain
freshness, dependency, classification and evidence checks and expose uncertainty.
