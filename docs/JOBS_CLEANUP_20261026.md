# Legacy Job Cleanup and Mobile Feed — October 26, 2026

## Live data correction

At the user's explicit request, the active runtime database no longer retains
legacy job entities or their schema-only migration copies. The explicit
`purge_legacy_positions.py` command acquired the existing source writer lock,
created a consistent private SQLite backup, and deleted **5904 rows**: 2952 v2
historical positions and 2952 migration-derived positions. Public `/api/jobs`
now returns **zero** until genuine new Recipe extraction produces jobs.

Original pages, attachments, material versions, source registration, Recipes and
audit history are retained. SQL comparisons confirmed these tables were not
removed or changed, and foreign-key checks passed. Genuine v3 quick/detailed
positions are outside the deletion predicate. Cleanup refuses to delete jobs
with user feedback until that feedback is explicitly archived; no user reports
were silently removed. It is an operator command, not startup or read-path logic.
Backups are not a route for restoring these legacy jobs automatically.

## Why employment fields were empty

The pre-cleanup public projection had 328 rows across 165 notices: all 328 had an
unknown `employment_type`; only one had a known `staffing_status`. Inspection of
the actual legacy Recipe showed it only assigned `public_institution_staff` for
one literal staffing phrase. It did not extract labor dispatch or personnel agency
from notice headings/body. The migration copied unknowns; it was not new parsing.

Among the 165 saved notice bundles, **147 contained the phrase 劳务派遣**. The
current generic draft helper also identified employment fields in 147 bundles.
These are diagnostic observations, not independently audited per-position labels;
scoping, negation and conflicting rules still matter. No fields were bulk-filled
from keyword occurrence, and no paid model or Recipe generation ran for this task.

The author system prompt now requires separate employment/staffing extraction
from available headings, body and table rows, scoped inheritance, use of shared
helpers, and a field-level employment-bearing test. Source scripts must be repaired
by RecipeService, not hand-edited by the runtime host. New author instructions
require normal service deployment before future tasks use them.

## Client changes (not published by this task)

The jobs page now has an accumulating mobile feed, native pull-to-refresh and
reach-bottom loading instead of previous/next/page-number controls. Loading failures
retain existing rows and offer a local retry. Filter changes invalidate stale
requests, restart transport pagination, and remove old-filter rows; returning from
detail does not auto-refresh and collapse the accumulated list. IDs are deduplicated.

Empty results use one quiet text line, not a large icon/card or verbose tips.
Employment and staffing badges render independently, so known staffing no longer
looks unknown merely because the separate employment mechanism is missing.

Verification uses focused server tests, client tests, type checking, isolated
H5/weapp builds and an isolated browser with saved DTO fixtures. Native browser
scrolling appended 20 → 40 → 45 rows, and search/reset/empty-state checks passed.
No live H5 replacement, WeChat upload, service restart or paid reparsing is included.
See `server/.artifacts/jobs-cleanup-20261026/` for receipts and diagnostics.

Verified results: 18 focused server tests and 126 client tests passed; type
checking, Ruff, isolated H5/weapp builds and the final staged browser check passed.
WeChat device behavior has not been verified. The prior published H5 file
manifest remains unchanged; no new WeChat version was uploaded.

## Subsequent WeChat upload — October 26, 2026

At the user's request, the mobile-feed client was subsequently uploaded as
**0.1.14** through the official Taro mini-ci command. The uploader returned exit
code 0 and an explicit upload-success receipt. Generated jobs-page settings
retain native pull-to-refresh and reach-bottom loading. The served H5 manifest
is unchanged. This is a development upload, not review submission, release or
WeChat device verification. No Recipe regeneration or paid reparsing ran.
Upload evidence: `server/.artifacts/wechat-mobile-feed-20261026/receipt.json`
and `upload.log`.
