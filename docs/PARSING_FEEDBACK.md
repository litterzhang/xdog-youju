# Native Job Detail, Filters and Parsing Feedback

## User path

The jobs list combines keyword search with literal education/degree, employment
and material-scope filters. Changing a filter resets pagination without applying
an unsubmitted search draft. Filters inspect saved fields, not inferred eligibility:
`doctorate` means the education field literally mentions 博士, not that a user
qualifies or that a doctoral degree proves doctoral educational attainment.
Unknown fields remain selectable and do not mean no requirements.

Details now use `pages/jobs/detail/index` via Taro `navigateTo`, outside the native
tab bar. WeChat supplies the native back control; no in-page back button or custom
header is rendered. The list refreshes on return while retaining query/filter state.
H5 uses the Taro framework back stack. Native device behavior still needs verification.

A logged-in reader can submit **反馈解析问题** from job details, selecting a field,
describing the discrepancy and optionally supplying a claimed original excerpt.
The excerpt is **unverified user text**, not a trusted source correction. Do not
enter personal identity/contact details or credentials. Anonymous visitors can
still read jobs; login is required only to submit/read their own feedback history.

## Recording and review

- `POST /api/jobs/<id>/feedback`: `field`, `message` (1–1000 characters), optional
  `expected_text` (at most 500). The trusted online subject comes from zdog-auth.
- `GET /api/jobs/<id>/feedback?page=1`: only that subject's reports, including
  historical reports after a job ceases to be publicly current.
- `GET /api/admin/sources/<source_id>/feedback?page=1&status=all`: current online
  `youju:source:manage` permission is required.
- `POST /api/admin/sources/<source_id>/feedback/<id>/review`: explicit
  `reviewed|resolved|dismissed` status and a required nonempty `resolution_note`.

The server derives source, official URL, job title, bundle, recipe and audit IDs
from the current admitted row. Clients cannot assign ownership or replace this
context. Original selected-field/requirements evidence and related requirement
text are snapshotted. Identical reports from the same subject are idempotent;
other subjects cannot retrieve them. Review transitions retain a private actor/
timestamp history; changing source IDs cannot access another source's report.

These are **issue records**, not executable repair instructions. Neither submitting
nor reviewing a report starts an Agent, sends Recipe RPC, downloads a resource,
updates parsed positions or alters a frozen onboarding result. A resolved label is
a human bookkeeping claim, not proof that data was independently revalidated.
An administrator/operator can later use reviewed evidence to plan an explicitly
approved standard Recipe repair; this first feedback path does not auto-forward
untrusted reports to a model or implicitly authorize paid tasks.

Storage is additive. Before deploying these endpoints on an existing database,
back it up and explicitly run from `server/`:

```sh
uv run flask --app youju feedback-migrate --database youju.sqlite3
```

Startup does not create/migrate tables. `parsing_feedback` preserves issue context;
no source script or material is overwritten.

## USTC education diagnosis — October 14, 2026

Read-only inspection found 210 of 327 historical stored positions had an
`education_text` value. Of 126 publicly admitted complete-material records, 22
had this field and 104 did not. Among the 104, **99 explicitly mention 博士学位 in
`requirements_text`, and none explicitly use 学历 in that requirement text**.
The remaining five must not be assigned a guessed qualification.

The generated source config maps 招聘条件 to `requirements_text`. Its table path
returns the shared literal-table extraction immediately; no rule extracts a
separate education/degree span from that qualification cell. A separate prose
fallback extracts education only for its supported one-role teaching template.
The captured requirements and original cell locators are intact: this is not an
attachment/download loss or a frontend field dropped in transit. The UI's old
学历：待确认 wording concealed the distinction between a missing separate field
and an already captured degree requirement.

No Agent-owned script was hand-edited and no Recipe build/repair was submitted
for this diagnosis. Degree text must not be converted into an invented education
level. The new feedback path lets readers surface the discrepancy and administrators
review it before deciding what schema/parser change is appropriate. Read-only
measurements and source evidence live in
`server/.artifacts/job-feedback-filters-20261014/education-diagnosis.json`.


## Verification and rollout boundary

The combined follow-up passed **302 backend tests** with two explicitly opt-in
sandbox checks skipped, **62 client tests**, Ruff and TypeScript. Staged H5 and
weapp builds succeeded (H5 retains the existing entrypoint-size warning); compiled
native detail/feedback routes and page titles were checked. A real Chromium smoke
against a copied USTC DB and synthetic auth exercised the filter wheel, native H5
back, anonymous reading, authenticated reporting and permission-gated source review.
The original position and frozen onboarding stayed unchanged; no Recipe call or
page error occurred. Evidence is under `server/.artifacts/job-feedback-filters-20261014/`.

At the initial October 14 verification, these native-back/filter/feedback follow-ups
**had not been deployed or uploaded**.
The already uploaded 0.1.6 and published H5 predate them. A next approved rollout
must back up/migrate feedback storage, restart the updated API, publish the new H5
build and upload a new WeChat version. The existing production incremental control
was observed changed during follow-up development and was deliberately not overwritten.
Real account and WeChat-device behavior remain unverified.


## Approved WeChat/API rollout — October 15, 2026

The subsequent upload request deployed the required additive feedback table and
updated API/source companion, then officially uploaded WeChat **0.1.7** at
**08:45:13 Asia/Shanghai**. Public literal-filter/detail reads and authentication
boundaries passed. The existing incremental switch and active recipe stayed unchanged;
Recipe service was not restarted and no repair was requested. This was an upload-only
follow-up: live H5 remains the earlier 0.1.6 static tree. See `WECHAT_UPLOAD.md` for
receipt, backup paths, package checks and the still-unverified experience/device boundary.
