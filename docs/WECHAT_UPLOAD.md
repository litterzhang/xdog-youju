# Official WeChat Code Upload

## Commands and private configuration

Preview and upload use official Taro 4.2.1 CLI commands with
`@tarojs/plugin-mini-ci@4.2.1`, not a custom uploader:

```sh
npm --prefix client run preview:weapp  # development preview; only when requested
npm --prefix client run upload:weapp  # upload code; only when requested
```

Configure the app's own `TARO_APP_ID`, `MINIPROGRAM_PRIVATE_KEY_PATH`,
`MINIPROGRAM_VERSION` and `MINIPROGRAM_DESCRIPTION` in ignored
`client/.env.local`. The configuration loads CI-only settings in Node only for
explicit preview/upload flags. The key must be a regular file outside `src/` and
`dist/`, with owner-only permissions. Do not use a frontend-exposed variable for
its contents or path. CI ignores environment, map and key files. Auth machine
secrets, WeChat AppSecret and code-upload private keys serve different purposes.

Normal type checks, tests and builds do not invoke upload or require the CI key.
URL checking remains enabled. WeChat's CI-IP whitelist and HTTPS request-domain
configuration must be managed in the app console, never bypassed to make tests pass.

## SDK compatibility

The first upload built successfully but failed when `miniprogram-ci@1.9.17`
required `less/lib/less`. The installed Less 4.9.1 package layout was incompatible.
A narrow npm override now pins **only miniprogram-ci's Less to 4.2.0**. The SDK's
legacy adapter loads; the Taro compiler's Less 4.9.1 and simulator's Less 3.13.1
remain separate. No SDK source or adjacent project was patched.

## Recorded upload — October 13, 2026

The explicit retry succeeded at **16:51:24 Asia/Shanghai**:

- AppID: `wxc01f9aa6db230b0c`.
- Version: **0.1.0**.
- Description: **Personal mock sample**, preserved from the user's local settings.
- Official CI reported **341.6943359375 KB** uploaded.
- QR image: `client/dist/weapp/upload.png`, also preserved with the bounded receipt
  under `server/.artifacts/wechat-upload-20261013/`.
- TypeScript checks and eight client tests passed. No account login, source fetch,
  model call, review submission or production-release submission was performed.

The QR is the app's generic experience entry. The console may still require setting
this upload as the **experience version**, and adding authorized experience users;
QR generation alone does not prove that 0.1.0 is selected or works on-device.
Real WeChat login and request-domain behavior still require device verification.

Dependency installation reported **131 whole-tree audit findings**, including
49 critical findings, plus deprecated transitive tooling. These are not asserted
to be introduced solely by this plugin or all runtime-exploitable. This task did
not perform a dependency security remediation; no blanket audit-force upgrade or
install-script permission relaxation was used. Review the locked toolchain before
broader distribution. Local upload logs are private and never include key material
in the bounded result report.


## Recorded upload — October 14, 2026

With explicit approval, official Taro CI uploaded **0.1.1** at **09:10:10
Asia/Shanghai**, AppID `wxc01f9aa6db230b0c`, size **371.0576171875 KB**.
Description: `Native tabs, source onboarding and centralized zdog-auth permissions`.
The upload used an explicit version override, not the older default in private
local settings. Use a new version override for the next upload.

This version includes native 首页 / 岗位 / 我的 tabs, six separate admin/onboarding
pages and exact `youju:source:manage` permission checks. Built artifacts were
checked for native tab/page configuration and the public HTTPS API base.
Receipt and QR are preserved under `server/.artifacts/wechat-upload-20261014/`.

The zdog-auth permission/role declaration succeeded and `youju-api.service` was
restarted. Both loopback and public API checks returned health 200, configured
auth status and 401 for anonymous admin access. No user was granted a role.
H5 static files were not replaced. No review or production release was submitted;
experience-version selection and real device login still require verification.


## Account profile upload — October 14, 2026

Official Taro CI uploaded **0.1.2** at **13:18:51 Asia/Shanghai**, size
**371.7587890625 KB**, to AppID `wxc01f9aa6db230b0c`. Description:
`Verified account avatar and username with centralized permissions`.
TypeScript, all eleven client tests and the weapp upload build passed. Bundle
checks confirmed native tabs, account avatar code/shared image template, the
public HTTPS API base and the source-management permission. The explicit version
override avoids reusing the older local-config default. Receipt and QR are
preserved under `server/.artifacts/wechat-upload-0.1.2-20261014/`.

The user reported completing the central grant; this upload did not grant or
independently verify permissions. No review/release was submitted, no H5 files
were replaced and no API restart was performed. The running API predates the
optional profile projection change, so avatar/username fields require an approved
API restart before they are available online. Experience-version selection,
real account profile values and device image-domain behavior remain unverified.


## Nickname/profile mapping correction — October 14, 2026

Official Taro CI uploaded **0.1.3** at **13:49:01 Asia/Shanghai**, size
**372.00390625 KB**. The verified identity response now preserves nickname and
normalizes OIDC `name`, `preferred_username`, `picture`, plus `avatar_url`, into
canonical client profile fields. Nickname takes precedence over username. A failed
image shows 头像暂时无法加载 and retries on the next page visit. Profile data cannot
grant permissions. The API was restarted with these changes; H5 static files
were not replaced. The independent managed Recipe service remains running.

TypeScript, twelve client tests and ten focused profile/admin backend tests passed.
Six synthetic browser cases include standard OIDC picture/name rendering and image
failure fallback. Receipt/QR: `server/.artifacts/wechat-upload-0.1.3-20261014/`.
The real user's upstream profile, selected experience version and device image
loading were not independently verified. No review or production release submitted.


## Blocked-source recovery upload — October 14, 2026

Official Taro CI uploaded **0.1.4** at **14:46:01 Asia/Shanghai**, size
**377.75390625 KB**. This version adds blocker explanations, confirmed saved-material
recovery with bounded budget increases and an audit-driven Agent repair action.
The API was restarted and the live USTC depth increased to 100 without requeueing
its 480 completed slots. Fourteen client tests, TypeScript, the upload build and
synthetic browser cancel/confirm/cap checks passed. The full server suite passed
200 tests with two opt-in skips. Receipt/QR: `server/.artifacts/wechat-upload-0.1.4-20261014/`.
No review/release submitted; actual device behavior and experience selection remain
unverified. Live H5 static files were not replaced.


## Session/navigation upload — October 14, 2026

Official Taro CI uploaded **0.1.5** at **15:26:18 Asia/Shanghai**, size
**384.5556640625 KB**. Same-identity single-flight WeChat relogin and native-safe
left/right arrow PNGs are included. TypeScript, nineteen client tests, H5/weapp
builds and synthetic H5 session/pagination smoke passed. Native WeChat renewal,
device icon rendering and experience selection remain unverified. Expired legacy
token-only sessions require one explicit login after upgrade to bind the subject.
Receipt/QR: `server/.artifacts/wechat-upload-0.1.5-20261014/`. No review/release,
API restart or live H5 replacement was submitted.


## Audited jobs and independent monitoring — October 14, 2026

Official Taro CI uploaded **0.1.6** at **16:40:37 Asia/Shanghai** to AppID
`wxc01f9aa6db230b0c`, reporting **420.896484375 KB**. Description:
`Audited jobs and independent opt-in incremental crawling`. The dedicated upload
key remained outside the bundle and URL/security checks stayed enabled.

This version connects the public 岗位 list/detail/search to saved, admitted material
and separates finalized onboarding from the admin-only 增量抓取 page. Monitoring
defaults off; partial material is an explicit public-view option, not silent admission.
The source DB was backed up/migrated, API/source worker restarted, and live H5
published independently. Public HTTP and real H5 browser checks returned 126 default
records or 160 with partial opt-in; these are not verified current vacancies.
Recipe service was not restarted and no new Agent/model task was submitted.

The preceding implementation verification passed 247 backend tests with two opt-in
skips, 42 client tests, Ruff, TypeScript and staged H5/weapp builds; the final
lifecycle checks also passed. The official upload build succeeded. Receipt and
QR: `server/.artifacts/wechat-upload-0.1.6-20261014/`. Select this upload as the
experience version in the WeChat console if needed; selection and device behavior
were not independently verified. No review/release submitted and no user role changed.


## Native detail, filters and parsing feedback — October 15, 2026

Official Taro CI uploaded **0.1.7** at **08:45:13 Asia/Shanghai**, reporting
**451.318359375 KB**, to AppID `wxc01f9aa6db230b0c`. Description:
`Native back, job filters and parsing feedback`. The compiled package contains
independent native-title job details, three ordinary-user tabs, combined literal
filters/search and the authenticated reporting/admin review path.

The required backend dependency was rolled out before upload: a consistent SQLite
backup was retained, `feedback-migrate` added issue storage, and API/source companion
restarted at 08:42:06. Existing positions, active recipes and the administrator's
incremental switch were preserved. Recipe service retained its PID; no source script
was edited, repair requested, role provisioned or synthetic production issue inserted.
Public filter/detail reads passed; anonymous report/history/review requests returned
401, invalid filters returned 400 and internal Recipe RPC remained publicly 404.

TypeScript, all **62 client tests**, **99 focused backend tests** and Ruff passed.
Official upload compilation passed; native page titles/routes and HTTPS API origin
were checked in the resulting package. These checks are not WeChat-device verification.
H5 static files were **not** replaced for this upload-only follow-up.

Receipt and generic experience QR are in
`server/.artifacts/wechat-upload-0.1.7-20261015/`; DB backup/HTTP checks are in
`server/.artifacts/feedback-rollout-20261015/`. Select 0.1.7 as the experience version
in the WeChat console if required. Selection, real account feedback and device behavior
remain unverified; no review or production-release submission was made.

## Recipe revisions and structured positions — October 15, 2026

Official Taro CI uploaded **0.1.8** at **15:02:03 Asia/Shanghai**, reporting
**459.4365234375 KB**, to AppID `wxc01f9aa6db230b0c`. Description: `Structured positions,
immutable recipe revisions and unified repair`. The package includes position-facts-v2,
active/candidate recipe IDs, bounded repair states and explicit saved-material recovery
for the breaking contract migration. Native tabs/titles and URL checks remain enabled.

The source database was explicitly migrated to a new schema path before upload,
preserving registration and downloaded materials. Old positions/recipes/audits and
Agent history remain archived, not falsely relabelled as new reviewed versions. API,
source worker and Recipe services were restarted; staged H5 was separately published.
Current jobs remain empty until USTC's new recipe generation/full audit is explicitly
resumed. Monitoring is disabled and no model task was launched by deployment.

Receipt, upload log and QR: `server/.artifacts/recipe-rollout-20261015/`.
See `RECIPE_ROLLOUT_20261015.md` for migration counts, HTTP checks and limitations.
No review/release submitted; select 0.1.8 as the experience version if necessary.
Actual device login/navigation and experience selection remain unverified.

## Flat-list UI and incremental results — October 16, 2026

Official Taro CI uploaded **0.1.9** at **11:26:32 Asia/Shanghai**, reporting
**538.30078125 KB**, to AppID `wxc01f9aa6db230b0c`. Description:
`Flat job lists, standalone feedback and incremental notice results`.
The package includes action icons, flat job rows, standalone parsing feedback,
account-ID integration, separate onboarding details and batch-scoped incremental
notices/positions. Both new routes, the HTTPS API origin and enabled URL checks
were checked in the uploaded build.

API/source-worker restart and the verified H5 publication preceded upload; Recipe
retained its original PID. No schema migration was required. Existing 656 stored
positions, source registration, 5,107 resources and the enabled incremental control
were preserved. Stored positions are not a count of currently available vacancies;
the public default list returned 126 records during verification.

The deployment recheck passed TypeScript, 107 client tests, Ruff and five focused
backend tests. Public HTTP checked the new route's anonymous 401, jobs/filter
responses, CORS, static asset hashes and protected/missing-path rejection. A real
anonymous browser against the published H5 rendered 20 job rows, opened a real job
and navigated to its independent feedback page, which required login for submission.
No browser API was mocked and no feedback was submitted.

Receipt, QR, rollback H5/database backup and checks are retained under
`server/.artifacts/ui-rollout-20261016/`. No role change, repair request, WeChat
review or production-release submission was made. Select 0.1.9 as the experience
version in the WeChat console if needed; experience selection, authenticated admin
browser behavior and WeChat-device behavior were not independently verified.

## UI design correction — October 16, 2026

Official Taro CI uploaded **0.1.10** at **11:44:10 Asia/Shanghai**, reporting
**540.4716796875 KB**, to AppID `wxc01f9aa6db230b0c`. Description:
`Refined job and source lists, directional arrows and concise copy`.
Ordinary actions are text-only; previous/next arrows, row disclosures and select
chevrons remain. Jobs and source management share styled contiguous lists with
compact metadata and less repeated guidance.

The verified staged H5 was published with rollback copies and scoped SELinux
labels. Public HTML/JS/CSS matched local files; an asset manifest records the new
build. API, source-worker and Recipe retained their original active PIDs. No
schema migration, shared Nginx change or backend restart was needed.

TypeScript and 109 client tests passed again. A read-only, anonymous real browser
against published H5 verified 20 real job rows, compact fact tags, text-only normal
actions, correctly positioned left/right arrows, details and independent feedback
navigation. No API data was mocked or feedback submitted. Authenticated source
management remains covered by local synthetic browser checks, not a live admin
session. URL checks and HTTPS API origin were retained in the uploaded package.

Receipt, QR, rollback H5, manifests and checks are under
`server/.artifacts/ui-polish-rollout-20261016/`. No WeChat review or production
release was submitted. Select **0.1.10** as the experience version if needed;
experience selection and physical-device behavior remain unverified.

## Source details and admin panels — October 16, 2026

Official Taro CI uploaded **0.1.11** at **13:21:31 Asia/Shanghai**, reporting
**543.96875 KB**, to AppID `wxc01f9aa6db230b0c`. Description:
`Source details, parsing summary and consistent admin pages`.
The uploaded build retains HTTPS API configuration and URL checks; its source
status native title is `来源详情`. The full-width source entry, separate parsing
summary/version panels and consistent onboarding/incremental/feedback pages are
included.

The required read-only parsing-summary API was deployed before upload. API and its
source companion restarted; Recipe retained its PID. No schema migration or Nginx
change was necessary. A consistent database backup was saved; source/resource/
position/run counts and the existing enabled incremental control were preserved.
H5 was deliberately **not** replaced for this upload-only request; its entire file
manifest was checked unchanged.

The upload recheck passed TypeScript, 110 client tests, Ruff and 14 focused backend
tests. Public health/jobs reads passed, the new summary route returned anonymous
401 and internal Recipe paths remained 404. This is not an authenticated live-admin
or WeChat physical-device verification. Earlier synthetic browser evidence remains
in `client/.artifacts/admin-pages-20261016/`.

Receipt, QR, upload log, database backup and checks are in
`server/.artifacts/admin-upload-20261016/`. No WeChat review or production release
was submitted. Select 0.1.11 as the experience version if needed; selection and
physical-device behavior remain unverified.

## Unknown job-field visibility — October 26, 2026

Official Taro mini-ci uploaded **0.1.18** at **15:13:22 Asia/Shanghai**, reporting
**572.9296875 KB**. Unknown job business rows/placeholders are hidden; recognized
zero/inferred facts, qualification logic and separate risk tags are retained.
Type checking, 135 client tests and both builds passed. H5 was separately published
and verified through the working `youjuh5-h.942295.xyz` ingress; the bare H5 hostname
still serves an outer welcome page. No backend service was restarted and no WeChat
review/production release was submitted. See `CLIENT_RELEASE_20261026.md` and the
private receipts under `server/.artifacts/release-20261026-151234/`.
