# First-Version Authentication and Source Administration

## Scope

The Flask API and shared Taro client now implement login, a permission-gated admin
entry, region/organization/source registration, explicit official-source approval,
onboarding submission, and status/summary inspection. UI text is Chinese; the
implementation and this guide are English. A later public saved-job query is now
accessible through the 岗位 tab, separate from these admin APIs. Eligibility matching,
favorites and notifications remain unimplemented; see `LIFECYCLE_AND_JOBS.md`.

The client hides the admin entry unless a fresh `/api/auth/me` response contains
`youju:source:manage`. Direct navigation is checked again. **Every admin API independently
validates the bearer token and application permission**; hiding a button is not
authorization. Tokens are removed locally on a 401 or device logout. Device logout
does not claim to revoke the upstream zdog-auth session.

## Dedicated zdog-auth integration

This follows the actual machine API contract in the local `zdog-xiaoman` project:

- Password login: `/machine/password/login`.
- WeChat code exchange: `/machine/wechat/miniprogram`.
- Online identity validation: `/machine/userinfo`, requiring a valid `sub`.
- Machine credentials are added only by the server. Redirects are not followed;
  errors and bounded responses never expose credentials or provider payloads.

Provision a **separate youju machine client**. No Xiaoman credential, account, config
file, or running process was copied or modified. Following zdog-mp, authorization
comes exclusively from the validated zdog-auth `userinfo.permissions` array.
The application permission is `youju:source:manage`, declared under role
`youju-admin`; permissions are namespaced to avoid cross-application collisions.
Missing or malformed permissions deny management access. Role labels, wildcards,
client flags and the legacy `YOUJU_ADMIN_SUBJECTS` setting grant nothing.

Run the explicit command `uv run flask --app youju auth-provision` after loading
server credentials and obtaining approval to modify the shared auth service. It
POSTs permissions/roles to `/machine/provision`, following zdog-mp's additive,
idempotent declaration contract. It does **not** assign roles to any user. Grant
or revoke `youju-admin` / `youju:source:manage` centrally in zdog-auth. No self-grant
endpoint exists. Normal app startup never provisions or contacts the auth service.

Each authenticated request validates userinfo online once, without cross-request
permission caching. A central revocation therefore takes effect on the next request
once zdog-auth returns the updated grants, without restarting Youju. `/api/auth/me`
returns only Youju's recognized permissions; unrelated central grants are omitted.
An auth outage fails closed with a service error, not an administrator fallback.

## Local startup

From `server/`:

```sh
uv sync --frozen
cp .env.example .env
# Edit the dedicated issuer/client credentials locally.
set -a
. ./.env
set +a
uv run flask --app youju init-db --database "$YOUJU_DATABASE"
uv run flask --app youju run --host 127.0.0.1 --port 8089
```

`create_app` and `/api/health` do not create databases or fetch resources. There is
no auth/demo bypass. The explicit init command creates missing tables, not a schema
migration. Paths are server-owned configuration, never HTTP inputs. An unconfigured
issuer or missing database returns a clear 503. Existing POC material can be viewed
by pointing `YOUJU_DATABASE` and `YOUJU_SOURCE_STORAGE` to its matching DB/blob pair;
never point the product at an isolated diagnostic replay as if it were activated.

From `client/`, run `npm ci` then `npm run dev:h5`. The default same-origin `/api`
proxy targets port 8089. For a separate host, set the public
`TARO_APP_API_BASE_URL` and rebuild; no secrets belong in client variables. Login and
admin requests require HTTPS except explicit loopback HTTP development. Configure
`YOUJU_ALLOWED_ORIGINS` and production `YOUJU_TRUSTED_HOSTS` explicitly. CORS only
permits reviewed origins and GET/POST with the required headers. Login attempts have
a bounded per-process limiter; multi-worker/public deployment needs edge limits.

## Admin workflow

1. Sign in with an authorized identity and open **管理组织与来源**.
2. Register region codes/names as needed, then the organization and its registrable
   root domain (for example `ustc.edu.cn`). Organization location and source region
   tags are separate; neither establishes a position's location or eligibility.
3. Register a **candidate** source with an HTTPS entry URL under that root. Optional
   seed URLs must share the source origin. The entry itself is always a discovery
   seed, even with no additional seeds. Duplicate/conflicting URLs need review.
4. Supply a concrete, manually checked official-publisher basis and explicitly
   confirm approval. A domain or repost alone is not official-source evidence.
5. Start onboarding. The API returns **202** after persisting the pipeline and
   acquisition queue; it performs no crawling, recipe generation or model work.
6. Inspect stages, queue counts, classification coverage (not accuracy), audit
   progress, complete/partial positions, expiry/declared-period counts, review/errors and material
   warnings. State refreshes every 15 seconds while the admin page is visible, or
   manually on request. Audit evaluation dates and administrative source-verification
   dates remain separate from page capture times. Historical rows are not open jobs.

Registry/onboarding mutations share the existing source-CLI filesystem lock.
The independent incremental switch is the exception, so disabling can persist
while a bounded acquisition pass runs. An active source pass
returns `409 SOURCE_BUSY` rather than racing registry writes. Status reads remain
available. GET projections omit full resource arrays, workflow contents, workspace
paths and free-form author/provider text. There is no arbitrary URL fetch endpoint,
attachment-policy bypass, direct recipe editing, automatic
promotion of diagnostics. Existing DNS/IP/origin/redirect and sandbox checks remain.

## Legacy CLI compatibility workers

Production web onboarding now uses enabled managed daemons and Recipe RPC; see
`MANAGED_DEPLOYMENT.md`. The following commands describe the retained explicit
legacy/foreground path, not a required user action in the deployed application.

`source-worker` discovers approved, started, nonblocked pipelines in the same DB and
advances one bounded pass per source. `--watch` repeats; admitted sources use the
incremental scheduler only after explicit enablement (default off). Blocked sources are shown but not silently retried.
No process/service is started by Flask or by an admin request.

For acquisition and rule-only inspection, a one-pass command needs no model opt-in:

```sh
uv run flask --app youju source-worker \
  --database "$YOUJU_DATABASE" --storage "$YOUJU_SOURCE_STORAGE" --limit 50
```

Load the server environment in each worker terminal. For the complete existing
source pipeline, use an explicitly configured source
worker and a separate recipe worker. These commands **allow paid model calls**;
they are operator actions, never default tests or browser-triggered author Agents.
Keep classifier/provider settings stable for a source's recorded profile. A worker
with a different classifier profile skips that source with
CLASSIFIER_PROFILE_MISMATCH rather than silently reclassifying its saved inventory.

```sh
# Terminal A: onboarding, auditing and due incrementals.
uv run flask --app youju source-worker --watch \
  --database "$YOUJU_DATABASE" --storage "$YOUJU_SOURCE_STORAGE" \
  --enable-models --provider "$YOUJU_PROVIDER" \
  --small-model "$YOUJU_SMALL_MODEL" --large-model "$YOUJU_LARGE_MODEL" \
  --author-model gpt-6-astra --local-classifier /path/to/reviewed-model.json

# Terminal B: source-script jobs submitted by the existing pipeline.
uv run flask --app youju recipe-worker --watch \
  --storage "$YOUJU_SOURCE_STORAGE" --enable-models \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model gpt-6-astra
```

The compatibility CLI retains its **existing explicit legacy RecipeService job path**.
The native managed control plane is not silently substituted or auto-migrated by
this UI. Script generation/repair remains xdog Agent-owned; activation still requires
the existing full-source audit and quarantine policy. Without model opt-in, authoring
can block explicitly at MODEL_DISABLED rather than invent a working recipe. Operator
CLI resume/repair/recovery commands remain available as documented separately.

## Verification and remaining boundaries

Default tests use a synthetic issuer transport and public-domain-shaped synthetic
registry data, with no real credential, external callback, download or paid model.
Focused tests cover the machine contracts, no-permission/expired-token denial,
registration/approval/idempotent queueing, scope/field injection, lock contention,
status projection and foreground worker dispatch. Client contract tests preserve
partial, unknown and failure states. Run:

```sh
# server/
uv run --frozen pytest -q
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
# client/
npm run typecheck
npm test
npm run build:h5
npm run build:weapp
```

Real zdog-auth account/WeChat login requires dedicated provisioning. The mini-program
still uses `touristappid` with URL checks enabled; supply the real AppID and approved
HTTPS request domain and verify on-device before claiming it is ready. The approved API/static-H5 deployment is recorded in `NGINX_DEPLOYMENT.md`.
No unattended crawler/model worker has been started. A later explicitly approved
WeChat code upload is recorded in `WECHAT_UPLOAD.md`; real device login remains
unverified.

### Recorded local verification (October 13, 2026)

- Server suite: 184 passed, two explicitly opt-in Linux author-sandbox checks
  skipped. Focused auth/admin/worker tests also passed after the final changes.
- Client contract suite: eight passed; TypeScript checking passed.
- H5 and weapp compiled through the official Taro CLI. H5 reports an entrypoint
  size warning (approximately 286 KiB versus the 244 KiB recommended limit);
  the warning was not disabled or represented as a measured performance result.
- A sandbox-enabled headless Chromium replay used the built H5 and actual Flask
  routes (browser-request routing to the local test client), with only the external
  issuer mocked. It registered a region, organization and source, confirmed official
  approval and queued onboarding. A normal-user login had no admin entry and direct
  navigation to admin was denied. There were no browser page errors, crawler
  downloads or live model calls. This is not a real zdog-auth or WeChat login test.
- A read-only API check projected the existing USTC source's recorded audit and
  classifier metrics without fetching pages or changing its recipe/lifecycle.

Browser screenshots and verification records are stored locally under
`server/.artifacts/admin-v1-browser-20261013/`; attempts retain their separate
synthetic databases. The root AGENTS.md was not modified.

## Production service

`uv run --frozen python -m youju.production` is the explicit Waitress entry point.
It loads the local server .env without overriding operator environment variables and
requires an initialized DB, configured auth client and explicit trusted hosts. The
installed `youju-api.service` runs this module as zhang on loopback port 8089.
Public ingress checks and the non-`-h` H5 routing limitation are documented in
`NGINX_DEPLOYMENT.md`; configuration-loaded status is not real-login verification.

## Updated client flow

The newer UI moves the privileged entry to 我的 and splits the combined form into
separate organization/source/review/status pages. See `CLIENT_UI_V2.md`; authentication
and server permission contracts remain unchanged. The original one-page description
above records the initial version, not the current client layout.


## Central permission verification — October 14, 2026

Authorization now follows zdog-mp's centralized permission contract. Changes cover
`api/auth.py`, the Flask configuration, the explicit provisioning CLI, client
permission checks, focused tests and configuration/deployment documentation.
No local subject allowlist is consulted. Synthetic tests verify central grant and
same-token revocation, malformed/missing permission denial, unrelated-role and
legacy-allowlist rejection, provider outage denial and declaration without grants.

Verification: Ruff passed; the full server suite passed 189 tests with two opt-in
sandbox tests skipped; TypeScript and all ten client tests passed. H5 and weapp
builds passed into `server/.artifacts/central-auth-20261014/`, not the live static
root. H5 retains a bundle-size warning. No real auth provisioning, user grant,
service restart, public deployment, WeChat upload or device verification occurred.


With subsequent explicit approval on October 14, 2026, real permission declaration
succeeded and the API was restarted. Public/loopback health, configured auth and
anonymous-admin denial passed. WeChat 0.1.1 was uploaded through official Taro CI;
see `docs/WECHAT_UPLOAD.md`. No user grants, review/release, H5 replacement or real
logged-in/device verification was performed.


## Production managed rollout

On October 14, 2026, the API companion source daemon and independent Recipe
service were installed/enabled. Web-submitted approved sources now automatically
advance through the managed RPC/init/build/callback/audit path, with no foreground
terminal requirement. See `MANAGED_DEPLOYMENT.md` for settings, recovery, explicit
model opt-in and verification limits. The product DB was backed up, not seeded.


Blocked status now offers server-validated recovery or audit-driven Agent repair
with explicit confirmation. See `MANAGED_DEPLOYMENT.md`; old foreground-only
recovery limitations above are superseded by the deployed admin endpoints.


## Finalized admission and public jobs

Onboarding no longer includes recurring monitoring. Its completed baseline stays
frozen; source status links to a separate 增量抓取 page with centrally authorized
enable/disable controls and independent run summaries. The public 岗位 tab now
queries audited saved jobs and their evidence, with explicit partial-material
opt-in. Existing databases require the additive lifecycle migration before updated
services restart. See `LIFECYCLE_AND_JOBS.md` for contracts and rollout boundaries.
