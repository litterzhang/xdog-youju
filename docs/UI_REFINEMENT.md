# UI refinement (2026-10-16)

## Navigation and presentation

- Shared `ActionButton` and `ActionIcon` use packaged monochrome PNGs on both
  targets. Native Button loading/disabled/form/platform props remain intact.
  Read-only `MenuRow` metadata renders as a View, not a button. Icon sources are
  reproducible with `client/tools/generate-action-icons.py`. H5 icon placement
  is explicitly anchored to prevent centered button text from clipping icons;
  flex actions use automatic width so account IDs and pagination labels remain
  readable.
- Jobs use full-width list rows with separators. Filters, search, uncertain
  deadlines, source attribution and material warnings remain visible.
- `pages/jobs/feedback/index` owns the feedback form and the signed-in user's
  records. Job details remain public; their feedback link preserves the job ID
  and partial-material scope. Feedback records problems; it does not invoke
  Recipe repair or change job facts.
- Account identity and its selectable/copyable ID share one user card.
- Source status is a concise overview. `pages/admin/onboarding-detail/index`
  holds the frozen baseline, onboarding timeline, audit, queue and task details.
  Current/candidate recipe IDs have separate status rows, short display IDs and
  full-ID copy actions. A candidate is not presented as an adopted version.
- Admin feedback has a plain introduction and status-filter toolbar, followed by
  separated records and literal submission-time snapshots rather than nested
  cards. Review still requires a note and explicit confirmation.
- Both new pages use native navigation titles/back behavior, outside the tab bar.

## Incremental results

The incremental page selects one batch and displays only its new or
material-changed notices and associated position snapshots. These are associated
records, **not newly vacant jobs**. Unchanged notices, list pages, assets and
whole-source audit totals do not appear as incremental results.

The admin-only read endpoint is:

```text
GET /api/admin/sources/<source_id>/incremental/runs/<run_id>?page=1
```

`summary.delta` contains `state`, `reason`, `notice_count`, `position_count`,
`new_notices` and `changed_notices`. `notices` contains the paginated records.
The projection compares exact audited bundle identities against the saved run
baseline, including attachment-only changes to a parent notice. It uses the
run's audit membership, recipe, evaluation day, generation and source scope.
Missing historical baselines/audits produce explicit unavailable/pending states
and null totals, not a zero-update assertion. The UI lists 20 notices per page
and at most 10 position snapshots per notice, with truncation/unavailable labels.
Position fields retain source/AI-inferred/unknown tags.

This is a read-only presentation change: no schema migration, recrawl, model
calls or changes to admission, audit membership or incremental scheduling.
The API and client changes must be deployed together; old responses do not
provide the new batch-detail contract.

## Local verification

- `cd client && npm run typecheck && npm test`: 107 tests passed.
- Official Taro H5 and WeChat builds passed. Staged outputs are under
  `client/.artifacts/ui-refinement-20261016/{h5,weapp}`; served builds were not
  overwritten. H5 retains a webpack entry-size warning.
- `cd server && uv run --frozen pytest tests/test_incremental_view.py
  tests/test_lifecycle.py tests/test_onboarding.py tests/test_auto_repair.py
  tests/test_admin_api.py tests/test_jobs_api.py -q`: 149 tests passed.
- Ruff passed for the modified backend modules and incremental projection tests.
- Browser verification uses local staged H5 and intercepted synthetic API data,
  never production identity, live crawling or paid models. Evidence and the
  standalone smoke script are under
  `client/.artifacts/ui-refinement-20261016/browser/`. Seven pages passed at
  375px width, including list-to-detail-to-feedback navigation, account-ID width,
  expanded incremental positions, no horizontal page overflow and no missing or
  clipped action icons. This is browser evidence, not WeChat device evidence.

No service restart, deployment, WeChat upload, release or physical-device
verification is included in this change.

## Approved rollout follow-up

A subsequent explicit deployment/upload request published the API and H5 on
October 16, 2026 and uploaded WeChat **0.1.9** at **11:26:32 Asia/Shanghai**.
No schema migration was needed; registration, saved material, position rows and
the existing enabled incremental setting were retained. The API and its source
companion restarted; Recipe did not restart and shared Nginx configuration was
unchanged. Public HTTP and anonymous live-browser jobs/detail/feedback navigation
passed. Rollback copies, receipt/QR and checks are in
`server/.artifacts/ui-rollout-20261016/`; see `WECHAT_UPLOAD.md` for evidence and
remaining device/review/release limitations.

## Local design correction after 0.1.9

The first rollout over-applied action icons and flattened the visual hierarchy.
The follow-up restores text-only ordinary actions. Icons in action buttons are
limited to directional navigation: previous arrows precede the label, next arrows
follow it. Row disclosures and select chevrons remain; native loading indicators,
disabled states and permission gates are unchanged.

Jobs remain a contiguous list, not separate cards. A shared white surface,
consistent insets, title/unit hierarchy, compact fact tags, deadline status and a
source/check-time footer replace repetitive explanatory paragraphs. AI-inferred
values remain marked, unknown fields remain explicit, and partial material still
has a visible badge. Full provenance and conditions stay in job details.

Source management uses a compact count/action toolbar and styled list rows instead
of large metric tiles and a generic menu. Rows distinguish source verification,
onboarding, maintenance and incremental blockers without showing artificial
completion percentages. Attention counts are explicitly page-local. Source overview
uses the same spacing and surface treatment. Routine explanatory copy is reduced
on home, source overview, feedback and account pages; safety confirmations, errors,
material warnings and native navigation are retained.

Verification: TypeScript and 109 client tests passed. Official H5/weapp builds are
staged under `client/.artifacts/ui-polish-20261016/`. Local Chromium checks use
synthetic API data across nine pages at 375px and 320px, including navigation to
standalone feedback, source attention labels, long account IDs and unclipped arrow
assets. These are not production or WeChat-device checks. H5 retains the existing
webpack entry-size warning.

This correction is local only: it does not overwrite the served H5, restart a
service, migrate data or upload another WeChat version. The deployed/uploaded
version remains 0.1.9 until a separate rollout is requested.

## Approved correction rollout — October 16, 2026

A later explicit request published the corrected H5 and uploaded WeChat **0.1.10**
at **11:44:10 Asia/Shanghai**. The API/source-worker/Recipe processes were not
restarted and no schema or Nginx configuration changed. Public asset verification
and anonymous live-browser jobs/detail/feedback checks passed, including correctly
positioned pagination arrows and text-only ordinary actions. Existing H5 is retained
for rollback. Receipt, QR and checks are in
`server/.artifacts/ui-polish-rollout-20261016/`. See `WECHAT_UPLOAD.md` for the upload
record and outstanding experience-selection/device/review/release limitations.

## Source-admin page follow-up (local, October 16, 2026)

The source entry is full-width again (48px minimum height). The status page's
native title is now `来源详情`; parsing versions and source-wide parsing totals have
independent white panels. Onboarding details, incremental batches and feedback
records share `components/admin.tsx` spacing, typography, stats and surfaces.
Onboarding task/version diagnostics and feedback submission snapshots expand on
demand. Feedback's notice and status filter remain outside panels. Permissions,
review confirmations, required notes and incremental enable/disable behavior remain
unchanged; ordinary action buttons remain text-only.

A new admin-only read endpoint supplies the source-wide card:

```text
GET /api/admin/sources/<source_id>/parsing-summary
```

It counts notices identified in current saved bundles, not resource URLs, historical
bundle duplicates, frozen admission totals or a single incremental delta. Statuses
use the latest saved audit for the active recipe and publication generation;
candidate recipes are excluded. Position counts require matching saved page versions.
Historical/expired notices are included, and saved audit-date ranges are explicit;
these counts are not current vacancies or a complete-site coverage claim. Missing
audits remain pending. Request failure shows unavailable rather than fake zeros.
No parsing, fetching, model calls or database writes occur on this endpoint.

Verification: 110 client tests, TypeScript, 14 focused backend tests and Ruff passed;
H5/weapp builds passed (the existing H5 entry-size warning remains). Synthetic browser
checks covered nine pages at 375px and 320px, panel scope, the restored button size,
expanded diagnostics/snapshots and unavailable-summary behavior. The projection also
ran read-only against a previously saved USTC database backup, not the live database.
Evidence is under `client/.artifacts/admin-pages-20261016/` and
`server/.artifacts/admin-pages-20261016/`.

This follow-up is not deployed/uploaded and is not WeChat-device verification.
A later rollout must include the new server endpoint with the client; no schema
migration is required. The served H5 remains the verified 0.1.10 build.

## Approved upload follow-up — October 16, 2026

A later upload request uploaded WeChat **0.1.11** at **13:21:31 Asia/Shanghai** and
deployed its required read-only summary endpoint. API and its source companion
restarted; Recipe did not. Source data/incremental settings were retained with no
schema migration. The entire served H5 manifest remains unchanged at 0.1.10.
Receipt, QR, checks and database backup are in
`server/.artifacts/admin-upload-20261016/`; see `WECHAT_UPLOAD.md` for verification
boundaries and remaining experience-selection/device/review/release limitations.
