# Finalized Onboarding, Opt-in Monitoring and Public Jobs

## Source lifecycle

Onboarding ends after a completed, passing source admission audit. The activation
transaction stores a write-once `onboarding_results` snapshot: baseline recipe,
audit, completion time, summary and acquisition counts. The public admin projection
uses `stage=completed` and `state=completed`; the internal pipeline retains `active`
for recipe/CLI compatibility. Subsequent recipe maintenance is projected separately.
Neither monitoring failures nor a later repair replace the first admission result.

`incremental_controls` defaults to **disabled**. An administrator with the current
central `youju:source:manage` permission can use the independent native-title
**增量抓取** page, reached from the source's onboarding status page:

- `GET /api/admin/sources/<id>/incremental`: switch, state and last 20 runs.
- `POST /api/admin/sources/<id>/incremental`, body `{"enabled": true|false}`.
- Enable requires an approved source and completed admission/maintenance.
- Disable persists independently of the acquisition process lock. An already
  started bounded pass finishes safely; future passes, including the remainder of
  an unfinished cycle, pause until re-enabled. This is not immediate cancellation.
- Re-enabling clears monitoring's blocked control state, not its history or the
  underlying cause. The UI retains errors; configuration/material limits are not bypassed.

A scheduled worker does nothing for a disabled source. Enabled sources create no
empty audits when there is no due/pending work. Each cycle has an independent
`incremental_runs` record, resumed across bounded passes and process restarts.
Results include acquired/failed counts, newly registered resources, changed content
versions, a bounded URL-level difference list, and its own audit/errors. Changes
are resource/version differences, **not** inferred changes to hiring conditions.
Classification/recipe failures block monitoring, not the frozen baseline.
Explicit trusted `source-tick` CLI calls remain one-shot operator actions;
they do not enable the recurring switch. They also preserve independent history.

## Existing database rollout

Normal application startup never creates or migrates a database. Before restarting
updated services, stop source writes, take a consistent SQLite backup, then run from
`server/` using the actual deployment paths:

```sh
uv run flask --app youju source-lifecycle-migrate \
  --database /path/to/youju.sqlite3 --storage /path/to/source-blobs
```

This additive, idempotent command creates missing tables and recovers the **first
completed, passed onboarding audit**, never the latest incremental summary. It
keeps the original audit timestamp/recipe and defaults monitoring off. Historical
classification and acquisition counts cannot be reconstructed and are omitted,
not copied from today's queue. Sources without a provable completed admission
remain unfinalized and cannot enable monitoring. Existing resources, recipes,
positions and scan policies are preserved. Restart updated API/source services
only after migration; publish staged H5/weapp builds as separate operator actions.

## Public saved-job query

- `GET /api/jobs?page=1&page_size=20&q=教师&include_partial=false`
- `GET /api/jobs/<position_id>?include_partial=false`

These unauthenticated, read-only endpoints perform no acquisition, model calls or
file execution. Search is literal, pagination is bounded, and totals count only
rows surviving all safety gates. The jobs tab includes search, literal filters,
pagination, independently routed native-title details, loading/error/empty states,
and an explicit partial-material option. See `PARSING_FEEDBACK.md` for the later
native-back/filter/feedback changes and their deployment boundary.

Rows require an approved source, active admitted recipe/current toolkit, completed
passing audit, current parent/dependency versions and a non-review recruitment
classification. Pending acquisition, failed/review/cancelled outputs and newer
unadmitted audit results are excluded. Partial records require explicit opt-in and
retain attachment warnings. Detail follows the same filters as the list.

A successful saved admission does not expire merely because the calendar changes.
Fixed notice deadlines and evidence-backed per-position deadlines are checked
against the current **Asia/Shanghai** date without claiming
a fresh crawl. Known expired records are excluded; cutoff-day results instruct users
to verify the exact time. Unknown, long-term and until-filled terms are never promoted
to verified current vacancies. Official URL, original field evidence/locators,
material version, audit evaluation date and last successful source check remain visible.
This is not semantic eligibility matching or a live vacancy verification service.

## Verification boundaries

Focused synthetic tests cover baseline immutability, default-off scheduling,
pause/resume across bounded passes, independent failure, centralized toggle
permissions, in-flight disable, legacy backfill, current material admission,
expiry across days, evidence, literal search and list/detail consistency.

An October 14, 2026 offline projection of a consistent USTC DB **copy** returned
126 complete-material rows and 160 rows with partial-material opt-in. All 160 rows
passed the client response parser across four pages; details were also checked.
These are saved, non-expired-by-observed-date records, **not current vacancy counts**.
Evidence lives in `server/.artifacts/lifecycle-jobs-20261014/`. No production DB
migration, service restart, live model call, H5 publication or WeChat upload was
performed during that initial offline implementation verification. A staged Chromium H5 smoke (synthetic auth, real saved
DB copy, all external requests denied) also verified pagination, partial opt-in,
detail evidence, search/empty reset, independent monitoring navigation,
confirmation cancellation, enable/disable and baseline immutability. It caught
and fixed a Taro dynamic-import API transform issue in the public transport and
a search-row sizing defect; build/type checks alone had not detected them.
WeChat device behavior remains unverified.


## Approved live rollout — October 14, 2026

The user's subsequent continuation authorized migration, service restart, H5
publication and the official WeChat upload. A consistent SQLite backup and the old
H5 tree were preserved under `server/.artifacts/lifecycle-jobs-rollout-20261014/`.
The settled source worker was paused; the explicit additive migration finalized
one historical onboarding result with monitoring disabled. All 327 stored position
rows and 5,106 registry/discovery resources were preserved, not downloaded again.
The latter is the whole registry, not a count of crawled source pages.

API and source companion restarted at 16:39:36 Asia/Shanghai; the independent
Recipe process retained its original PID. All three services remained active with
zero automatic restarts; API/RPC listeners stayed loopback-only. A twelve-second
observation kept the source-audit count at 299, confirming disabled monitoring did
not create new empty audits. Anonymous admin/toggle requests returned 401 and the
public internal RPC route remained 404. This deployment did not enable a source,
submit an Agent task or change centralized permissions.

A production H5 build targets `https://youju-h.942295.xyz`. Its static tree was
atomically exchanged into `client/dist/h5`, with previous files retained and the
existing narrow SELinux content labels restored. No shared Nginx reload or security
relaxation was needed. Public index bytes matched the new build; CORS preflight
passed. The real public Chromium smoke at `https://youjuh5-h.942295.xyz` verified
126 default rows, 160 partial-opt-in rows, detail evidence, pagination and search
empty states, with zero page errors. The requested non-`-h` H5 domain still does
not route to the app; this outer-gateway limitation was not changed.

Official Taro CI uploaded WeChat **0.1.6** at **16:40:37 Asia/Shanghai**, reporting
**420.896484375 KB**. Native incremental route/title and HTTPS API origin were
checked in the compiled package. Receipt/QR are preserved in
`server/.artifacts/wechat-upload-0.1.6-20261014/`. Experience-version selection,
real account login and WeChat device behavior remain unverified; no review or
production-release submission was made.
